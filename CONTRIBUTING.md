# Contributing to DevRel Swarm

Thanks for your interest in contributing! This guide helps you get started.

## Quick Setup

```bash
git clone https://github.com/dovzhikova/devrel-swarm.git
cd devrel-swarm
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config/env.example .env
# Add at minimum: ANTHROPIC_API_KEY
```

## Running Tests

```bash
pytest tests/ -v                    # Full suite
pytest tests/test_kai.py -v         # Single agent
pytest tests/ -k "not integration"  # Skip integration tests
```

Tests use `respx` to mock all HTTP calls — no API keys needed for testing.

## Project Structure

```
agents/          # 12 agent implementations + shared base
  atlas.py       # Orchestrator (start here)
  base.py        # Shared: TF-IDF KB search, prompt loading
  llm.py         # LLM client with revision loop + cost tracking
  config.py      # YAML config loader
tools/           # External integrations (GitHub, Search, Email, etc.)
knowledge_base/  # Product docs (markdown, searched via TF-IDF)
optimize/        # Per-agent prompt optimization + eval harness
tests/           # pytest + respx mocking
config/          # env.example + agent_config.yaml
```

## How to Contribute

### Fix a Bug
1. Check existing issues
2. Write a failing test first
3. Fix the bug
4. Ensure `pytest tests/ -v` passes

### Add a New Agent
1. Create `agents/new_agent.py` with an `execute(task, context)` async method
2. Add to `Atlas.__init__()` and the weekly cycle in `run_weekly_cycle()`
3. Update `SharedContext` with the agent's output field
4. Add tests in `tests/test_new_agent.py`
5. Add an eval scorer in `optimize/agent_eval.py`
6. Update CLAUDE.md file map

### Add an Integration
1. Create `tools/new_tool.py` with an async client class
2. Add env vars to `config/env.example`
3. Wire into Atlas or the relevant agent
4. Add tests with respx mocking
5. Graceful degradation: always check env vars before using

### Override an Agent Prompt
Drop a file at `optimize/{agent_name}/system_prompt.txt` — agents auto-load it via `load_agent_prompt()`.

## Retargeting to Your Product

This system is designed to be pointed at any DevTools product:

1. Set `PRODUCT_NAME` and `PRODUCT_URL` in `.env`
2. Replace `knowledge_base/` contents with your product docs
3. Set `GITHUB_REPO` to your repo
4. Run `python -m agents.atlas --weekly-cycle`

## Code Style

- Python 3.12+ with type hints everywhere
- `httpx` for HTTP (never `requests` or `aiohttp`)
- `async/await` for all I/O
- Dataclasses for DTOs
- `logging` over `print`
- Line length: 100 (ruff + black)
- Tests: `pytest` + `respx` for HTTP mocking

```bash
ruff check .         # Lint
black .              # Format
mypy agents/ tools/  # Type check
```

## Pull Request Checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] No hardcoded API keys, URLs, or personal data
- [ ] New env vars added to `config/env.example`
- [ ] CLAUDE.md updated if architecture changed
- [ ] Type hints on all new functions
