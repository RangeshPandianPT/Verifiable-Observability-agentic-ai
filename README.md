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
| 4 | ✅ Done | Real LLM wiring (Anthropic/OpenAI adapter) |
| 5 | ⬜ Next | Metrics Engine + Behavioral Regimes |
| 6 | ⬜ | Healthcare & Code Execution domains |
| 7 | ⬜ | Evaluation harness + reproducible results |
| 8 | ⬜ | Optional: minimal dashboard |

---

## Project Structure

```
verifiable_observability/
├── core/
│   ├── strategy_profiler.py   # StrategyProfilerBase + StubStrategyProfiler
│   ├── rule_bank.py           # RuleBankBase + StubRuleBank + RuleBank
│   ├── matching.py            # StructuredPredicateMatcher + SimilarityMatcher
│   ├── constraint_monitor.py  # ConstraintComplianceMonitorBase + StubCCM
│   ├── metrics.py             # MetricsEngineBase + BasicMetricsEngine
│   └── orchestrator.py        # Orchestrator — drives the turn loop
├── agent/
│   ├── adapter.py             # AgentAdapterBase + ScriptedAgentAdapter
│   ├── loop.py                # AgentLoop wrapper
│   ├── tool_registry.py       # Finance tool schemas + simulated executor  [Phase 4]
│   ├── ollama_adapter.py      # OllamaAgentAdapter (default local backend) [Phase 4+]
│   ├── anthropic_adapter.py   # AnthropicAgentAdapter (Claude)              [Phase 4]
│   ├── openai_adapter.py      # OpenAIAgentAdapter (GPT-4o)                 [Phase 4]
│   └── factory.py             # build_adapter() -- config-driven selection    [Phase 4+]
├── simulation/
│   ├── domains/
│   │   ├── finance/
│   │   │   └── seed_rules.py  # 15 Finance seed rules
│   │   ├── healthcare/        # Phase 6
│   │   └── code_execution/    # Phase 6
│   └── regimes/               # Phase 5
├── storage/
│   ├── models.py              # All Pydantic v2 schemas
│   └── db.py                  # SQLite (SQLAlchemy Core) — TrajectoryStore, RuleStore
└── cli/
    └── main.py                # Typer CLI: demo, run, rulebank list/verify/show/add
tests/
├── test_smoke.py              # Phase 0 end-to-end smoke tests
├── test_rule_bank.py          # Phase 1 Rule Bank unit tests
├── test_constraint_monitor.py # Phase 3 CCM unit tests
├── test_llm_adapters.py       # Phase 4 adapter unit tests (cloud, mocked)
└── test_ollama_adapter.py     # Phase 4+ Ollama adapter + factory tests (mocked)
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
