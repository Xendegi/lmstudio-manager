#!/usr/bin/env python3
"""
Agentic Benchmark Runner
Two-agent adversarial benchmark with ground truth verification.

Agent A (testee) solves problems.
Agent B (evaluator) independently verifies solutions.
External scoring compares against ground truth.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Clear proxy env vars that interfere with direct HTTP calls
for var in list(os.environ.keys()):
    if "proxy" in var.lower() or "PROXY" in var:
        del os.environ[var]

import httpx
import yaml

# Configuration
LMSTUDIO_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://192.168.2.100:1234")
LMSTUDIO_KEY = os.environ.get("LMSTUDIO_API_KEY", "sk-lm-rTgbTGyG:DGJ5nhlDtjok9UFuDtMx")
MODEL_NAME = os.environ.get("BENCHMARK_MODEL", "qwen/qwen3.5-9b")
API_ENDPOINTS = [
    f"{LMSTUDIO_URL}/v1/chat/completions",
    f"{LMSTUDIO_URL}/api/v1/chat/completions",
]

BENCHMARK_DIR = Path(__file__).parent
TESTS_DIR = BENCHMARK_DIR / "tests"
AGENTS_DIR = BENCHMARK_DIR / "agents"
REPORTS_DIR = BENCHMARK_DIR / "reports"

REPORTS_DIR.mkdir(exist_ok=True)


def load_prompt(name: str) -> str:
    """Load an agent prompt file."""
    path = AGENTS_DIR / f"{name}.md"
    return path.read_text()


def load_tests(category: str) -> list[dict]:
    """Load test cases for a category."""
    path = TESTS_DIR / f"{category}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("tests", [])


def call_llm(system_prompt: str, user_message: str, max_tokens: int = 2048) -> str:
    """Call the LLM via LM Studio API."""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "reasoning": "off",
    }
    headers = {
        "Authorization": f"Bearer {LMSTUDIO_KEY}",
        "Content-Type": "application/json",
    }

    last_error = None
    for endpoint in API_ENDPOINTS:
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(endpoint, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return content if content else ""
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"All API endpoints failed. Last error: {last_error}")


def extract_final_answer(response: str) -> str | None:
    """Extract FINAL_ANSWER from Agent A's response. Returns None if not found."""
    # Try explicit FINAL_ANSWER marker (most reliable)
    match = re.search(r"FINAL_ANSWER:\s*(.+?)(?:\n|$)", response, re.IGNORECASE)
    if match:
        answer = match.group(1).strip()
        # Clean up code block markers
        answer = answer.strip("`").strip()
        answer = re.sub(r"^python\s*", "", answer)
        answer = answer.strip()
        # Skip if it looks like code (contains def, class, import, print, etc.)
        if any(kw in answer for kw in ["def ", "class ", "import ", "print(", "for ", "if "]):
            return None
        return answer
    
    # Try "The answer is" or similar patterns
    match = re.search(r"(?:the answer is|answer:|result:|solution:)\s*(.+?)(?:\n|$)", response, re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip("`").strip()
    
    return None


def force_final_answer(response: str, problem: str, agent_a_prompt: str) -> str:
    """If FINAL_ANSWER not found, re-ask the model for just the answer."""
    extraction_prompt = (
        "You previously attempted to solve a problem but did not provide a final answer "
        "in the required format. Based on your work below, provide ONLY the final answer "
        "on a single line starting with FINAL_ANSWER:\n\n"
        f"YOUR PREVIOUS RESPONSE:\n{response[:2000]}\n\n"
        "Now provide just the final answer:"
    )
    try:
        retry_response = call_llm(agent_a_prompt, extraction_prompt, max_tokens=256)
        return extract_final_answer(retry_response) or retry_response.strip().split("\n")[-1].strip()
    except:
        return ""


def run_system_command(command: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a shell command and return (success, output)."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def evaluate_outcomes(outcomes: list[dict]) -> list[dict]:
    """Evaluate system admin test outcomes."""
    results = []
    for outcome in outcomes:
        otype = outcome["type"]
        if otype == "file_exists":
            exists = Path(outcome["path"]).exists()
            results.append({"check": "file_exists", "path": outcome["path"], "passed": exists})
        elif otype == "directory_exists":
            exists = Path(outcome["path"]).is_dir()
            results.append({"check": "directory_exists", "path": outcome["path"], "passed": exists})
        elif otype == "file_content":
            try:
                content = Path(outcome["path"]).read_text()
                passed = outcome.get("contains", "") in content
            except:
                passed = False
            results.append({"check": "file_content", "path": outcome["path"], "passed": passed})
        elif otype == "file_line_count":
            try:
                lines = Path(outcome["path"]).read_text().splitlines()
                passed = len(lines) >= outcome.get("min_lines", 1)
            except:
                passed = False
            results.append({"check": "file_line_count", "path": outcome["path"], "passed": passed})
        elif otype == "command_output":
            ok, output = run_system_command(outcome["command"])
            passed = ok
            if "contains" in outcome:
                passed = passed and outcome["contains"] in output
            if "also_contains" in outcome:
                passed = passed and outcome["also_contains"] in output
            if "is_valid_json" in outcome and outcome["is_valid_json"]:
                try:
                    json.loads(output)
                except:
                    passed = False
            if "min_value" in outcome:
                try:
                    num = int(re.search(r"\d+", output).group())
                    passed = passed and num >= outcome["min_value"]
                except:
                    passed = False
            results.append({"check": "command_output", "command": outcome["command"], "passed": passed, "output": output[:500]})
        elif otype == "output_contains":
            passed = outcome.get("pattern", "") in str(outcome.get("output", ""))
            results.append({"check": "output_contains", "passed": passed})
    return results


def grade_numeric_answer(answer: str, expected: str, tolerance: float = 0.0) -> bool:
    """Check if a numeric answer matches."""
    try:
        # Extract numbers from both
        ans_nums = re.findall(r"-?[\d,]+\.?\d*", answer.replace(",", ""))
        exp_nums = re.findall(r"-?[\d,]+\.?\d*", expected.replace(",", ""))
        if not ans_nums or not exp_nums:
            return False
        
        # For single number answers, check if any answer number matches
        if len(exp_nums) == 1:
            for an in ans_nums:
                try:
                    if abs(float(an) - float(exp_nums[0])) <= tolerance:
                        return True
                except:
                    continue
            return False
        
        # For multi-number answers, ALL expected numbers must appear in answer
        exp_matched = set()
        for en in exp_nums:
            found = False
            for an in ans_nums:
                try:
                    if abs(float(an) - float(en)) <= tolerance:
                        exp_matched.add(en)
                        found = True
                        break
                except:
                    continue
            if not found:
                return False
        
        # Check order - expected numbers should appear in same order as in answer
        # Find indices of matched numbers in answer
        ans_indices = []
        for en in exp_nums:
            for i, an in enumerate(ans_nums):
                try:
                    if abs(float(an) - float(en)) <= tolerance and i not in ans_indices:
                        ans_indices.append(i)
                        break
                except:
                    continue
        
        # Verify indices are in ascending order (same order as expected)
        return ans_indices == sorted(ans_indices)
    except:
        return False


def grade_exact_match(answer: str, expected: str, ignore_case: bool = False) -> bool:
    """Check exact string match, with flexible prefix handling."""
    # Strip common prefixes like "The answer is" or "x ="
    ans_clean = re.sub(r"^(the answer is|answer|result|final answer)[:\s]*", "", answer.strip(), flags=re.IGNORECASE)
    exp_clean = re.sub(r"^(the answer is|answer|result|final answer)[:\s]*", "", expected.strip(), flags=re.IGNORECASE)
    
    # Also try to extract just the core value
    ans_core = ans_clean.split("\n")[0].strip()
    exp_core = exp_clean.split("\n")[0].strip()
    
    # Normalize brackets/whitespace for comparison
    def normalize(s):
        s = s.strip().strip("[]").strip()
        s = re.sub(r"\s+", " ", s)
        return s
    
    if ignore_case:
        return normalize(ans_core).lower() == normalize(exp_core).lower()
    return normalize(ans_core) == normalize(exp_core)


def grade_answer(test: dict, agent_a_response: str, agent_b_response: str, agent_a_prompt: str) -> dict:
    """Grade a test based on ground truth and Agent B's evaluation."""
    final_answer = extract_final_answer(agent_a_response)
    
    # If FINAL_ANSWER not found, retry extraction
    if final_answer is None:
        final_answer = force_final_answer(agent_a_response, test["problem"], agent_a_prompt)
    
    eval_type = test.get("evaluation_type", "exact_match")
    expected = test.get("expected_answer", "")

    # Extract Agent B's independent answer if available
    b_answer_match = re.search(r"MY_ANSWER:\s*(.+?)(?:\n|$)", agent_b_response, re.IGNORECASE)
    agent_b_independent_answer = b_answer_match.group(1).strip() if b_answer_match else ""

    # Ground truth scoring - only if expected answer exists
    ground_truth_pass = None
    if expected:
        if eval_type in ("numeric_match",):
            tolerance = test.get("tolerance", 0.0)
            ground_truth_pass = grade_numeric_answer(final_answer, expected, tolerance)
        elif eval_type in ("exact_match",):
            ground_truth_pass = grade_exact_match(final_answer, expected)
        elif eval_type in ("exact_match_ignore_case",):
            ground_truth_pass = grade_exact_match(final_answer, expected, ignore_case=True)
        elif eval_type == "code_output":
            ground_truth_pass = grade_exact_match(final_answer, expected)
        elif eval_type == "custom":
            pass
        elif eval_type == "procedure_check":
            pass
    elif eval_type == "procedure_check":
        pass  # Need Agent B for this

    # Check expected_outcomes for system admin tests (only if external_verify is set)
    outcomes_pass = None
    if "expected_outcomes" in test and test.get("external_verify", False):
        outcome_results = evaluate_outcomes(test["expected_outcomes"])
        if outcome_results:
            all_passed = all(r.get("passed", False) for r in outcome_results)
            outcomes_pass = all_passed

    # Parse Agent B's verdict
    agent_b_correct = None
    b_match = re.search(r"VERDICT:\s*(CORRECT|INCORRECT)", agent_b_response, re.IGNORECASE)
    if b_match:
        agent_b_correct = b_match.group(1).upper() == "CORRECT"
    else:
        # Fallback: check for CORRECT/INCORRECT anywhere in the response
        if re.search(r"\bCORRECT\b", agent_b_response, re.IGNORECASE) and not re.search(r"\bINCORRECT\b", agent_b_response, re.IGNORECASE):
            agent_b_correct = True
        elif re.search(r"\bINCORRECT\b", agent_b_response, re.IGNORECASE):
            agent_b_correct = False

    # Final score: ground truth / outcomes take priority if available
    if ground_truth_pass is not None:
        score = ground_truth_pass
    elif outcomes_pass is not None:
        score = outcomes_pass
    elif agent_b_correct is not None:
        score = agent_b_correct
    else:
        score = False

    return {
        "test_id": test["id"],
        "difficulty": test.get("difficulty", "unknown"),
        "points_possible": test.get("points", 10),
        "points_earned": test.get("points", 10) if score else 0,
        "passed": score,
        "agent_a_answer": final_answer,
        "expected_answer": expected,
        "agent_b_verdict": "CORRECT" if agent_b_correct else ("INCORRECT" if agent_b_correct is not None else "UNKNOWN"),
        "agent_b_independent_answer": agent_b_independent_answer,
        "ground_truth_pass": ground_truth_pass,
    }


def run_benchmark(categories: list[str] | None = None) -> dict:
    """Run the full benchmark suite."""
    if categories is None:
        categories = ["math_logic", "code_execution", "system_admin"]

    agent_a_prompt = load_prompt("agent_a_prompt")
    agent_b_prompt = load_prompt("agent_b_prompt")

    results = {
        "benchmark_name": "Agentic Adversarial Benchmark",
        "timestamp": datetime.now().isoformat(),
        "model": MODEL_NAME,
        "categories": {},
        "summary": {},
    }

    total_points = 0
    earned_points = 0
    total_tests = 0
    passed_tests = 0

    for category in categories:
        print(f"\n{'='*60}")
        print(f"  CATEGORY: {category.upper().replace('_', ' ')}")
        print(f"{'='*60}")

        tests = load_tests(category)
        cat_results = {"tests": [], "total": 0, "passed": 0, "points_possible": 0, "points_earned": 0}

        for test in tests:
            print(f"\n  [{test['id']}] {test.get('difficulty', '?').upper()}")
            print(f"  Problem: {test['problem'][:100]}...")

            # Agent A solves the problem
            print(f"    -> Agent A solving...", end=" ", flush=True)
            try:
                agent_a_response = call_llm(agent_a_prompt, test["problem"])
                print(f"done ({len(agent_a_response)} chars)")
            except Exception as e:
                print(f"FAILED: {e}")
                agent_a_response = f"ERROR: {e}"

            # Agent B evaluates (sees problem + solution, not reasoning)
            eval_input = f"PROBLEM:\n{test['problem']}\n\nPROPOSED SOLUTION:\n{agent_a_response}"
            print(f"    -> Agent B verifying...", end=" ", flush=True)
            try:
                agent_b_response = call_llm(agent_b_prompt, eval_input)
                print(f"done ({len(agent_b_response)} chars)")
            except Exception as e:
                print(f"FAILED: {e}")
                agent_b_response = f"VERDICT: INCORRECT\nREASONING: Evaluation failed: {e}"

            # Grade the test
            grade = grade_answer(test, agent_a_response, agent_b_response, agent_a_prompt)
            cat_results["tests"].append(grade)
            cat_results["total"] += 1
            cat_results["points_possible"] += grade["points_possible"]
            cat_results["points_earned"] += grade["points_earned"]
            total_tests += 1
            total_points += grade["points_possible"]
            earned_points += grade["points_earned"]

            status = "PASS" if grade["passed"] else "FAIL"
            print(f"    -> Result: {status} ({grade['points_earned']}/{grade['points_possible']} pts)")
            print(f"       Expected: {grade['expected_answer'][:60]}")
            print(f"       Got:      {grade['agent_a_answer'][:60]}")
            print(f"       Agent B:  {grade['agent_b_verdict']}")

            if grade["passed"]:
                cat_results["passed"] += 1
                passed_tests += 1

        results["categories"][category] = cat_results
        print(f"\n  Category Total: {cat_results['passed']}/{cat_results['total']} "
              f"({cat_results['points_earned']}/{cat_results['points_possible']} pts)")

    # Summary
    results["summary"] = {
        "total_tests": total_tests,
        "passed_tests": passed_tests,
        "total_points": total_points,
        "earned_points": earned_points,
        "score_percent": round((earned_points / total_points * 100) if total_points > 0 else 0, 1),
    }

    return results


def save_report(results: dict) -> str:
    """Save benchmark results and return the path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"benchmark_{timestamp}.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    # Also save a human-readable markdown report
    md_path = REPORTS_DIR / f"benchmark_{timestamp}.md"
    with open(md_path, "w") as f:
        f.write(f"# Agentic Benchmark Report\n\n")
        f.write(f"**Date:** {results['timestamp']}\n")
        f.write(f"**Model:** {results['model']}\n\n")

        summary = results["summary"]
        f.write(f"## Summary\n\n")
        f.write(f"| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Tests Passed | {summary['passed_tests']}/{summary['total_tests']} |\n")
        f.write(f"| Score | {summary['earned_points']}/{summary['total_points']} pts ({summary['score_percent']}%) |\n\n")

        for cat_name, cat_data in results["categories"].items():
            f.write(f"## {cat_name.replace('_', ' ').title()}\n\n")
            f.write(f"| Test | Difficulty | Status | Points | Agent B |\n")
            f.write(f"|------|------------|--------|--------|---------|\n")
            for t in cat_data["tests"]:
                status = "PASS" if t["passed"] else "FAIL"
                f.write(f"| {t['test_id']} | {t['difficulty']} | {status} | {t['points_earned']}/{t['points_possible']} | {t['agent_b_verdict']} |\n")
            f.write(f"\n")

    print(f"\nReports saved:")
    print(f"  JSON: {report_path}")
    print(f"  MD:   {md_path}")
    return str(report_path)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Agentic Benchmark Runner")
    parser.add_argument("--categories", nargs="+", default=None,
                        help="Categories to run (default: all)")
    parser.add_argument("--model", default=None,
                        help="Model to use (default: qwen/qwen3.5-9b)")
    parser.add_argument("--lmstudio-url", default=None,
                        help="LM Studio URL (default: http://192.168.2.100:1234)")
    args = parser.parse_args()

    global LMSTUDIO_URL, MODEL_NAME
    if args.lmstudio_url:
        LMSTUDIO_URL = args.lmstudio_url
    if args.model:
        MODEL_NAME = args.model

    print("=" * 60)
    print("  AGENTIC ADVERSARIAL BENCHMARK")
    print("  Agent A (testee) + Agent B (evaluator) + Ground Truth")
    print("=" * 60)
    print(f"  Model: {MODEL_NAME}")
    print(f"  LM Studio: {LMSTUDIO_URL}")
    print(f"  Time: {datetime.now().isoformat()}")

    results = run_benchmark(args.categories)
    report_path = save_report(results)

    summary = results["summary"]
    print(f"\n{'='*60}")
    print(f"  FINAL RESULTS")
    print(f"{'='*60}")
    print(f"  Score: {summary['earned_points']}/{summary['total_points']} ({summary['score_percent']}%)")
    print(f"  Tests: {summary['passed_tests']}/{summary['total_tests']} passed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
