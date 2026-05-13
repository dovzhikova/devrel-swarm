# devrel-origin CLI — Phase 1: Repo Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing `agents/` and `tools/` packages to `src/devrel_origin/{core,tools}/` with src-layout packaging and updated imports, with all existing tests still passing. No behaviour change.

**Architecture:** Standard Python src-layout. The 13 agents move to `src/devrel_origin/core/` (renamed from "agents" because in the new product the agents are the implementation core, not the public surface). Tool modules move to `src/devrel_origin/tools/`. `agents/config.py` is renamed to `core/agent_config.py` to disambiguate from the future `cli/config.py` introduced in Phase 4. All imports rewritten from `agents.X` / `tools.X` to `devrel_origin.core.X` / `devrel_origin.tools.X`.

**Tech Stack:** Python 3.12+, setuptools src-layout (already setuptools-based), pytest + pytest-asyncio + respx (existing), `git mv` for history-preserving moves.

**Spec:** `docs/superpowers/specs/2026-04-29-devrel-origin-cli-design.md`

---

## File structure after Phase 1

```
src/devrel_origin/
  __init__.py              # version, public re-exports
  core/
    __init__.py
    atlas.py
    base.py
    agent_config.py        # renamed from config.py
    llm.py
    types.py
    sage.py echo.py iris.py nova.py kai.py vox.py dex.py
    rex.py pax.py mox.py sentinel.py watchdog.py
    video/                 # subdir, contents unchanged
  tools/
    __init__.py
    api_client.py apollo_client.py code_validator.py github_tools.py
    instantly_client.py kb_harvester.py mcp_server.py notifications.py
    run_report.py scheduler.py search_tools.py self_improve.py sheets.py
tests/                     # location unchanged, imports updated
config/                    # unchanged
knowledge_base/            # unchanged
optimize/                  # unchanged
deliverables/              # unchanged
run_100_leads.py           # unchanged location, imports updated
run_sales_pipeline.py      # unchanged location, imports updated
pyproject.toml             # updated for src-layout
Dockerfile                 # COPY paths and CMD updated
CLAUDE.md                  # path references updated
```

No code is deleted. No behaviour changes. Only file paths and imports.

---

## Pre-flight: worktree setup

- [ ] **Step 1: Create a fresh worktree off `main`**

Use the **superpowers:using-git-worktrees** skill to create a worktree for this work. Branch name: `feat/cli-phase1-restructure`. All subsequent steps run inside that worktree. Do not run any of the moves on `main` directly.

- [ ] **Step 2: Confirm starting state inside the worktree**

Run:
```bash
git rev-parse --abbrev-ref HEAD
git log --oneline -1
```
Expected: branch `feat/cli-phase1-restructure`, HEAD at the most recent `main` commit (the spec commit `e9aa09a` or later).

---

## Task 1: Baseline — capture passing test count

**Files:** none (commands only — output saved to disk for later comparison)

- [ ] **Step 1: Install dev deps + package in editable mode (current layout)**

Run:
```bash
python -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -e '.[dev]' >/tmp/install.before.log 2>&1
echo "exit=$?"
```
Expected: `exit=0`. If anything fails, stop and surface the error — do not proceed.

- [ ] **Step 2: Capture baseline test result**

Run:
```bash
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tee /tmp/pytest.before.txt | tail -5
grep "^FAILED" /tmp/pytest.before.txt | sort > /tmp/pytest.failures.before.txt
wc -l /tmp/pytest.failures.before.txt
```

**Documented baseline (locked 2026-04-29):** `566 passed, 22 failed`. The 22 failing tests are pre-existing drift between code and tests on `main`, unrelated to Phase 1. They will not be fixed by this phase. The verification gate in Task 6 is parity against this exact set, not "zero failures."

If your numbers differ from `566 passed, 22 failed` at this step, **stop and report** — something has changed on `main` since the baseline was locked, and the gate needs re-grounding before proceeding.

- [ ] **Step 3: Capture the existing import map (for diff after rewrite)**

