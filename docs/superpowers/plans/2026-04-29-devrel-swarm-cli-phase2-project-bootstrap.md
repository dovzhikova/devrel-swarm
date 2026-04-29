# devrel-swarm CLI — Phase 2: Project Bootstrap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `devrel init` and `devrel doctor` user-facing commands plus the supporting `project/` package (paths, config, state, init, templates) and a minimal Typer CLI skeleton that registers them, so a developer can `cd` into any repo, run `devrel init`, and have a working `.devrel/` scaffold validated by `devrel doctor`.

**Architecture:** Five new modules under `src/devrel_swarm/project/` (paths, config, state, init, templates) plus a thin Typer app under `src/devrel_swarm/cli/` with two registered commands. `init_project()` is idempotent — re-running merges, never clobbers committed files (voice.md / style.md / slop-blocklist.md / config.toml). State DB at `.devrel/state.db` is initialized empty with a versioned schema; agents start writing to it in Phase 3. Paths discovery walks up from cwd like `git rev-parse --show-toplevel`. Templates ship as in-package data files copied on init.

**Tech Stack:** Python 3.12+, Typer (`>=0.12.0`), Rich (`>=13.7.0`), `tomli-w` for TOML serialization (`tomllib` stdlib for reads), stdlib `sqlite3`, pytest + pytest-asyncio + Typer's `CliRunner` for tests.

**Spec:** `docs/superpowers/specs/2026-04-29-devrel-swarm-cli-design.md`
**Phase 1 (prerequisite, already merged):** `be971bd refactor: move to src/devrel_swarm/ layout (Phase 1)`

---

## File structure after Phase 2

```
src/devrel_swarm/
  cli/                              NEW
    __init__.py                     # Typer app, version flag, command registration
    init.py                         # devrel init
    doctor.py                       # devrel doctor
  project/                          NEW
    __init__.py
    paths.py                        # find_devrel_root, ProjectPaths dataclass
    config.py                       # ProjectConfig + TOML load + env merge
    state.py                        # SQLite schema + init_db + get_schema_version
    init.py                         # init_project() — scaffold .devrel/ idempotently
    templates/
      __init__.py                   # importlib.resources entry
      config.toml                   # template, copied to .devrel/
      voice.md                      # template
      style.md                      # template
      slop-blocklist.md             # template
      devrel.gitignore              # template, becomes .devrel/.gitignore
tests/
  project/
    __init__.py                     NEW
    test_paths.py                   NEW
    test_config.py                  NEW   (renamed-aware; the existing tests/test_config.py is for agent_config.py)
    test_state.py                   NEW
    test_init.py                    NEW
  cli/
    __init__.py                     NEW
    test_init_command.py            NEW
    test_doctor_command.py          NEW
pyproject.toml                      # add typer, rich, tomli-w deps; add [project.scripts]
```

No code moves. No existing files restructure. Phase 1's surface stays put.

---

## Pre-flight: worktree setup

- [ ] **Step 1: Create a fresh worktree off `main`**

Use **superpowers:using-git-worktrees** to create a worktree at `.worktrees/cli-phase2-bootstrap` on a new branch `feat/cli-phase2-bootstrap`. Confirm `main` is at `be971bd` (Phase 1 merge) or later before branching.

- [ ] **Step 2: Confirm starting state inside the worktree**

```bash
git rev-parse --abbrev-ref HEAD
git log --oneline -1
test -d src/devrel_swarm/core && test -d src/devrel_swarm/tools && echo "Phase 1 layout present"
```
Expected: branch `feat/cli-phase2-bootstrap`, HEAD at the latest `main` commit, `Phase 1 layout present` printed.

- [ ] **Step 3: Activate venv + reinstall against current pyproject.toml**

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]' >/tmp/install.preflight.log 2>&1 && echo "exit=$?"
```
Expected: `exit=0`. Then run baseline:
```bash
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
```
Expected: `566 passed, 22 failed`. Phase 2 work must preserve this; new tests Phase 2 adds will increase the passing count, but the 22 known failures must remain identical (same node IDs).

```bash
grep "^FAILED" <(python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1) | sort > /tmp/pytest.failures.phase2.before.txt
wc -l /tmp/pytest.failures.phase2.before.txt
```
Expected: `22`.

---

## Task 1: Add Typer + Rich + tomli-w dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the three new runtime deps**

In `pyproject.toml`, find the `dependencies = [...]` block and append three lines so the block reads (preserving existing entries, appending):

```toml
dependencies = [
    "httpx>=0.25.0",
    "anthropic>=0.28.0",
    "pyyaml>=6.0",
    "scipy>=1.13.0",
    "python-dotenv>=1.0.0",
    "tenacity>=8.2.0",
    "PyGithub>=2.1.1",
    "requests>=2.31.0",
    "aiohttp>=3.9.0",
    "openai>=1.50.0",
    "playwright>=1.49.0",
    "ffmpeg-python>=0.2.0",
    "pyautogui>=0.9.54",
    "typer>=0.12.0",
    "rich>=13.7.0",
    "tomli-w>=1.0.0",
]
```

- [ ] **Step 2: Reinstall**

```bash
pip install -e '.[dev]' >/tmp/install.deps.log 2>&1 && echo "exit=$?"
python -c "import typer, rich, tomli_w; print('ok')"
```
Expected: `exit=0`, then `ok`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat(deps): add Typer + Rich + tomli-w for Phase 2 CLI"
```

---

## Task 2: `project/paths.py` — root discovery + path dataclass

**Files:**
- Create: `src/devrel_swarm/project/__init__.py`
- Create: `src/devrel_swarm/project/paths.py`
- Create: `tests/project/__init__.py`
- Create: `tests/project/test_paths.py`

- [ ] **Step 1: Create empty package init files**

Write `src/devrel_swarm/project/__init__.py`:
```python
"""Project bootstrap: .devrel/ scaffold, config, state, paths."""
```

Write `tests/project/__init__.py`:
```python
```
(empty file — required for pytest test discovery in this layout).

- [ ] **Step 2: Write the failing test for `find_devrel_root`**

