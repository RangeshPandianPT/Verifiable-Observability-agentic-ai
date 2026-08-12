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
| 2 | ⬜ Next | Strategy Profiler — rule-based task classifier |
| 3 | ⬜ | Constraint Compliance Monitor — Finance constraints |
| 4 | ⬜ | Real LLM wiring (Anthropic/OpenAI adapter) |
| 5 | ⬜ | Metrics Engine + Behavioral Regimes |
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
│   └── loop.py                # AgentLoop wrapper
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
    └── main.py                # Typer CLI: demo, rulebank list/verify/show/add
tests/
├── test_smoke.py              # Phase 0 end-to-end smoke tests
└── test_rule_bank.py          # Phase 1 Rule Bank unit tests
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
- Anthropic / OpenAI adapters (Phase 4)