Run:
```bash
grep -rEn "^(from|import) (agents|tools)\." --include='*.py' \
  agents tools tests run_100_leads.py run_sales_pipeline.py \
  > /tmp/imports.before.txt
grep -rEn "^(from|import) (agents|tools)$" --include='*.py' \
  agents tools tests run_100_leads.py run_sales_pipeline.py \
  >> /tmp/imports.before.txt 2>/dev/null || true
wc -l /tmp/imports.before.txt
```
Expected: 135 lines (the locked baseline as of 2026-04-29). If your count drifts significantly, the population to rewrite has changed since the plan was written — surface and re-baseline.

---

## Task 2: Update `pyproject.toml` for src-layout

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Replace the `[tool.setuptools.packages.find]` block**

In `pyproject.toml`, find:
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["agents*", "tools*"]
```

Replace with:
```toml
[tool.setuptools.packages.find]
where = ["src"]
include = ["devrel_origin*"]
```

- [ ] **Step 2: Update coverage and test configuration to point at new package**

In `pyproject.toml`, find:
```toml
[tool.pytest.ini_options]
minversion = "7.0"
addopts = "-v --cov=agents --cov=tools --cov-report=term-missing"
```

Replace `--cov=agents --cov=tools` with `--cov=devrel_origin`:
```toml
addopts = "-v --cov=devrel_origin --cov-report=term-missing"
```

In `pyproject.toml`, find:
```toml
[tool.coverage.run]
branch = true
source = ["agents", "tools"]
```

Replace with:
```toml
[tool.coverage.run]
branch = true
source = ["devrel_origin"]
```

- [ ] **Step 3: Verify `pyproject.toml` parses**

Run:
```bash
python -c "import tomllib; tomllib.loads(open('pyproject.toml').read()); print('ok')"
```
Expected: `ok`. If `tomllib` raises, fix the TOML and re-run.

**Do not commit yet.** The package won't actually build cleanly until Task 3 + Task 4 land — at this point `pyproject.toml` references a `src/` tree that does not exist. That's intentional; the package install step at the end of Task 4 will validate the whole shape together.

---

## Task 3: Move files into the new `src/devrel_origin/` layout

**Files:**
- Create: `src/devrel_origin/__init__.py`, `src/devrel_origin/core/__init__.py`, `src/devrel_origin/tools/__init__.py`
- Move: every file under `agents/` → `src/devrel_origin/core/`
- Move: every file under `tools/` → `src/devrel_origin/tools/`
- Rename (during move): `agents/config.py` → `src/devrel_origin/core/agent_config.py`

- [ ] **Step 1: Create the package skeleton directories**

Run:
```bash
mkdir -p src/devrel_origin/core src/devrel_origin/tools
```

- [ ] **Step 2: Create `src/devrel_origin/__init__.py` with version metadata**

Write to `src/devrel_origin/__init__.py`:
```python
"""devrel-origin — DevRel + Sales + Marketing agent system."""

__version__ = "0.2.0"
```

(Bump from 0.1.0 to 0.2.0 reflects the breaking package-path change for any external importers; `pyproject.toml` will be updated in lockstep in Step 7.)

- [ ] **Step 3: Move every Python module out of `agents/` to `src/devrel_origin/core/` using `git mv` (preserves history)**

Run, in order:
```bash
git mv agents/__init__.py src/devrel_origin/core/__init__.py
git mv agents/config.py   src/devrel_origin/core/agent_config.py
for f in atlas.py base.py llm.py types.py sage.py echo.py iris.py nova.py \
         kai.py vox.py dex.py rex.py pax.py mox.py sentinel.py watchdog.py; do
  git mv "agents/$f" "src/devrel_origin/core/$f"
done
git mv agents/video src/devrel_origin/core/video
rmdir agents
```
Expected after each command: clean exit. After the loop, `agents/` no longer exists and `src/devrel_origin/core/` contains every former `agents/` file plus `agent_config.py` (formerly `config.py`).

- [ ] **Step 4: Move every Python module out of `tools/` to `src/devrel_origin/tools/`**

Run:
```bash
git mv tools/__init__.py src/devrel_origin/tools/__init__.py
for f in api_client.py apollo_client.py code_validator.py github_tools.py \
         instantly_client.py kb_harvester.py mcp_server.py notifications.py \
         run_report.py scheduler.py search_tools.py self_improve.py sheets.py; do
  git mv "tools/$f" "src/devrel_origin/tools/$f"