Write `tests/project/test_paths.py`:
```python
"""Tests for project root discovery + ProjectPaths."""

from __future__ import annotations

import pytest

from devrel_swarm.project.paths import (
    DEVREL_DIR_NAME,
    ProjectNotFoundError,
    ProjectPaths,
    find_devrel_root,
)


def _make_devrel(root):
    """Helper: create a minimal valid .devrel/config.toml under root."""
    d = root / DEVREL_DIR_NAME
    d.mkdir()
    (d / "config.toml").write_text('[project]\nname = "test"\n')
    return root


def test_finds_root_when_cwd_is_project_root(tmp_path):
    project = _make_devrel(tmp_path)
    assert find_devrel_root(project) == project.resolve()


def test_finds_root_from_nested_subdirectory(tmp_path):
    project = _make_devrel(tmp_path)
    nested = project / "src" / "deep" / "module"
    nested.mkdir(parents=True)
    assert find_devrel_root(nested) == project.resolve()


def test_raises_when_no_devrel_anywhere(tmp_path):
    with pytest.raises(ProjectNotFoundError):
        find_devrel_root(tmp_path)


def test_ignores_devrel_dir_without_config_toml(tmp_path):
    """A bare .devrel/ without config.toml shouldn't count as a project."""
    (tmp_path / DEVREL_DIR_NAME).mkdir()
    with pytest.raises(ProjectNotFoundError):
        find_devrel_root(tmp_path)


def test_paths_dataclass_derives_all_paths(tmp_path):
    project = _make_devrel(tmp_path)
    p = ProjectPaths.from_root(project)
    assert p.root == project
    assert p.devrel_dir == project / ".devrel"
    assert p.config_file == project / ".devrel" / "config.toml"
    assert p.voice_file == project / ".devrel" / "voice.md"
    assert p.style_file == project / ".devrel" / "style.md"
    assert p.slop_file == project / ".devrel" / "slop-blocklist.md"
    assert p.kb_dir == project / ".devrel" / "kb"
    assert p.deliverables_dir == project / ".devrel" / "deliverables"
    assert p.context_dir == project / ".devrel" / "context"
    assert p.state_db == project / ".devrel" / "state.db"
    assert p.env_file == project / ".devrel" / ".env"
    assert p.gitignore == project / ".devrel" / ".gitignore"
```

- [ ] **Step 3: Run test, confirm it fails with ImportError**

```bash
python -m pytest tests/project/test_paths.py -v --no-cov 2>&1 | tail -10
```
Expected: ImportError or ModuleNotFoundError on `devrel_swarm.project.paths`.

- [ ] **Step 4: Implement `paths.py`**

Write `src/devrel_swarm/project/paths.py`:
```python
"""Project path discovery and structure.

`find_devrel_root` walks up from a starting directory looking for the nearest
ancestor containing a `.devrel/config.toml`. `ProjectPaths` is a frozen
dataclass holding every derived path under `.devrel/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEVREL_DIR_NAME = ".devrel"
CONFIG_FILE_NAME = "config.toml"


class ProjectNotFoundError(Exception):
    """Raised when no .devrel/config.toml is found in cwd or any ancestor."""


@dataclass(frozen=True)
class ProjectPaths:
    """All derived paths for a devrel-swarm project."""

    root: Path
    devrel_dir: Path
    config_file: Path
    voice_file: Path
    style_file: Path
    slop_file: Path
    kb_dir: Path
    deliverables_dir: Path
    context_dir: Path
    state_db: Path
    env_file: Path
    gitignore: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        d = root / DEVREL_DIR_NAME
        return cls(
            root=root,
            devrel_dir=d,
            config_file=d / CONFIG_FILE_NAME,
            voice_file=d / "voice.md",
            style_file=d / "style.md",
            slop_file=d / "slop-blocklist.md",
            kb_dir=d / "kb",
            deliverables_dir=d / "deliverables",
            context_dir=d / "context",
            state_db=d / "state.db",
            env_file=d / ".env",
            gitignore=d / ".gitignore",
        )


def find_devrel_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: cwd) until a `.devrel/config.toml` is
    found. Returns the project root (parent of `.devrel/`), resolved to an
    absolute path.

    Raises ProjectNotFoundError if no `.devrel/config.toml` is found before
    the filesystem root.
    """
    cur = (start if start is not None else Path.cwd()).resolve()
    while True:
        candidate = cur / DEVREL_DIR_NAME / CONFIG_FILE_NAME
        if candidate.is_file():
            return cur
        if cur.parent == cur:
            raise ProjectNotFoundError(
                "No .devrel/config.toml found in cwd or any ancestor. "
                "Run `devrel init` from the project root."
            )
        cur = cur.parent
```

- [ ] **Step 5: Run tests to verify pass**

```bash
python -m pytest tests/project/test_paths.py -v --no-cov 2>&1 | tail -10
```
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/devrel_swarm/project/__init__.py src/devrel_swarm/project/paths.py \
        tests/project/__init__.py tests/project/test_paths.py
git commit -m "feat(project): add path discovery and ProjectPaths dataclass"
```

---

## Task 3: `project/config.py` — TOML config loader

**Files:**
- Create: `src/devrel_swarm/project/config.py`
- Create: `tests/project/test_config.py`

- [ ] **Step 1: Write failing tests**

Write `tests/project/test_config.py`:
```python
"""Tests for ProjectConfig loading from TOML."""

from __future__ import annotations

import pytest

from devrel_swarm.project.config import (
    BudgetConfig,
    ConfigError,
    ModelConfig,
    ProjectConfig,
    ProjectIdentity,
)


def _write(tmp_path, body):
    f = tmp_path / "config.toml"
    f.write_text(body)
    return f


def test_load_minimal_config(tmp_path):
    f = _write(tmp_path, '[project]\nname = "openclaw"\n')
    cfg = ProjectConfig.load(f)
    assert cfg.project.name == "openclaw"
    assert cfg.project.url == ""
    assert cfg.project.github_repo is None
    assert cfg.model == ModelConfig()
    assert cfg.budget == BudgetConfig()


def test_load_full_config(tmp_path):
    f = _write(
        tmp_path,
        """
[project]
name = "openclaw"
url = "https://openclaw.ai"
github_repo = "openclaw/openclaw"

[model]
default = "claude-sonnet-4-6"
cheap = "claude-haiku-4-5-20251001"
opus_opt_in = false

