# The Auditor

**AI-assisted software test-quality auditing platform** that evaluates whether a project's existing tests can detect defects in its code — powered by mutation testing and IBM Granite foundation models.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI REST API                      │
│  POST /audit  ·  POST /audit/upload  ·  GET /health     │
└────────────┬────────────────────────────┬───────────────┘
             │                            │
    ┌────────▼────────┐          ┌────────▼────────┐
    │  Audit Engine   │          │ Granite Service  │
    │  ┌────────────┐ │          │  (watsonx.ai)    │
    │  │AST Mutator │ │◄────────►│  • AI mutations  │
    │  └────────────┘ │          │  • Analysis       │
    └────────┬────────┘          └─────────────────┘
             │
    ┌────────▼────────┐
    │    Sandbox       │
    │  subprocess │    │
    │    docker   │    │
    └─────────────────┘
```

## Quick Start

### 1. Clone & install

```bash
cd the-auditor
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env
# Edit .env with your IBM watsonx.ai credentials
```

### 3. Run the server

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Run tests

```bash
pytest tests/ -v
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check + watsonx status |
| `POST` | `/audit` | Submit inline source & test files |
| `POST` | `/audit/upload` | Upload a ZIP project archive |
| `GET` | `/audit/{id}` | Retrieve full audit results |
| `GET` | `/audit/{id}/status` | Lightweight status poll |
| `GET` | `/audits` | List all audit jobs |

### Example: Inline Audit

```bash
curl -X POST http://localhost:8000/audit \
  -H "Content-Type: application/json" \
  -d '{
    "source_files": {
      "calculator.py": "def add(a, b):\n    return a + b\n\ndef divide(a, b):\n    return a / b"
    },
    "test_files": {
      "test_calculator.py": "from calculator import add, divide\n\ndef test_add():\n    assert add(2, 3) == 5\n\ndef test_divide():\n    assert divide(10, 2) == 5.0"
    },
    "test_command": "pytest --tb=short -q"
  }'
```

## How It Works

1. **Baseline Check** — Runs your test suite against the original code to confirm it passes.
2. **AST Mutations** — Generates deterministic operator-level mutations (swap `+`→`-`, `<`→`<=`, `and`→`or`, return values → `None`, boundary constants).
3. **AI Mutations** — Asks IBM Granite to propose realistic semantic mutations (off-by-one errors, swapped arguments, missing edge cases).
4. **Test Execution** — For each mutant, injects the mutation and runs the test suite in a sandboxed environment.
5. **Scoring** — A mutant *killed* by a failing test = good. A mutant that *survives* = test gap.
6. **AI Analysis** — Granite generates a narrative report with actionable recommendations.

## Mutation Score

```
Mutation Score = (Killed Mutants / Total Mutants) × 100%
```

- **90–100%** — Excellent defect-detection capability
- **70–89%** — Good, but gaps exist
- **50–69%** — Moderate — significant improvements needed
- **< 50%** — Poor — tests provide weak safety guarantees

## Configuration

All settings are in `.env` (see `.env.example`). Key options:

| Variable | Default | Description |
|----------|---------|-------------|
| `WATSONX_API_KEY` | — | IBM Cloud API key |
| `WATSONX_PROJECT_ID` | — | watsonx.ai project ID |
| `GRANITE_MODEL_ID` | `ibm/granite-3-3-8b-instruct` | Foundation model |
| `SANDBOX_MODE` | `subprocess` | `subprocess` or `docker` |
| `MAX_MUTATIONS_PER_FILE` | `50` | AST mutation cap per file |
| `AI_MUTATIONS_PER_FILE` | `5` | Granite mutations per file |
| `SANDBOX_TIMEOUT_SECONDS` | `60` | Per-mutant test timeout |

## Tech Stack

- **Python 3.11+** · **FastAPI** · **Uvicorn** · **Pydantic**
- **pytest** for test execution
- **IBM watsonx.ai** (Granite models) for AI-powered mutations and analysis
- **AST module** for deterministic code mutations
- **subprocess / Docker** for secure sandboxed execution

## License

MIT