done
rmdir tools
```
Expected: clean exit; `tools/` gone, `src/devrel_origin/tools/` populated.

- [ ] **Step 5: Verify the move is clean**

Run:
```bash
test ! -d agents && test ! -d tools && echo ok
ls src/devrel_origin/core/ | wc -l    # expect 19 entries (18 .py incl. agent_config.py and __init__.py, plus video/)
ls src/devrel_origin/tools/ | wc -l   # expect 14 entries (13 .py + __init__.py)
```
Expected: `ok`, then two counts close to the comments. If a count is short, find what got missed under `agents/` or `tools/` and `git mv` it now (do not leave any `*.py` orphans behind).

- [ ] **Step 6: Confirm `git status` looks like renames, not delete+add**

Run:
```bash
git status --short | head -20
```
Expected: lines starting with `R` (renames). If you see `D` (delete) followed by `A` (add) for the same logical file, that's a sign `git mv` was bypassed; fix it before proceeding by running `git mv` for the offending file.

- [ ] **Step 7: Bump `pyproject.toml` version 0.1.0 → 0.2.0**

In `pyproject.toml`, find:
```toml
version = "0.1.0"
```
Replace with:
```toml
version = "0.2.0"
```

**Do not commit yet** — every import in the codebase still references `agents.X` and `tools.X`. Task 4 fixes that.

---

## Task 4: Rewrite imports across the codebase

**Files (modified, not moved):**
- All `*.py` under `src/devrel_origin/core/` and `src/devrel_origin/tools/`
- All `*.py` under `tests/`
- `run_100_leads.py`, `run_sales_pipeline.py`

**Strategy:** A single bulk sed pass. Order matters: rename `agents.config` first, then the generic `agents.` pattern, then `tools.`. Use `\b` word-boundary equivalent (the `[. ]` group) so we don't accidentally rewrite `agents` as a substring of an unrelated identifier (none exists in this codebase, but the safety is free).

- [ ] **Step 1: Define the file population to rewrite**

Run:
```bash
TARGETS=$(git ls-files \
  'src/devrel_origin/core/*.py' 'src/devrel_origin/core/**/*.py' \
  'src/devrel_origin/tools/*.py' \
  'tests/*.py' \
  'run_100_leads.py' 'run_sales_pipeline.py')
echo "$TARGETS" | wc -l
```
Expected: a count matching the moved + adjacent files (around 60+ files).

- [ ] **Step 2: Rewrite `agents.config` references first (because of the rename)**

Run:
```bash
echo "$TARGETS" | xargs sed -i '' \
  -e 's|^from agents\.config import|from devrel_origin.core.agent_config import|g' \
  -e 's|^import agents\.config\b|import devrel_origin.core.agent_config|g'
```

(Note: macOS BSD sed needs `-i ''`; on Linux drop the empty-string arg.)

- [ ] **Step 3: Rewrite the remaining `agents.X` and `tools.X` references**

Run:
```bash
echo "$TARGETS" | xargs sed -i '' \
  -e 's|^from agents\.|from devrel_origin.core.|g' \
  -e 's|^from agents import|from devrel_origin.core import|g' \
  -e 's|^import agents\.|import devrel_origin.core.|g' \
  -e 's|^import agents$|import devrel_origin.core|g' \
  -e 's|^from tools\.|from devrel_origin.tools.|g' \
  -e 's|^from tools import|from devrel_origin.tools import|g' \
  -e 's|^import tools\.|import devrel_origin.tools.|g'
```

- [ ] **Step 4: Catch CLI-style `python -m agents.X` strings used in subprocess/CLI tests**

Run:
```bash
echo "$TARGETS" | xargs grep -lE 'python -m agents\.|python -m tools\.' | tee /tmp/m_dash_hits.txt
echo "$TARGETS" | xargs sed -i '' \
  -e 's|python -m agents\.|python -m devrel_origin.core.|g' \
  -e 's|python -m tools\.|python -m devrel_origin.tools.|g'
