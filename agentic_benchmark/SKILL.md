# Agentic Benchmark Skill

Two-agent adversarial benchmark for evaluating agentic capabilities.

## How It Works

1. **Agent A (testee)** solves problems using available tools
2. **Agent B (evaluator)** independently verifies solutions (same model, different prompt)
3. **Ground truth** comparison provides objective scoring

## Running the Benchmark

### Run All Categories
```bash
cd ~/.hermes/benchmarks/agentic_v2 && python3 run_benchmark.py
```

### Run Specific Categories
```bash
cd ~/.hermes/benchmarks/agentic_v2 && python3 run_benchmark.py --categories math_logic code_execution
```

### Run with Different Model
```bash
cd ~/.hermes/benchmarks/agentic_v2 && python3 run_benchmark.py --model unsloth/qwen3.5-4b
```

## Test Categories

| Category | Tests | Description |
|----------|-------|-------------|
| `math_logic` | 9 | Math problems, logic puzzles, constraint satisfaction |
| `code_execution` | 9 | Write and run code, verify output |
| `system_admin` | 9 | Linux system tasks with verifiable outcomes |

## Scoring

- Each test has a point value (10/20/30 based on difficulty)
- Ground truth takes priority when available
- Agent B verification used for subjective/complex tests
- Final score: earned_points / total_points * 100

## Output

Reports saved to `~/.hermes/benchmarks/agentic_v2/reports/`:
- `benchmark_YYYYMMDD_HHMMSS.json` — raw results
- `benchmark_YYYYMMDD_HHMMSS.md` — human-readable report

## Architecture

```
agentic_v2/
├── run_benchmark.py          # Main orchestration
├── tests/
│   ├── math_logic.yaml       # Math/logic test cases
│   ├── code_execution.yaml   # Code execution tests
│   └── system_admin.yaml     # System admin tests
├── agents/
│   ├── agent_a_prompt.md     # Testee system prompt
│   └── agent_b_prompt.md     # Evaluator system prompt
└── reports/                  # Generated reports
```

## When to Use

- After making significant changes to Hermes configuration
- When testing new models for agentic capability
- Periodically to track capability regression
- Before deploying to production workflows
