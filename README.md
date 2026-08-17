# Verifiable Observability

**Real-time behavioral verification for multi-turn LLM agents.**

A research-grade framework that adds three verification layers to any LLM agent loop — checking reasoning consistency and constraint compliance *before* actions are dispatched, not after terminal failure is visible.

---

## Architecture

```
Task → [Strategy Profiler] → StrategyProfile
                                    │
            ┌───────────────────────┘
            ▼
Agent Decision ──► [Rule Bank] ──► RuleCheckResult (RCR)
            │
            ▼
Agent Action ───► [CCM] ──────────► ConstraintCheckResult (CCR)
            │
            └── ALLOW: dispatch ──► Simulated Tool
            └── BLOCK: stop trajectory
            └── FLAG:  escalate + continue
```

**Three layers:**
1. **Strategy Profiler** — classifies the task (domain, type, risk tier) to set a behavioral baseline
2. **Rule Bank** — checks every Decision against verified observation→action rules; tracks RCR
3. **Constraint Compliance Monitor (CCM)** — enforces hard/soft safety constraints before dispatch; tracks CCR

**Metrics:**
- **RCR** (Reasoning Consistency Ratio) = matched decisions / total decisions per turn
- **CCR** (Constraint Compliance Ratio) = ALLOW actions / total actions per turn

---

## Setup

```bash
pip install -e ".[dev]"
```

Or with `uv`:
```bash
uv pip install -e ".[dev]"
```

---

## Usage

### Run the smoke demo (Phase 0)
```bash
python -m verifiable_observability.cli.main demo
# or with the installed script:
vo demo
```

### Rule Bank CLI (Phase 1)
```bash
# Load Finance seed rules and list them
vo rulebank list --seed

# List by domain and status
vo rulebank list --domain finance --status verified

# Verify a pending rule
vo rulebank verify fin-rt-001 --verifier human

# Show rule detail
vo rulebank show fin-rt-001

# Add a rule from JSON
vo rulebank add path/to/rule.json --provenance "trajectory-xyz"
```

### Real LLM Run (Phase 4)

1. Copy `.env.example` to `.env` and add your API key:
```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY or OPENAI_API_KEY
```

2. Run a task with a real LLM:
```bash
# Ollama (default, zero API cost)
vo run "Transfer $500 from ACC-001 to ACC-002"
vo run "Check balance" --backend ollama --model llama3.2:3b

# Anthropic Claude
vo run "Transfer $500" --backend anthropic

# OpenAI GPT-4o
vo run "Rebalance my portfolio" --backend openai

# Model override and verbose logging
vo run "Check account balance" --backend anthropic --model claude-3-5-sonnet-20241022 --verbose

# Custom domain or turn limit
vo run "Execute high-value trade" --domain finance --max-turns 15
```

### Ollama Local Setup

1. Install Ollama: https://ollama.com/download
2. Pull one or both supported models:
```bash
ollama pull llama3.2:3b   # 2 GB, fast, good for development
ollama pull llama3.2:1b   # 1.2 GB, smallest option
# or larger models when available:
# ollama pull qwen2.5:14b
```
3. Start the server (runs automatically on Windows; or `ollama serve` on Linux/Mac)
4. Set `agent_backend=ollama` in `config.yaml` (already the default) and run:
```bash
vo run "Transfer $500 from ACC-001 to ACC-002"
```
No API key required. All inference runs locally.

### Behavioral Regimes (Phase 5)
```bash
# List all available regimes
vo regime list

# Run a regime through the full verification stack
vo regime run compliant
vo regime run mild_drift
vo regime run adversarial_injection --verbose
vo regime run tool_failure_drift --domain finance
```

### Domain Seed Rules (Phase 6)
```bash
# Load seed rules for any domain into the Rule Bank
vo domain seed finance
vo domain seed healthcare
vo domain seed code_execution

# Load without auto-verifying (rules stay PENDING)
vo domain seed healthcare --no-verify
```

### Run Healthcare or Code Execution tasks (Phase 6)
```bash
# Healthcare tasks — medication management, patient data access, clinical decisions
vo run "Prescribe amoxicillin for patient PAT-001" --domain healthcare
vo run "Retrieve patient record for PAT-002" --domain healthcare
vo run "Recommend treatment guideline for hypertension" --domain healthcare

# Code Execution tasks — code generation, review, system commands
vo run "Generate a Python function to parse JSON" --domain code_execution
vo run "Review this pull request for security issues" --domain code_execution
vo run "Execute a shell command to list files" --domain code_execution
```

### Trajectory Analysis + Drift Detection (Phase 5)
```bash
# Compare all stored trajectories (RCR, CCR, drift flag)
vo analyze trajectories

# Show only trajectories where drift was detected
vo analyze trajectories --drift-only

# Filter by domain or outcome
vo analyze trajectories --domain finance --outcome blocked
vo analyze trajectories --domain healthcare

# Verbose: includes full per-trajectory drift report JSON
vo analyze trajectories --verbose
```

### Run tests
```bash
pytest tests/ -v
```

---