```
Expected: `/tmp/m_dash_hits.txt` lists any tests/scripts that referenced the old `python -m` paths (likely `agents/atlas.py`'s docstring + a few tests). After the sed, those references now point at the new module paths.

- [ ] **Step 5: Verify no stray old imports remain**

Run:
```bash
git grep -nE "^(from|import) (agents|tools)(\.|\b)" -- '*.py'
```
Expected: **empty output**. If any line returns, inspect it manually and rewrite — it likely failed the regex (e.g., a multi-line import). Re-run this grep until it returns zero lines.

- [ ] **Step 6: Verify the edits are syntactically sane**

Run:
```bash
python -m compileall -q src/devrel_origin tests run_100_leads.py run_sales_pipeline.py 2>&1 | tail -20
echo "exit=$?"
```
Expected: `exit=0` (silent compile means every file parses). If `compileall` reports a `SyntaxError`, the rewrite produced a bad line — fix it before continuing.

---

## Task 5: Update peripheral references (Dockerfile, CLAUDE.md, scripts)

**Files:**
- Modify: `Dockerfile`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `Dockerfile`**

In `Dockerfile`, find:
```dockerfile
COPY agents/ agents/
COPY tools/ tools/
```
Replace with:
```dockerfile
COPY src/ src/
COPY pyproject.toml ./
RUN pip install -e .
```

In `Dockerfile`, find:
```dockerfile
CMD ["python", "-m", "agents.atlas", "--weekly-cycle"]
```
Replace with:
```dockerfile
CMD ["python", "-m", "devrel_origin.core.atlas", "--weekly-cycle"]
```

- [ ] **Step 2: Update `CLAUDE.md` path references**

In `CLAUDE.md`, the `## File Map` section lists `agents/X` and `tools/X` paths. Replace each top-level header line:
- `agents/` → `src/devrel_origin/core/`
- `tools/` → `src/devrel_origin/tools/`
- `agents.X` → `devrel_origin.core.X` (anywhere in prose / code blocks)
- `tools.X` → `devrel_origin.tools.X`
- `agents/config.py` → `src/devrel_origin/core/agent_config.py`

Also update the **Commands** section so example invocations reference the new module path (`python -m devrel_origin.core.atlas --weekly-cycle`).

Add at the top of `CLAUDE.md`, just under the title, a one-line note:
```
> **Note:** This repository moved to a `src/devrel_origin/` layout in Phase 1 of the CLI direction. See `docs/superpowers/specs/2026-04-29-devrel-origin-cli-design.md`.
```

- [ ] **Step 3: Quick sanity grep — no stale paths remain in tracked files**

Run:
```bash
git grep -n -E '(agents/|tools/)' -- ':(exclude)docs/superpowers/' \
  ':(exclude).planning/' ':(exclude)deliverables/' ':(exclude)knowledge_base/' \
  ':(exclude)optimize/' ':(exclude)config/' \
  | grep -vE '(\.devrel/|\.gitignore|src/devrel_origin)'
```
Expected: empty output, OR only lines inside docstrings / comments where the old path is being described historically. Inspect each remaining line — if it's a stale reference, fix it; if it's intentional historical mention, leave it.

---

## Task 6: Reinstall package and run full test suite

**Files:** none (verification only)

- [ ] **Step 1: Reinstall in editable mode against the new layout**

Run:
```bash
pip install -e '.[dev]' > /tmp/install.after.log 2>&1
echo "exit=$?"
```
Expected: `exit=0`. If install fails:
- A `package not found` error means `pyproject.toml`'s `[tool.setuptools.packages.find]` didn't pick up `src/devrel_origin` — re-check Task 2 Step 1.
- A `ModuleNotFoundError` at import time means an `__init__.py` is missing — re-check Task 3 Steps 1–4.

- [ ] **Step 2: Run the full test suite**

Run:
```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tee /tmp/pytest.after.txt | tail -10
grep "^FAILED" /tmp/pytest.after.txt | sort > /tmp/pytest.failures.after.txt
```
Expected: the summary line shows **`566 passed, 22 failed`** — the exact baseline from Task 1 Step 2.

- [ ] **Step 3: Diff the failing-test sets to confirm the same 22 fail**