[budget]
monthly_usd = 250.0
warn_at_pct = 70
""",
    )
    cfg = ProjectConfig.load(f)
    assert cfg.project == ProjectIdentity(
        name="openclaw",
        url="https://openclaw.ai",
        github_repo="openclaw/openclaw",
    )
    assert cfg.model.default == "claude-sonnet-4-6"
    assert cfg.model.cheap == "claude-haiku-4-5-20251001"
    assert cfg.model.opus_opt_in is False
    assert cfg.budget.monthly_usd == 250.0
    assert cfg.budget.warn_at_pct == 70


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        ProjectConfig.load(tmp_path / "absent.toml")


def test_missing_project_section_raises(tmp_path):
    f = _write(tmp_path, '[model]\ndefault = "x"\n')
    with pytest.raises(ConfigError, match=r"\[project\]"):
        ProjectConfig.load(f)


def test_missing_project_name_raises(tmp_path):
    f = _write(tmp_path, '[project]\nurl = "x"\n')
    with pytest.raises(ConfigError, match="project.name"):
        ProjectConfig.load(f)


def test_partial_model_section_uses_defaults(tmp_path):
    f = _write(
        tmp_path,
        '[project]\nname = "x"\n[model]\ndefault = "claude-opus-4-7"\n',
    )
    cfg = ProjectConfig.load(f)
    assert cfg.model.default == "claude-opus-4-7"
    assert cfg.model.cheap == ModelConfig().cheap  # default preserved
    assert cfg.model.opus_opt_in is True            # default preserved
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/project/test_config.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError on `devrel_swarm.project.config`.

- [ ] **Step 3: Implement `config.py`**

Write `src/devrel_swarm/project/config.py`:
```python
"""Load .devrel/config.toml into a typed ProjectConfig.

The schema is intentionally narrow: project identity (required), model
selection (optional with sensible defaults), and budget guardrails
(optional). Future phases extend this with additional sections; the loader
is permissive about unknown top-level keys.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised when config.toml is malformed or missing required fields."""


@dataclass(frozen=True)
class ProjectIdentity:
    name: str
    url: str = ""
    github_repo: str | None = None


@dataclass(frozen=True)
class ModelConfig:
    default: str = "claude-sonnet-4-6"
    cheap: str = "claude-haiku-4-5-20251001"
    opus_opt_in: bool = True


@dataclass(frozen=True)
class BudgetConfig:
    monthly_usd: float = 100.0
    warn_at_pct: int = 80


@dataclass(frozen=True)
class ProjectConfig:
    project: ProjectIdentity
    model: ModelConfig = field(default_factory=ModelConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    @classmethod
    def load(cls, config_file: Path) -> "ProjectConfig":
        if not config_file.is_file():
            raise ConfigError(f"config.toml not found at {config_file}")
        with config_file.open("rb") as f:
            raw = tomllib.load(f)
        if "project" not in raw:
            raise ConfigError("config.toml missing required [project] section")
        proj = raw["project"]
        if "name" not in proj or not proj["name"]:
            raise ConfigError("config.toml missing required project.name")
        identity = ProjectIdentity(
            name=str(proj["name"]),
            url=str(proj.get("url", "")),
            github_repo=proj.get("github_repo"),
        )
        model_raw = raw.get("model") or {}
        defaults = ModelConfig()
        model = ModelConfig(
            default=str(model_raw.get("default", defaults.default)),
            cheap=str(model_raw.get("cheap", defaults.cheap)),
            opus_opt_in=bool(model_raw.get("opus_opt_in", defaults.opus_opt_in)),
        )
        budget_raw = raw.get("budget") or {}
        bdefaults = BudgetConfig()
        budget = BudgetConfig(
            monthly_usd=float(budget_raw.get("monthly_usd", bdefaults.monthly_usd)),
            warn_at_pct=int(budget_raw.get("warn_at_pct", bdefaults.warn_at_pct)),
        )
        return cls(project=identity, model=model, budget=budget)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/project/test_config.py -v --no-cov 2>&1 | tail -10
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/project/config.py tests/project/test_config.py
git commit -m "feat(project): add ProjectConfig TOML loader with typed sections"
```

---

## Task 4: `project/state.py` — SQLite schema + init

**Files:**
- Create: `src/devrel_swarm/project/state.py`
- Create: `tests/project/test_state.py`

- [ ] **Step 1: Write failing tests**

Write `tests/project/test_state.py`:
```python
"""Tests for project state DB initialization and schema introspection."""

from __future__ import annotations

import sqlite3

import pytest

from devrel_swarm.project.state import (
    SCHEMA_VERSION,
    get_schema_version,
    init_db,
    open_db,
)


def test_init_db_creates_file_and_tables(tmp_path):
    db = tmp_path / "state.db"
    assert not db.exists()
    init_db(db)
    assert db.is_file()
    with sqlite3.connect(db) as conn:
        names = sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"
            )
        )
    assert names == ["checkpoints", "costs", "jobs", "schema_meta"]


def test_init_db_creates_parent_dir(tmp_path):
    db = tmp_path / "nested" / "dir" / "state.db"
    init_db(db)
    assert db.is_file()


def test_get_schema_version_returns_current(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    assert get_schema_version(db) == SCHEMA_VERSION


def test_get_schema_version_returns_none_when_db_missing(tmp_path):
    assert get_schema_version(tmp_path / "absent.db") is None


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    # Insert a row, then re-init; row must survive.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, status) VALUES (?, ?, ?)",
            ("abc", "weekly_cycle", "queued"),
        )
        conn.commit()
    init_db(db)
    with sqlite3.connect(db) as conn:
        rows = list(conn.execute("SELECT id FROM jobs"))
    assert rows == [("abc",)]


def test_open_db_yields_row_factory_connection(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with open_db(db) as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, status) VALUES (?, ?, ?)",
            ("xyz", "triage", "queued"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", ("xyz",)).fetchone()
    assert row["id"] == "xyz"
    assert row["kind"] == "triage"
    assert row["status"] == "queued"


def test_jobs_status_check_constraint_enforced(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (id, kind, status) VALUES (?, ?, ?)",
                ("bad", "x", "garbage_status"),
            )
            conn.commit()
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/project/test_state.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError on `devrel_swarm.project.state`.

- [ ] **Step 3: Implement `state.py`**

Write `src/devrel_swarm/project/state.py`:
```python
"""Project state DB: SQLite at .devrel/state.db.