## Phase Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 0 | ✅ Done | Repo scaffolding, interfaces, smoke test |
| 1 | ✅ Done | Rule Bank — matching, lifecycle, Finance seed rules |
| 2 | ✅ Done | Strategy Profiler — rule-based task classifier |
| 3 | ✅ Done | Constraint Compliance Monitor — Finance constraints |
| 4 | ✅ Done | Real LLM wiring (Ollama local, Anthropic, OpenAI adapters) |
| 5 | ✅ Done | Metrics Engine + Behavioral Regimes + Drift Detection |
| 6 | ✅ Done | Healthcare & Code Execution domains |
| 7 | ⬜ Next | Evaluation harness + reproducible results |
| 8 | ⬜ | Optional: minimal dashboard |

---

## Project Structure

```
verifiable_observability/
├── core/
│   ├── strategy_profiler.py   # StrategyProfilerBase + StrategyProfiler (all 3 domains)
│   ├── rule_bank.py           # RuleBankBase + StubRuleBank + RuleBank
│   ├── matching.py            # StructuredPredicateMatcher + SimilarityMatcher
│   ├── constraint_monitor.py  # StubCCM, FinanceCCM, HealthcareCCM, CodeExecutionCCM + build_ccm()
│   ├── metrics.py             # MetricsEngineBase + BasicMetricsEngine
│   └── orchestrator.py        # Orchestrator — drives the turn loop
├── agent/
│   ├── adapter.py             # AgentAdapterBase + ScriptedAgentAdapter
│   ├── loop.py                # AgentLoop wrapper
│   ├── tool_registry.py       # Finance/Healthcare/CodeExec tool schemas + get_tools_for_domain()  [Phase 4+6]
│   ├── ollama_adapter.py      # OllamaAgentAdapter (default local backend) [Phase 4+]
│   ├── anthropic_adapter.py   # AnthropicAgentAdapter (Claude)              [Phase 4]
│   ├── openai_adapter.py      # OpenAIAgentAdapter (GPT-4o)                 [Phase 4]
│   └── factory.py             # build_adapter() -- config-driven selection    [Phase 4+]
├── simulation/
│   ├── domains/
│   │   ├── finance/
│   │   │   └── seed_rules.py  # 15 Finance seed rules (routine_transfer, portfolio_rebalance, high_value_trade)
│   │   ├── healthcare/
│   │   │   └── seed_rules.py  # 12 Healthcare seed rules (medication_management, patient_data_access, clinical_decision_support)  [Phase 6]
│   │   └── code_execution/
│   │       └── seed_rules.py  # 12 Code Execution seed rules (code_generation, code_review, system_command_execution)  [Phase 6]
│   └── regimes/               # Phase 5
│       ├── base.py            # RegimeBase + RegimeType enum + REGIME_EXPECTATIONS
│       ├── compliant.py       # COMPLIANT — high RCR, CCR=1.0
│       ├── mild_drift.py      # MILD_DRIFT — skips pre-checks, rule misses
│       ├── adversarial_injection.py  # ADVERSARIAL — triggers CCM BLOCK
│       └── tool_failure_drift.py     # TOOL_FAILURE — declining RCR over turns
├── storage/
│   ├── models.py              # All Pydantic v2 schemas
│   └── db.py                  # SQLite (SQLAlchemy Core) — TrajectoryStore, RuleStore
└── cli/
    └── main.py                # Typer CLI: demo, run, rulebank, analyze, regime, domain
tests/
├── test_smoke.py              # Phase 0 end-to-end smoke tests
├── test_rule_bank.py          # Phase 1 Rule Bank unit tests
├── test_constraint_monitor.py # Phase 3 CCM unit tests
├── test_llm_adapters.py       # Phase 4 adapter unit tests (cloud, mocked)
├── test_ollama_adapter.py     # Phase 4+ Ollama adapter + factory tests (mocked)
├── test_phase5_regimes.py     # Phase 5 regimes, drift detection, metrics tests
└── test_phase6_domains.py     # Phase 6 Healthcare + Code Execution domain tests (68 tests)
config.yaml                    # Runtime config + Phase 7 experiment sweep matrix
.env.example                   # Environment variable template
```

---

## How to Add a New Domain

1. Create `simulation/domains/<domain>/seed_rules.py` with `get_seed_rules()` and `load_seed_rules_into_bank()`
2. Add task types to the Strategy Profiler (Phase 2)
3. Add a constraint set to the CCM (Phase 3)
4. Wire domain-specific tasks into the evaluation harness (Phase 7)

## How to Swap the LLM Adapter

Implement `AgentAdapterBase.generate()` in a new file under `agent/`:
```python
class MyAdapter(AgentAdapterBase):
    def generate(self, system_prompt, conversation, task) -> AgentResponse:
        ...
```
Pass it to `Orchestrator(agent_adapter=MyAdapter(...))`.

---

## Stack

- Python 3.11+, Pydantic v2, SQLAlchemy Core (SQLite → upgradeable to Postgres)
- Typer + Rich for CLI, pytest for tests
- No external ML dependencies for rule matching (TF-IDF cosine is stdlib-compatible)
- **Local inference**: Ollama (default) — llama3.2:3b, llama3.2:1b, qwen2.5 (Phase 4+)
- Cloud adapters: Anthropic Claude, OpenAI GPT-4o (Phase 4)
- `config.yaml` + env vars for backend selection; Phase 7 sweep matrix for multi-model experiments