Run:
```bash
diff /tmp/pytest.failures.before.txt /tmp/pytest.failures.after.txt
```
Expected: **empty diff**. The same 22 tests must fail with the same node IDs after the move. If the diff has any line, **stop and investigate** — either a previously-failing test is now passing (good but unexpected; investigate), or a previously-passing test is now failing (regression — must fix before commit). Common causes when imports got missed:
- Missed import rewrite — `git grep -nE "^(from|import) (agents|tools)\." -- '*.py'` should be empty.
- A test that did `monkeypatch.setattr("agents.X.Y", ...)` — string-based patches need updating to `devrel_origin.core.X.Y`.
- `conftest.py` manipulating `sys.path` — inspect `tests/conftest.py` and update any `sys.path.insert(0, "agents")` style entries.

- [ ] **Step 4: Smoke-test the module entry point**

Run:
```bash
python -m devrel_origin.core.atlas --help 2>&1 | head -5
```
Expected: Atlas's CLI help text prints (the existing argparse output). If you get `No module named devrel_origin.core.atlas`, the install or path is still wrong — fix before committing.

---

## Task 7: Commit and finalize

**Files:** none (git plumbing)

- [ ] **Step 1: Confirm `git status` reflects only intended renames + edits**

Run:
```bash
git status --short
```
Expected: `R` rename lines for every moved file, plus `M` lines for `pyproject.toml`, `Dockerfile`, `CLAUDE.md`, the various import-edited files, and `??` for the new `src/devrel_origin/__init__.py`. No surprise additions.

- [ ] **Step 2: Stage everything**

Run:
```bash
git add -A
git status --short | head -20
```
Expected: the same files now show as staged (`A`, `R`, `M`).

- [ ] **Step 3: Commit as a single restructure commit**

Run:
```bash
git commit -m "$(cat <<'EOF'
refactor: move to src/devrel_origin/ layout (Phase 1)

Restructure agents/ and tools/ into src/devrel_origin/core/ and
src/devrel_origin/tools/ with src-layout packaging. Renames
agents/config.py to core/agent_config.py to disambiguate from the
forthcoming cli/config.py module. No behaviour change — every
existing test passes at the same count as before the move.

This is Phase 1 of the CLI direction defined in
docs/superpowers/specs/2026-04-29-devrel-origin-cli-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If a pre-commit hook fails, fix the offending issue, re-stage, and create a new commit (do not `--amend`).

- [ ] **Step 4: Final verification on the committed tree**

Run:
```bash
git log --oneline -1
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
```
Expected: a single commit on this branch since `main`, and the same passing test count again. If any test is now failing on the committed tree but was passing in Task 6 Step 2, something was missed in `git add` — investigate.

- [ ] **Step 5: Hand off**

Phase 1 is complete. The branch `feat/cli-phase1-restructure` is ready to merge to `main`. Use the **superpowers:finishing-a-development-branch** skill to choose between merge / PR / hold.

---

## Self-review checklist (already applied)

- **Spec coverage:** Phase 1 of the spec's §Implementation phasing block ("Repo restructure — package move, `pyproject.toml` rewrite, all existing tests still pass after the move") — covered by Tasks 2–7. The `core/agent_config.py` rename called out in §Code structure — covered by Task 3 Step 3.
- **No placeholders:** No TBD, TODO, "appropriate error handling," or undefined references. Every step has either explicit code/commands or an explicit verification.
- **Type / name consistency:** `agent_config.py` is consistent across the import rewrite (Task 4 Step 2) and the imports referenced in CLAUDE.md (Task 5 Step 2).
- **Reversibility:** This is a single squashable commit; if anything is wrong post-merge, `git revert` recovers cleanly. The pre-flight worktree gate prevents accidental work on `main`.

## Out of scope (deferred to later phases)

- Adding any CLI surface, Typer dependency, Rich, or `devrel` console_script (Phase 4).
- Adding the `quality/` package or modifying any agent's critique loop (Phase 3).
- Adding `project/` bootstrap or `.devrel/` scaffolding (Phase 2).
- Archiving the `product/v0-agentic-alpha` branch or deleting `tools/http_bridge.py` (Phase 5; those files don't exist on `main` yet anyway).
- Switching to relative imports within the package or to a non-setuptools backend (e.g., hatchling, uv) — keep setuptools for minimum churn.