Stores: jobs (kind, status, started/finished timestamps), costs (per-call
token + USD ledger consumed by BudgetGate), checkpoints (per-agent context
snapshots, one per (agent, week_of) pair).

In Phase 2 the DB is initialized empty by `devrel init`. Agents start
writing to it in Phase 3 (quality pipeline cost recording) and beyond.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    week_of TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, week_of)
);
"""


def init_db(db_path: Path) -> None:
    """Create the DB file and apply the schema. Idempotent — preserves
    existing data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()


def get_schema_version(db_path: Path) -> int | None:
    """Return the current schema version, or None if the DB is missing /
    has no schema_meta table."""
    if not db_path.is_file():
        return None
    with sqlite3.connect(db_path) as conn:
        try:
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
        except sqlite3.OperationalError:
            return None
        row = cur.fetchone()
        return row[0] if row else None


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection with row_factory set and
    foreign-key enforcement enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/project/test_state.py -v --no-cov 2>&1 | tail -10
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/project/state.py tests/project/test_state.py
git commit -m "feat(project): add SQLite state DB schema + init"
```

---

## Task 5: Templates (`project/templates/*`)

**Files:**
- Create: `src/devrel_swarm/project/templates/__init__.py`
- Create: `src/devrel_swarm/project/templates/config.toml`
- Create: `src/devrel_swarm/project/templates/voice.md`
- Create: `src/devrel_swarm/project/templates/style.md`
- Create: `src/devrel_swarm/project/templates/slop-blocklist.md`
- Create: `src/devrel_swarm/project/templates/devrel.gitignore`
- Modify: `pyproject.toml` (add `[tool.setuptools.package-data]` entry to ship the templates inside the wheel)

- [ ] **Step 1: Create templates package init**

Write `src/devrel_swarm/project/templates/__init__.py`:
```python
"""Static template files copied into .devrel/ on `devrel init`.

Access via `importlib.resources.files("devrel_swarm.project.templates")`.
"""
```

- [ ] **Step 2: Write `config.toml` template**

Write `src/devrel_swarm/project/templates/config.toml`:
```toml
# devrel-swarm project config. Commit this file — it encodes editorial
# contract along with voice.md / style.md / slop-blocklist.md.

# Project identity. Replace placeholders with real values; the github_repo
# is optional (set to a comment-out or null if there isn't one).
[project]
name = "PROJECT_NAME"
url = "PROJECT_URL"
github_repo = "OWNER/REPO"

# Model selection. Sonnet for primary work; Haiku for cheap quality stages
# (slop lint, persona scoring, readability scoring); Opus opt-in via
# `--model opus` on individual commands.
[model]
default = "claude-sonnet-4-6"
cheap = "claude-haiku-4-5-20251001"
opus_opt_in = true

# Budget guardrails. monthly_usd is the cap; warn_at_pct is the threshold
# at which `devrel doctor` and `devrel run` print a warning. Set
# monthly_usd to 0 to disable the cap entirely.
[budget]
monthly_usd = 100.0
warn_at_pct = 80
```

- [ ] **Step 3: Write `voice.md` template**

Write `src/devrel_swarm/project/templates/voice.md`:
```markdown
# Voice profile

The tone, register, and stylistic markers that make content sound like *this product* — not generic AI output. Keep this short. Edit it after you read your published content out loud and hear the voice.

## Tone

Describe the voice in 3-5 adjectives.

> Replace with: e.g., "direct, technical, mildly irreverent, never preachy, no marketing fluff."

One or two sentences explaining how that voice shows up in writing.

## Sample passages

Three to five short excerpts (50-150 words each) from existing content that should sound exactly like new content. Use your best published work.

> Replace this blockquote with a real sample.

> Replace this blockquote with a real sample.

> Replace this blockquote with a real sample.

## Words and phrases we use

Comma-separated list of vocabulary that's distinctively ours.

## Words and phrases we avoid

Beyond the global slop blocklist, anything specific to this product's voice that should never appear.
```

- [ ] **Step 4: Write `style.md` template**

Write `src/devrel_swarm/project/templates/style.md`:
```markdown
# House style

Structural and per-content-type rules. Short rules; expand only where the rule isn't obvious from the rule itself.

## Structural rules

- Sentence-case headings (not Title Case).
- One H1 per document (the title).
- Code blocks always have language tags: ```python, ```bash, etc.
- No trailing whitespace.
- Reference-style links only when the same URL repeats.
- No emojis in headings; sparingly in body.

## Per-content-type targets

| Content type | Flesch-Kincaid | Mean sentence length | Jargon density |
|---|---|---|---|
| Tutorial | 50-65 | 12-18 words | medium |
| Blog post | 55-70 | 12-20 words | low-medium |
| Landing page | 60-75 | 10-15 words | low |
| Cold email | 65-80 | 10-14 words | low |
| Battle card | 45-60 | 12-18 words | medium-high |

Targets are guidance, not pass/fail gates. The readability check in the quality pipeline flags drift greater than ±10 points from the Flesch-Kincaid target.
```

- [ ] **Step 5: Write `slop-blocklist.md` template**

Write `src/devrel_swarm/project/templates/slop-blocklist.md`:
```markdown
# Anti-slop blocklist

Words, phrases, and patterns that mark text as AI-written. The quality pipeline rewrites any content that contains a hit; on second failure it aborts loud with a report listing offenders.

One entry per line. Lines starting with `#` are comments and ignored. Matching is case-insensitive against word boundaries.

## Hedge words and filler
perhaps
furthermore
moreover
in conclusion
in today's
in this fast-paced world

## AI tells
delve
delves
tapestry
seamless
seamlessly
unleash
unleashing
revolutionize
revolutionary
empower
empowering
groundbreaking

## Generic CTAs
learn more
discover more
get started today
contact us today

## Listicle filler
in this article we will
this article will explore
in this post

## Empty intensifiers
truly
incredibly
extremely
very
really
```

- [ ] **Step 6: Write `devrel.gitignore` template**

Write `src/devrel_swarm/project/templates/devrel.gitignore`:
```
# Auto-managed by `devrel init`. Edit only if you know what you're doing.
# Generated outputs and runtime state are gitignored; the editorial
# contract files (config.toml, voice.md, style.md, slop-blocklist.md) are
# intended to be committed.

kb/
deliverables/
context/
state.db
.env
```

- [ ] **Step 7: Tell setuptools to ship templates inside the wheel**

In `pyproject.toml`, after the existing `[tool.setuptools.packages.find]` block, add:
```toml
[tool.setuptools.package-data]
"devrel_swarm.project.templates" = ["*.toml", "*.md", "*.gitignore"]
```

- [ ] **Step 8: Verify templates ship via `importlib.resources`**

```bash
pip install -e '.[dev]' >/dev/null 2>&1 && \
python -c "
from importlib.resources import files
pkg = files('devrel_swarm.project.templates')
names = sorted(p.name for p in pkg.iterdir())
print(names)
"
```
Expected: `['__init__.py', '__pycache__'?, 'config.toml', 'devrel.gitignore', 'slop-blocklist.md', 'style.md', 'voice.md']` (the `__pycache__` line varies and can be ignored).

- [ ] **Step 9: Commit**

```bash
git add src/devrel_swarm/project/templates/ pyproject.toml
git commit -m "feat(project): add .devrel/ scaffold templates"
```

---

## Task 6: `project/init.py` — `init_project()` scaffolder

**Files:**
- Create: `src/devrel_swarm/project/init.py`
- Create: `tests/project/test_init.py`

- [ ] **Step 1: Write failing tests**

Write `tests/project/test_init.py`:
```python
"""Tests for init_project() — idempotent .devrel/ scaffolding."""

from __future__ import annotations

import pytest

from devrel_swarm.project.init import (
    InitOptions,
    InitResult,
    init_project,
)
from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.project.state import SCHEMA_VERSION, get_schema_version


def test_init_creates_full_scaffold(tmp_path):
    opts = InitOptions(name="openclaw", url="https://openclaw.ai", github_repo="openclaw/openclaw")
    result = init_project(tmp_path, opts)
    p = ProjectPaths.from_root(tmp_path)
    assert p.devrel_dir.is_dir()
    assert p.config_file.is_file()
    assert p.voice_file.is_file()
    assert p.style_file.is_file()
    assert p.slop_file.is_file()
    assert p.kb_dir.is_dir()
    assert p.deliverables_dir.is_dir()
    assert p.context_dir.is_dir()
    assert p.gitignore.is_file()
    assert p.state_db.is_file()
    assert get_schema_version(p.state_db) == SCHEMA_VERSION
    assert isinstance(result, InitResult)
    assert result.created and not result.skipped


def test_init_substitutes_placeholders_in_config(tmp_path):
    opts = InitOptions(name="openclaw", url="https://openclaw.ai", github_repo="openclaw/openclaw")
    init_project(tmp_path, opts)
    body = (tmp_path / ".devrel" / "config.toml").read_text()
    assert 'name = "openclaw"' in body
    assert 'url = "https://openclaw.ai"' in body
    assert 'github_repo = "openclaw/openclaw"' in body
    assert "PROJECT_NAME" not in body
    assert "PROJECT_URL" not in body
    assert "OWNER/REPO" not in body


def test_init_is_idempotent_and_preserves_user_edits(tmp_path):
    opts = InitOptions(name="x", url="", github_repo=None)
    init_project(tmp_path, opts)
    voice = tmp_path / ".devrel" / "voice.md"
    voice.write_text("# my custom voice — DO NOT CLOBBER\n")
    result = init_project(tmp_path, opts)
    assert voice.read_text() == "# my custom voice — DO NOT CLOBBER\n"
    assert "voice.md" in result.skipped
    assert "config.toml" in result.skipped


def test_init_handles_missing_github_repo(tmp_path):
    opts = InitOptions(name="solo", url="https://solo.dev", github_repo=None)
    init_project(tmp_path, opts)
    body = (tmp_path / ".devrel" / "config.toml").read_text()
    # github_repo line should be absent or commented out, not literally
    # 'OWNER/REPO' or empty quotes
    assert 'github_repo = "OWNER/REPO"' not in body
    assert 'github_repo = ""' not in body


def test_init_dry_run_creates_nothing(tmp_path):
    opts = InitOptions(name="x", url="", github_repo=None, dry_run=True)
    result = init_project(tmp_path, opts)
    assert not (tmp_path / ".devrel").exists()
    assert result.dry_run is True
    assert "config.toml" in result.would_create


def test_init_creates_devrel_gitignore(tmp_path):
    opts = InitOptions(name="x", url="", github_repo=None)
    init_project(tmp_path, opts)
    body = (tmp_path / ".devrel" / ".gitignore").read_text()
    assert "kb/" in body
    assert "deliverables/" in body
    assert "state.db" in body
    assert ".env" in body
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/project/test_init.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError on `devrel_swarm.project.init`.

- [ ] **Step 3: Implement `init.py`**

Write `src/devrel_swarm/project/init.py`:
```python
"""Idempotent .devrel/ scaffolder.

`init_project(root, opts)` writes the .devrel/ directory tree, copies the
template files, substitutes config placeholders, and initializes the state
DB. Re-running on an existing project preserves user edits to committed
files (config.toml, voice.md, style.md, slop-blocklist.md, .gitignore) —
those are listed in `result.skipped`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.project.state import init_db

_TEMPLATE_PKG = "devrel_swarm.project.templates"

# Files that are committed and must NEVER be overwritten on re-init.
_COMMITTED_FILES = ("config.toml", "voice.md", "style.md", "slop-blocklist.md", ".gitignore")


@dataclass(frozen=True)
class InitOptions:
    name: str
    url: str = ""
    github_repo: str | None = None
    dry_run: bool = False


@dataclass
class InitResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    would_create: list[str] = field(default_factory=list)
    dry_run: bool = False


def _read_template(name: str) -> str:
    return (files(_TEMPLATE_PKG) / name).read_text(encoding="utf-8")


def _render_config_toml(opts: InitOptions) -> str:
    body = _read_template("config.toml")
    body = body.replace("PROJECT_NAME", opts.name)
    body = body.replace("PROJECT_URL", opts.url)
    if opts.github_repo:
        body = body.replace('github_repo = "OWNER/REPO"', f'github_repo = "{opts.github_repo}"')
    else:
        body = body.replace(
            'github_repo = "OWNER/REPO"',
            "# github_repo = \"OWNER/REPO\"  # set if this product has a public repo",
        )
    return body


def init_project(root: Path, opts: InitOptions) -> InitResult:
    """Scaffold .devrel/ under `root`. Idempotent: preserves committed files
    on re-run."""
    paths = ProjectPaths.from_root(root)
    result = InitResult(dry_run=opts.dry_run)

    # The directory and subdirectories.
    dirs = [paths.devrel_dir, paths.kb_dir, paths.deliverables_dir, paths.context_dir]
    for d in dirs:
        if d.is_dir():
            result.skipped.append(d.name + "/")
        else:
            if opts.dry_run:
                result.would_create.append(d.name + "/")
            else:
                d.mkdir(parents=True, exist_ok=True)
                result.created.append(d.name + "/")

    # File payloads keyed by destination path.
    payloads: dict[Path, str] = {
        paths.config_file: _render_config_toml(opts),
        paths.voice_file: _read_template("voice.md"),
        paths.style_file: _read_template("style.md"),
        paths.slop_file: _read_template("slop-blocklist.md"),
        paths.gitignore: _read_template("devrel.gitignore"),
    }
    for dest, body in payloads.items():
        if dest.is_file():
            result.skipped.append(dest.name)
            continue
        if opts.dry_run:
            result.would_create.append(dest.name)
        else:
            dest.write_text(body, encoding="utf-8")
            result.created.append(dest.name)

    # State DB: idempotent (init_db preserves rows).
    if opts.dry_run:
        if not paths.state_db.is_file():
            result.would_create.append("state.db")
    else:
        already = paths.state_db.is_file()
        init_db(paths.state_db)
        (result.skipped if already else result.created).append("state.db")

    return result
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/project/test_init.py -v --no-cov 2>&1 | tail -10
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/project/init.py tests/project/test_init.py
git commit -m "feat(project): add idempotent init_project() scaffolder"
```

---

## Task 7: CLI app skeleton + `devrel init` command

**Files:**
- Create: `src/devrel_swarm/cli/__init__.py`
- Create: `src/devrel_swarm/cli/init.py`
- Create: `tests/cli/__init__.py`
- Create: `tests/cli/test_init_command.py`
- Modify: `pyproject.toml` — add `[project.scripts]` entry

- [ ] **Step 1: Create CLI package skeleton**

Write `src/devrel_swarm/cli/__init__.py`:
```python
"""Typer CLI app for devrel-swarm.

Phase 2 registers `init` and `doctor`. Later phases register additional
verb groups (run, content, sales, marketing, etc.).
"""

from __future__ import annotations

import typer

from devrel_swarm import __version__
from devrel_swarm.cli.init import init_command


app = typer.Typer(
    name="devrel",
    help="DevRel + Sales + Marketing agent system. Run from inside a project repo.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"devrel-swarm {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Root callback. Subcommands are registered below."""
    return None


app.command(name="init")(init_command)


if __name__ == "__main__":
    app()
```

Write `tests/cli/__init__.py`:
```python
```
(empty).

- [ ] **Step 2: Write the `init` CLI command**

Write `src/devrel_swarm/cli/init.py`:
```python
"""`devrel init` command — bootstrap .devrel/ in cwd."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from devrel_swarm.project.init import InitOptions, init_project

console = Console()


def init_command(
    name: str = typer.Option(
        ...,
        "--name",
        prompt="Project name (e.g., 'openclaw')",
        help="The product this devrel-swarm instance covers.",
    ),
    url: str = typer.Option(
        "",
        "--url",
        prompt="Project URL (or empty)",
        help="Public homepage URL for the product. Optional.",
    ),
    github_repo: str = typer.Option(
        "",
        "--github-repo",
        prompt="GitHub repo as 'owner/name' (or empty)",
        help="Optional. Used by Sage for issue triage.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be created without writing anything.",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Skip prompts. Requires --name (others default to empty/null).",
    ),
) -> None:
    """Bootstrap a `.devrel/` scaffold in the current directory."""
    if non_interactive and not name:
        console.print("[red]--non-interactive requires --name.[/red]")
        raise typer.Exit(code=2)

    opts = InitOptions(
        name=name,
        url=url,
        github_repo=github_repo or None,
        dry_run=dry_run,
    )
    result = init_project(Path.cwd(), opts)

    if result.dry_run:
        console.print("[yellow]Dry run — nothing written.[/yellow]")
        for entry in result.would_create:
            console.print(f"  + {entry}")
        return

    for entry in result.created:
        console.print(f"  [green]+[/green] {entry}")
    for entry in result.skipped:
        console.print(f"  [dim]= {entry} (existed; preserved)[/dim]")
    console.print()
    console.print("[bold green]Done.[/bold green] Edit voice.md / style.md / slop-blocklist.md, then run [cyan]devrel doctor[/cyan].")
```

- [ ] **Step 3: Add the console_script entry to `pyproject.toml`**

In `pyproject.toml`, append after `[project.optional-dependencies]`:
```toml
[project.scripts]
devrel = "devrel_swarm.cli:app"
```

Then reinstall:
```bash
pip install -e '.[dev]' >/dev/null 2>&1
which devrel
devrel --version
```
Expected: a path to `devrel` inside `.venv/bin/`, then `devrel-swarm 0.2.0`.

- [ ] **Step 4: Write the failing CLI test**

Write `tests/cli/test_init_command.py`:
```python
"""Tests for `devrel init`."""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _run_in(tmp_path, *args):
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        return runner.invoke(app, list(args))
    finally:
        os.chdir(cwd)


def test_init_non_interactive_minimal(tmp_path):
    result = _run_in(
        tmp_path,
        "init",
        "--non-interactive",
        "--name", "openclaw",
        "--url", "",
        "--github-repo", "",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".devrel" / "config.toml").is_file()
    assert (tmp_path / ".devrel" / "voice.md").is_file()
    assert (tmp_path / ".devrel" / "state.db").is_file()


def test_init_non_interactive_full(tmp_path):
    result = _run_in(
        tmp_path,
        "init",
        "--non-interactive",
        "--name", "openclaw",
        "--url", "https://openclaw.ai",
        "--github-repo", "openclaw/openclaw",
    )
    assert result.exit_code == 0, result.output
    body = (tmp_path / ".devrel" / "config.toml").read_text()
    assert 'name = "openclaw"' in body
    assert 'url = "https://openclaw.ai"' in body
    assert 'github_repo = "openclaw/openclaw"' in body


def test_init_dry_run_writes_nothing(tmp_path):
    result = _run_in(
        tmp_path,
        "init",
        "--non-interactive",
        "--name", "x",
        "--url", "",
        "--github-repo", "",
        "--dry-run",
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert not (tmp_path / ".devrel").exists()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "devrel-swarm" in result.output
```

- [ ] **Step 5: Run tests to verify pass**

```bash
python -m pytest tests/cli/test_init_command.py -v --no-cov 2>&1 | tail -10
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/devrel_swarm/cli/ tests/cli/__init__.py tests/cli/test_init_command.py pyproject.toml
git commit -m "feat(cli): add Typer skeleton + 'devrel init' command"
```

---

## Task 8: `devrel doctor` command

**Files:**
- Create: `src/devrel_swarm/cli/doctor.py`
- Create: `tests/cli/test_doctor_command.py`
- Modify: `src/devrel_swarm/cli/__init__.py` (register the new command)

- [ ] **Step 1: Write failing tests**

Write `tests/cli/test_doctor_command.py`:
```python
"""Tests for `devrel doctor`."""

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _run_in(tmp_path, *args, env=None):
    cwd = os.getcwd()
    saved = os.environ.copy()
    try:
        os.chdir(tmp_path)
        if env:
            os.environ.update(env)
        return runner.invoke(app, list(args))
    finally:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(saved)


def _init(tmp_path):
    runner.invoke(
        app,
        [
            "init",
            "--non-interactive",
            "--name", "x",
            "--url", "",
            "--github-repo", "",
        ],
    )


def test_doctor_fails_when_no_devrel(tmp_path):
    result = _run_in(tmp_path, "doctor")
    assert result.exit_code != 0
    assert "No .devrel/" in result.output or "not found" in result.output.lower()


def test_doctor_passes_with_anthropic_key(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)
    result = _run_in(tmp_path, "doctor", env={"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY" in result.output


def test_doctor_fails_without_required_env(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        result = _run_in(tmp_path, "doctor")
        assert result.exit_code != 0
        assert "ANTHROPIC_API_KEY" in result.output
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_doctor_json_mode(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)
    result = _run_in(tmp_path, "doctor", "--json", env={"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] in ("ok", "warn", "fail")
    assert "checks" in data
    assert any(c["name"] == "ANTHROPIC_API_KEY" for c in data["checks"])
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/cli/test_doctor_command.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError or `No such command` because `doctor` isn't wired yet.

- [ ] **Step 3: Implement `doctor.py`**

Write `src/devrel_swarm/cli/doctor.py`:
```python
"""`devrel doctor` — health checks for the current project."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import typer
from rich.console import Console

from devrel_swarm.project.config import ConfigError, ProjectConfig
from devrel_swarm.project.paths import (
    DEVREL_DIR_NAME,
    ProjectNotFoundError,
    ProjectPaths,
    find_devrel_root,
)
from devrel_swarm.project.state import SCHEMA_VERSION, get_schema_version

console = Console()

REQUIRED_ENV = ("ANTHROPIC_API_KEY",)
OPTIONAL_ENV = (
    "GITHUB_TOKEN",
    "FIRECRAWL_API_KEY",
    "BRAVE_API_KEY",
    "INSTANTLY_API_KEY",
    "APOLLO_API_KEY",
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)


@dataclass
class CheckResult:
    name: str
    status: str           # 'pass' | 'warn' | 'fail'
    detail: str = ""


def _run_checks(paths: ProjectPaths) -> list[CheckResult]:
    results: list[CheckResult] = []

    # Python version.
    py = sys.version_info
    py_str = f"{py.major}.{py.minor}.{py.micro}"
    if (py.major, py.minor) >= (3, 12):
        results.append(CheckResult("python_version", "pass", py_str))
    else:
        results.append(
            CheckResult("python_version", "fail", f"{py_str} (requires >=3.12)")
        )

    # Required files.
    for label, fp in [
        ("config.toml", paths.config_file),
        ("voice.md", paths.voice_file),
        ("style.md", paths.style_file),
        ("slop-blocklist.md", paths.slop_file),
    ]:
        if fp.is_file():
            results.append(CheckResult(label, "pass"))
        else:
            results.append(CheckResult(label, "fail", f"missing at {fp}"))

    # Config parses.
    if paths.config_file.is_file():
        try:
            cfg = ProjectConfig.load(paths.config_file)
            results.append(
                CheckResult("config_parses", "pass", f"project={cfg.project.name}")
            )
        except ConfigError as e:
            results.append(CheckResult("config_parses", "fail", str(e)))

    # State DB.
    sv = get_schema_version(paths.state_db)
    if sv is None:
        results.append(CheckResult("state_db", "fail", "missing or unreadable"))
    elif sv == SCHEMA_VERSION:
        results.append(CheckResult("state_db", "pass", f"schema v{sv}"))
    else:
        results.append(
            CheckResult(
                "state_db",
                "warn",
                f"schema v{sv}, current is v{SCHEMA_VERSION} — migration needed",
            )
        )

    # Required env.
    for name in REQUIRED_ENV:
        val = os.environ.get(name)
        if val:
            results.append(CheckResult(name, "pass", "set"))
        else:
            results.append(CheckResult(name, "fail", "not set (required)"))

    # Optional env.
    for name in OPTIONAL_ENV:
        val = os.environ.get(name)
        results.append(
            CheckResult(name, "pass" if val else "warn", "set" if val else "not set (optional)")
        )

    # KB freshness.
    if paths.kb_dir.is_dir():
        n = sum(1 for _ in paths.kb_dir.rglob("*.md"))
        results.append(
            CheckResult("kb_files", "pass" if n > 0 else "warn", f"{n} markdown files")
        )
    else:
        results.append(CheckResult("kb_files", "warn", "kb/ missing"))

    return results


def _overall(results: list[CheckResult]) -> str:
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "ok"


def _emit_pretty(results: list[CheckResult], overall: str) -> None:
    icons = {"pass": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}
    for r in results:
        console.print(f"  {icons[r.status]} {r.name:<24} {r.detail}")
    console.print()
    label = {"ok": "[bold green]All checks passed.[/bold green]",
             "warn": "[bold yellow]Some warnings; nothing blocking.[/bold yellow]",
             "fail": "[bold red]One or more checks failed.[/bold red]"}[overall]
    console.print(label)


def _emit_json(results: list[CheckResult], overall: str) -> None:
    typer.echo(
        json.dumps(
            {"status": overall, "checks": [asdict(r) for r in results]},
            indent=2,
        )
    )


def doctor_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of pretty output.",
    ),
) -> None:
    """Run health checks on the current project."""
    try:
        root = find_devrel_root()
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    paths = ProjectPaths.from_root(root)
    results = _run_checks(paths)
    overall = _overall(results)

    if json_output:
        _emit_json(results, overall)
    else:
        _emit_pretty(results, overall)

    if overall == "fail":
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Register `doctor` in the CLI app**

In `src/devrel_swarm/cli/__init__.py`, add an import and a registration line. Find:
```python
from devrel_swarm.cli.init import init_command
```
Add below it:
```python
from devrel_swarm.cli.doctor import doctor_command
```

Find:
```python
app.command(name="init")(init_command)
```
Add below it:
```python
app.command(name="doctor")(doctor_command)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
python -m pytest tests/cli/test_doctor_command.py -v --no-cov 2>&1 | tail -10
```
Expected: 4 passed.

- [ ] **Step 6: Smoke test from a fresh tmp dir**

```bash
TMPDIR_FOR_SMOKE=$(mktemp -d)
cd "$TMPDIR_FOR_SMOKE"
ANTHROPIC_API_KEY=sk-ant-test devrel init --non-interactive --name smoke --url "" --github-repo ""
ANTHROPIC_API_KEY=sk-ant-test devrel doctor
echo "exit=$?"
cd -
rm -rf "$TMPDIR_FOR_SMOKE"
```
Expected: init prints `Done.`, doctor prints all checks (some `!` warnings on optional env vars are expected), and `exit=0`.

- [ ] **Step 7: Commit**

```bash
git add src/devrel_swarm/cli/__init__.py src/devrel_swarm/cli/doctor.py tests/cli/test_doctor_command.py
git commit -m "feat(cli): add 'devrel doctor' health-check command"
```

---

## Task 9: Verify, document, finalize

**Files:**
- Modify: `CLAUDE.md` (mention new commands + paths)

- [ ] **Step 1: Run the entire test suite**

```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tee /tmp/pytest.phase2.after.txt | tail -10
grep "^FAILED" /tmp/pytest.phase2.after.txt | sort > /tmp/pytest.failures.phase2.after.txt
diff /tmp/pytest.failures.phase2.before.txt /tmp/pytest.failures.phase2.after.txt
```
Expected: the diff is empty (the same 22 known failures, no new ones). The summary line shows roughly **`592 passed, 22 failed`** (566 baseline + 26 new tests across paths/config/state/init/cli, exact count may vary by ±1 if you renamed any test).

If anything new fails, **stop and investigate** — Phase 2 must not regress Phase 1's behaviour.

- [ ] **Step 2: Verify coverage of new packages meets the 80% bar**

```bash
python -m pytest tests/project tests/cli --cov=devrel_swarm.project --cov=devrel_swarm.cli --cov-report=term-missing 2>&1 | tail -20
```
Expected: `TOTAL` line shows ≥80% for both `devrel_swarm.project` and `devrel_swarm.cli`. If either is below 80%, add tests for the uncovered branches before continuing.

- [ ] **Step 3: Update CLAUDE.md with the new commands**

In `CLAUDE.md`, find the `## Commands` section and **add** these lines under it (do not remove the existing ones):

```bash
# Bootstrap a project (Phase 2)
devrel init --name openclaw --url https://openclaw.ai --github-repo openclaw/openclaw

# Run project health checks
devrel doctor
devrel doctor --json
```

In `CLAUDE.md`'s File Map, add after the `src/devrel_swarm/tools/` block:

```
src/devrel_swarm/cli/      Typer app + per-command modules. Phase 2 ships
                           init.py + doctor.py; Phase 4 expands.
src/devrel_swarm/project/  Project bootstrap. paths.py walks cwd to find
                           .devrel/. config.py loads config.toml. state.py
                           manages SQLite state DB. init.py scaffolds
                           .devrel/ idempotently. templates/ holds the
                           starter content for voice.md, style.md,
                           slop-blocklist.md, config.toml, .gitignore.
```

- [ ] **Step 4: Final commit (docs)**

```bash
git add CLAUDE.md
git commit -m "docs: add Phase 2 commands and File Map entries"
```

- [ ] **Step 5: Verify final state**

```bash
git log --oneline main..HEAD
devrel --version
```
Expected: a stack of focused commits (one per Task) on `feat/cli-phase2-bootstrap`, and `devrel-swarm 0.2.0`.

---

## Self-review checklist (already applied)

- **Spec coverage:**
  - `project/paths.py` — Task 2.
  - `project/config.py` — Task 3.
  - `project/state.py` — Task 4.
  - `project/init.py` — Task 6 (with templates from Task 5).
  - `devrel init` command — Task 7.
  - `devrel doctor` command — Task 8.
  - `.devrel/` scaffold tested against a fixture repo — Tasks 6 (init test) + 7 (CLI test) + 8 (doctor test).
- **No placeholders:** every step has either explicit code or an explicit verification.
- **Type / name consistency:** `ProjectPaths`, `ProjectConfig`, `ModelConfig`, `BudgetConfig`, `InitOptions`, `InitResult`, `CheckResult` — all consistent across tasks.
- **Console-script entry-point:** added in Task 7 Step 3 alongside the first command using it.

## Out of scope (deferred to later phases)

- Other CLI verbs (run / triage / listen / content / sales / marketing / etc.) — Phase 4.
- The 8-stage quality pipeline that uses voice.md / style.md / slop-blocklist.md — Phase 3.
- Wiring agent cost-tracking to write into `costs` table — Phase 3.
- BudgetGate enforcement using the budget block from config.toml — Phase 3.
- Archiving the `product/v0-agentic-alpha` branch — Phase 5.
