# devrel-origin CLI — Phase 8: Wave 3 Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the final ~13 polish items deferred from Phases 6 + 7. Every fix is XS or small S — caching repeated disk reads, naming magic numbers, surfacing previously-buried stderr, splitting overly-broad exception handlers, removing prompt artifacts that hardcode obsolete defaults. Nothing here is broken; nothing here changes behaviour for the user. The point is to leave the code in a state where the next round of feature work doesn't need to re-discover any of these papercuts.

**Scope:** Wave 3 only. After this phase the agent code review is fully addressed. Sage's "wire LLM into ambiguous-classification cases" is deliberately excluded — it's a design judgment call that warrants its own brainstorming, not a polish item.

**Architecture:** Surgical edits across 8 agent files. Most edits are 1-5 lines. No new modules. No tests added unless behavior changes (most don't); the standard verification is "full suite stays at parity."

**Tech Stack:** Python 3.12+ existing test infra. No new dependencies.

**Source review:** Phase 6 + Phase 7 plans' "Out of scope" sections.
**Phases 1-7 (prerequisites, all merged):** `be971bd`, `121187e`, `bfb3bb5`, `86c2747`, `24604c5`, `863f575`, `af4020f` on `main`.

---

## File coverage

```
src/devrel_origin/core/
  atlas.py       MODIFY   process_draft hardening + self-improvement except split
  echo.py        MODIFY   search_limit constructor parameter
  iris.py        MODIFY   remove hardcoded "sources" hint from extraction prompt
  sage.py        MODIFY   shared keyword constants block
  nova.py        MODIFY   scipy import to module-level + MDE-severity comment
  vox.py         MODIFY   stderr surfacing in desktop_recorder
  video/desktop_recorder.py    MODIFY   pipe stderr to PIPE + log on non-zero
  video/tts_engine.py          MODIFY   stream_to_file in run_in_executor
  kai.py / mox.py / pax.py / rex.py    MODIFY   cache SYSTEM_PROMPT at construction
```

---

## Pre-flight: worktree setup

- [ ] **Step 1: Create a fresh worktree**

Use **superpowers:using-git-worktrees** to create `.worktrees/cli-phase8-wave3` on a new branch `feat/cli-phase8-wave3`. Confirm `main` is at `af4020f` (Phase 7 merge) or later.

- [ ] **Step 2: Confirm baseline + lock test set**

```bash
cd /Users/macmini/devrel-origin/.worktrees/cli-phase8-wave3
/opt/homebrew/bin/python3.13 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -e '.[dev]' >/tmp/install.preflight.log 2>&1 && echo "exit=$?"
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | grep "^FAILED" | sort > /tmp/pytest.failures.phase8.before.txt
wc -l /tmp/pytest.failures.phase8.before.txt
```

Expected: `744 passed, 21 failed`, `21` lines.

---

## Task 1: Atlas — `process_draft` hardening + self-improvement except split

**Files:**
- Modify: `src/devrel_origin/core/atlas.py`

**Bugs:**
1. `process_draft` (around `atlas.py:800-809`) launches the `EDITOR` env var via a shell-interpolated command string. A malicious or unexpected `EDITOR` value can cause unintended commands to execute, and a missing `EDITOR` binary silently no-ops (the user thinks they edited the draft when they didn't).
2. The self-improvement block at `atlas.py:617-629` uses bare `except Exception` — `ImportError` (module not installed) and runtime errors are indistinguishable in logs.

- [ ] **Step 1: Replace shell-interpolated editor launch with a direct argv call**

In `src/devrel_origin/core/atlas.py`, find the `process_draft` function (around line 800-809). The current implementation reads `EDITOR` from the environment and passes a string to `os.system`. Rewrite it to:

1. Read `EDITOR` (default `"vi"`).
2. Resolve via `shutil.which(editor)` to confirm the binary exists; if `None`, log a warning and return without attempting to edit.
3. Launch with `subprocess.run([editor_path, str(tmp_path)], check=False)` — list-of-args avoids any shell interpretation.

Imports needed (add at the top of `atlas.py` if not already present):

```python
import shutil
import subprocess
```

The new function body should look roughly like:

```python
editor = os.environ.get("EDITOR", "vi")
editor_path = shutil.which(editor)
if editor_path is None:
    logger.warning("EDITOR=%s not found on PATH; skipping interactive edit", editor)
    return
subprocess.run([editor_path, str(tmp_path)], check=False)
```

(Adapt the surrounding code — the existing function may have additional logic before/after the editor launch.)

- [ ] **Step 2: Split the self-improvement `except`**

In `src/devrel_origin/core/atlas.py:617-629`, find the self-improvement block. Currently it imports a tool module and runs it inside a single `try`/`except Exception` that catches both the import failure and any runtime error. Split into two layers:

```python
try:
    from devrel_origin.tools.self_improve import run_self_improvement
except ImportError as e:
    logger.warning("Self-improvement module not available; skipping: %s", e)
else:
    try:
        await run_self_improvement(...)
    except Exception:
        logger.exception("Self-improvement step raised; continuing weekly cycle")
```

The `logger.exception` form emits the full traceback at ERROR level, which is what an operator needs to diagnose a real crash. The `ImportError` branch logs at WARNING because "tool not installed" is expected in stripped-down deployments.

- [ ] **Step 3: Verify**

```bash
source .venv/bin/activate
python -m pytest tests/test_atlas.py tests/test_atlas_replies.py -q --no-cov 2>&1 | tail -5
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
```

Expected: full suite at parity. No new failures.

- [ ] **Step 4: Commit**

```bash
git add src/devrel_origin/core/atlas.py
git commit -m "fix(atlas): subprocess.run for editor + split self-improvement ImportError vs Exception (Wave 3)"
```

---

## Task 2: Echo — `search_limit` constructor parameter

**Files:**
- Modify: `src/devrel_origin/core/echo.py`

**Bug:** `web_search(..., limit=20)` is hardcoded across every platform call (echo.py:241). For high-mention products, 20 caps the scan; for low-mention products, fewer would be cheaper.

- [ ] **Step 1: Add `search_limit` parameter**

In `src/devrel_origin/core/echo.py`, find the `Echo.__init__` method. Add a `search_limit: int = 20` parameter and store it on `self`:

```python
def __init__(
    self,
    *,
    llm_client=None,
    search_tools=None,
    product_name: str = "",
    search_limit: int = 20,
):
    # ... existing assignments
    self.search_limit = search_limit
```

Find the `web_search(query, limit=20)` calls (around line 241) and change each to:

```python
results = await self.search_tools.web_search(query, limit=self.search_limit)
```

- [ ] **Step 2: Verify**

```bash
python -m pytest tests/test_echo.py -q --no-cov 2>&1 | tail -5
```

Existing tests should pass — the default `search_limit=20` preserves the previous behaviour. If an existing test constructs `Echo(...)` and the new kwarg breaks it, that's a parameter-ordering issue — check the test fixture and reorder kwargs as needed.

- [ ] **Step 3: Commit**

```bash
git add src/devrel_origin/core/echo.py
git commit -m "feat(echo): expose search_limit as constructor parameter (Wave 3)"
```

---

## Task 3: Iris — remove hardcoded `"sources"` hint from extraction prompt

**Files:**
- Modify: `src/devrel_origin/core/iris.py`

**Bug:** The theme-extraction prompt at iris.py:298-314 instructs the LLM to return `["github"]` for the sources field ("always for now"). Multi-source signals (Discourse, support tickets passed via `synthesize_weekly`) get mislabeled as GitHub-only.

- [ ] **Step 1: Update the prompt**

In `src/devrel_origin/core/iris.py`, find `_extract_themes_from_chunk` (or wherever the chunk-extraction prompt lives, around line 298-314). Locate the line in the prompt that hardcodes a single source value with the "always for now" annotation. Replace it with an instruction asking the LLM to infer the sources from the signals it actually saw:

The principle: don't hardcode a value the LLM should infer. Update the prompt to say something like "list the sources observed in the signals you classified — typically a subset of github, discourse, twitter, etc." (preserve the existing prompt's overall tone and structure; only change the offending line).

- [ ] **Step 2: Verify**

```bash
python -m pytest tests/test_iris.py -q --no-cov 2>&1 | tail -5
```

If a test asserts that themes always have `sources == ["github"]`, that test was relying on the bug. Update it to assert `"github" in theme.sources` (i.e., GitHub is among the sources, not the only source).

- [ ] **Step 3: Commit**

```bash
git add src/devrel_origin/core/iris.py tests/test_iris.py
git commit -m "fix(iris): let LLM infer theme sources instead of hardcoding github (Wave 3)"
```

---

## Task 4: Sage — shared keyword constants module

**Files:**
- Modify: `src/devrel_origin/core/sage.py`

**Bug:** The keyword `"broken"` (and several others like `"frustrated"`, `"crash"`) is duplicated across `_analyze_sentiment` (line 244-258), `_categorize_issue` (line 258), and `_score_priority` (line 318). Future edits will silently diverge.

- [ ] **Step 1: Add module-level constants block**

In `src/devrel_origin/core/sage.py`, near the top of the file (after imports, before class definitions), add a constants block:

```python
# Shared keyword vocabularies for triage classification. Single source of
# truth — avoids divergence between sentiment, category, and priority logic.
CHURN_SIGNALS: tuple[str, ...] = (
    "switching to",
    "moved to",
    "considering alternatives",
    "nth time",
    # ... add the existing churn signals from _analyze_sentiment
)

FRUSTRATION_SIGNALS: tuple[str, ...] = (
    "broken",
    "useless",
    "frustrated",
    "wasted",
    "!!!",
    # ... add the existing frustration signals
)

BUG_KEYWORDS: tuple[str, ...] = (
    "broken",
    "error",
    "crash",
    "bug",
    "fail",
    # ... add the existing bug keywords from _categorize_issue
)

CRITICAL_KEYWORDS: tuple[str, ...] = (
    "broken",
    "down",
    "production",
    "outage",
    # ... add the existing critical keywords from _score_priority
)
```

(Inspect the existing methods for the exact lists before transcribing — don't paraphrase.)

- [ ] **Step 2: Replace inline keyword lists with the constants**

In `_analyze_sentiment`, `_categorize_issue`, `_score_priority`, and any other method that has an inline keyword list, replace the literal list with a reference to the appropriate module constant:

```python
# Before:
if any(kw in text_lower for kw in ("broken", "useless", "frustrated", "wasted")):
    ...

# After:
if any(kw in text_lower for kw in FRUSTRATION_SIGNALS):
    ...
```

The functions stay one-liners; the lists move to module level.

Note: `"broken"` legitimately appears in MULTIPLE constants (frustration AND bug AND critical). That's correct — the same word can signal multiple things. The point of the dedup is that `"broken"` is now defined ONCE per signal-type and referenced from each constant, not literal-copied across method bodies.

If the constants share a substantial common subset, factor that out:

```python
_CRASH_WORDS: tuple[str, ...] = ("broken", "crash", "down")
BUG_KEYWORDS: tuple[str, ...] = (*_CRASH_WORDS, "error", "bug", "fail")
CRITICAL_KEYWORDS: tuple[str, ...] = (*_CRASH_WORDS, "production", "outage")
```

- [ ] **Step 3: Verify**

```bash
python -m pytest tests/test_sage.py -q --no-cov 2>&1 | tail -5
```

Existing classification tests should pass — the behavior is unchanged, only the source of the keyword lists moved.

- [ ] **Step 4: Commit**

```bash
git add src/devrel_origin/core/sage.py
git commit -m "refactor(sage): extract shared keyword constants to module level (Wave 3)"
```

---

## Task 5: Nova — scipy import to module-level + MDE-severity comment

**Files:**
- Modify: `src/devrel_origin/core/nova.py`

**Bugs:**
1. `from scipy import stats` is deferred inside `calculate_sample_size` (nova.py:221). Missing-dependency error surfaces only at call time.
2. `mde = 0.03 if severity >= 7 else 0.05` has counterintuitive logic with no comment.

- [ ] **Step 1: Move scipy import to module-level**

In `src/devrel_origin/core/nova.py`, find the `from scipy import stats` line inside `calculate_sample_size` (around line 221). Delete it. At the top of the file with the other imports:

```python
from scipy import stats
```

Place it alphabetically with the existing third-party imports.

If `scipy` is genuinely optional (some deploys don't install it), use a conditional import pattern:

```python
try:
    from scipy import stats
except ImportError:
    stats = None  # type: ignore[assignment]
```

…and add an early check inside `calculate_sample_size`:

```python
if stats is None:
    raise ImportError(
        "scipy is required for power analysis. Install with: pip install scipy"
    )
```

The `pyproject.toml` already lists `scipy>=1.13.0` as a runtime dep, so the conditional shouldn't be needed in practice — but it's defensive coding. Pick whichever matches the codebase's existing style for optional deps.

- [ ] **Step 2: Document the MDE-severity logic**

In `src/devrel_origin/core/nova.py:150` (or wherever the MDE assignment lives), find:

```python
mde = 0.03 if severity >= 7 else 0.05
```

Add a comment immediately above it explaining the design choice:

```python
# High-severity themes warrant detecting a smaller lift (3% MDE) — the
# downside of missing a real improvement is large because the underlying
# pain is hurting users. Lower-severity themes accept a larger MDE (5%)
# to ship faster; if the experiment is inconclusive, the cost of being
# wrong is bounded.
mde = 0.03 if severity >= 7 else 0.05
```

If the actual intent was the opposite (high-severity → larger MDE for faster decisions), flip the assignment. The reviewer flagged this as ambiguous, not as definitively wrong. Preserve the existing behavior unless you have evidence the inversion is desired.

- [ ] **Step 3: Verify**

```bash
python -m pytest tests/test_nova.py -q --no-cov 2>&1 | tail -5
```

Expected: existing tests pass — no behavior change.

- [ ] **Step 4: Commit**

```bash
git add src/devrel_origin/core/nova.py
git commit -m "refactor(nova): scipy at module-level + document MDE-severity rationale (Wave 3)"
```

---

## Task 6: Vox — surface stderr + non-blocking TTS

**Files:**
- Modify: `src/devrel_origin/core/video/desktop_recorder.py`
- Modify: `src/devrel_origin/core/video/tts_engine.py`

**Bugs:**
1. Desktop recorder pipes stderr to `DEVNULL` (desktop_recorder.py:152-156) — silent failures appear as cryptic downstream errors.
2. `tts_engine.py:46` calls `response.stream_to_file()` synchronously inside an async method, blocking the event loop.

- [ ] **Step 1: Pipe desktop_recorder stderr**

In `src/devrel_origin/core/video/desktop_recorder.py:152-156`, find the FFmpeg subprocess construction. Currently:

```python
process = await asyncio.create_subprocess_exec(
    *cmd,
    stdout=asyncio.subprocess.DEVNULL,
    stderr=asyncio.subprocess.DEVNULL,
)
```

Change `stderr` to `PIPE` and check the result:

```python
process = await asyncio.create_subprocess_exec(
    *cmd,
    stdout=asyncio.subprocess.DEVNULL,
    stderr=asyncio.subprocess.PIPE,
)
# ... existing run logic, then:
_stdout, stderr = await process.communicate()
if process.returncode != 0:
    logger.error(
        "Desktop recorder FFmpeg failed (rc=%d). stderr:\n%s",
        process.returncode,
        stderr.decode(errors="replace") if stderr else "(no stderr captured)",
    )
    raise RuntimeError(f"Desktop recorder FFmpeg failed: rc={process.returncode}")
```

If the existing code already calls `await process.wait()` or similar without capturing stderr, restructure to use `communicate()` so the captured bytes are available for logging.

- [ ] **Step 2: Run `stream_to_file` in an executor**

In `src/devrel_origin/core/video/tts_engine.py:46`, find the `response.stream_to_file(...)` call. It's a synchronous OpenAI SDK method called inside an async function. Wrap it:

```python
import asyncio

# Before:
# response.stream_to_file(str(output_path))

# After:
loop = asyncio.get_event_loop()
await loop.run_in_executor(
    None,  # default executor (ThreadPoolExecutor)
    response.stream_to_file,
    str(output_path),
)
```

This frees the event loop for other coroutines while the audio file streams.

- [ ] **Step 3: Verify**

```bash
python -m pytest tests/test_vox.py -q --no-cov 2>&1 | tail -5
```

Tests for video sub-modules likely don't exercise these specific code paths (they're integration-level), so the verification is "no test fails."

- [ ] **Step 4: Commit**

```bash
git add src/devrel_origin/core/video/desktop_recorder.py src/devrel_origin/core/video/tts_engine.py
git commit -m "fix(vox): surface FFmpeg stderr + non-blocking TTS via run_in_executor (Wave 3)"
```

---

## Task 7: Cache `SYSTEM_PROMPT` at construction across content agents

**Files:**
- Modify: `src/devrel_origin/core/kai.py`
- Modify: `src/devrel_origin/core/mox.py`
- Modify: `src/devrel_origin/core/pax.py`
- Modify: `src/devrel_origin/core/rex.py`

**Bug:** Each of these four agents has a `SYSTEM_PROMPT` property that reads the prompt from disk via `load_agent_prompt()` on every `execute()` call. In a weekly cycle this is harmless; in a tight loop (e.g., bulk personalization in Pax) it's a stat+read per call with no semantic value beyond the first.

- [ ] **Step 1: Cache in Kai**

In `src/devrel_origin/core/kai.py`, find the `SYSTEM_PROMPT` property (around line 75-77) and the `__init__` method.

In `__init__`, after the existing setup, add:

```python
self._system_prompt = load_agent_prompt("kai", "system_prompt.txt", _DEFAULT_SYSTEM_PROMPT)
```

Replace the property with a simple attribute reference. If callers access `self.SYSTEM_PROMPT`, change them to `self._system_prompt`. If the property is referenced elsewhere via `Kai.SYSTEM_PROMPT` (class-level), keep a property as a thin wrapper:

```python
@property
def SYSTEM_PROMPT(self) -> str:
    return self._system_prompt
```

This way external callers don't break, but the disk read happens once at construction.

If the prompt incorporates `self.product_name` or other instance state via `.format()`, do the format inside `__init__` after loading:

```python
self._system_prompt = load_agent_prompt(
    "kai", "system_prompt.txt", _DEFAULT_SYSTEM_PROMPT
).format(product_name=self.product_name)
```

- [ ] **Step 2: Same change in Mox, Pax, Rex**

Apply the same pattern to:
- `src/devrel_origin/core/mox.py`
- `src/devrel_origin/core/pax.py`
- `src/devrel_origin/core/rex.py`

Each may have a slightly different prompt-loading method name (e.g., Pax uses its own `_load_prompt` after the Phase 7 migration). Adapt to match.

For Rex specifically, the prompt is constructed via `SYSTEM_PROMPT_TEMPLATE.format(product_name=self.product_name)` in `execute()` (rex.py:440 per the review). Move the format call into `__init__`.

- [ ] **Step 3: Verify**

```bash
python -m pytest tests/test_kai.py tests/test_mox.py tests/test_pax.py tests/test_rex.py -q --no-cov 2>&1 | tail -10
```

Expected: existing tests pass. Behavior is unchanged; only the timing of the disk read shifts from per-call to once-at-construction.

If any test asserts that `SYSTEM_PROMPT` reflects a specific value after the `optimize/<agent>/system_prompt.txt` file was modified mid-test, that test was relying on the per-call reload. Update it to construct a fresh agent instance after the file change.

- [ ] **Step 4: Commit**

```bash
git add src/devrel_origin/core/kai.py src/devrel_origin/core/mox.py \
        src/devrel_origin/core/pax.py src/devrel_origin/core/rex.py
git commit -m "perf(content): cache SYSTEM_PROMPT at construction across Kai/Mox/Pax/Rex (Wave 3)"
```

---

## Task 8: Verify, document, finalize

- [ ] **Step 1: Full suite + parity**

```bash
source .venv/bin/activate
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tee /tmp/pytest.phase8.after.txt | tail -10
diff /tmp/pytest.failures.phase8.before.txt <(grep "^FAILED" /tmp/pytest.phase8.after.txt | sort)
```

Expected: `744+ passed, ≤21 failed`. Diff: empty. Polish should not change behavior.

- [ ] **Step 2: Smoke test**

```bash
T=$(mktemp -d) && cd "$T"
ANTHROPIC_API_KEY=sk-ant-test devrel init --non-interactive --name probe --url https://probe.dev --github-repo probe/probe >/dev/null
ANTHROPIC_API_KEY=sk-ant-test devrel doctor 2>&1 | tail -5
echo "exit=$?"
cd - && rm -rf "$T"
```

- [ ] **Step 3: Update CHANGELOG.md**

Prepend a new section above the existing `## 0.2.2 — 2026-05-01`:

```markdown
## 0.2.3 — 2026-05-01

Wave 3 polish — final batch from the 2026-04-29 agent code review. No behavior changes; pure cleanup of papercuts the next round of feature work would otherwise re-discover.

### Changed

- **Atlas**: `process_draft` now uses `subprocess.run([editor, path])` with `shutil.which` validation — no shell-injection surface, "editor not found" surfaces as a log line rather than a silent no-op. Self-improvement step splits `ImportError` (module not installed) from generic `Exception` (module crashed) for diagnosable logs.
- **Echo**: `search_limit` exposed as constructor parameter (default 20) — projects with high mention volume can scan more deeply, projects with low volume can be cheaper.
- **Iris**: theme-extraction prompt no longer hardcodes a single source value. The LLM now infers sources from the signal list, so multi-source feedback (Discourse, support tickets, etc.) is correctly labeled.
- **Sage**: classification keyword vocabularies extracted to module-level constants (`CHURN_SIGNALS`, `FRUSTRATION_SIGNALS`, `BUG_KEYWORDS`, `CRITICAL_KEYWORDS`). Single source of truth — no more silent divergence between sentiment, category, and priority logic.
- **Nova**: `from scipy import stats` moved to module-level (was deferred inside `calculate_sample_size`). MDE-severity logic now has an explanatory comment.
- **Vox**: desktop recorder FFmpeg stderr piped to `PIPE` and logged on non-zero exit (was: discarded silently, failures showed up as cryptic downstream errors). TTS `stream_to_file()` runs in `loop.run_in_executor` so the event loop isn't blocked during audio streaming.

### Performance

- **Kai/Mox/Pax/Rex**: `SYSTEM_PROMPT` cached at construction (was: file re-read on every `execute()` call). Removes a per-call stat+read from the hot path; meaningful for bulk operations like Pax's per-contact personalization.
```

- [ ] **Step 4: Commit CHANGELOG**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG for v0.2.3 Wave 3 polish"
```

- [ ] **Step 5: Final state**

```bash
git log --oneline main..HEAD
.venv/bin/python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
```

Expected: 8 commits on the branch (7 task fixes + CHANGELOG).

---

## Self-review checklist (already applied)

- **Spec coverage:** every Wave 3 polish item from Phase 6 + Phase 7 plans' "Out of scope" sections is mapped to a task. The Sage "wire LLM into ambiguous-classification cases" judgment call is intentionally excluded.
- **No placeholders:** every fix specifies file:line, before/after intent, verification step.
- **Type / name consistency:** `CHURN_SIGNALS` / `FRUSTRATION_SIGNALS` / `BUG_KEYWORDS` / `CRITICAL_KEYWORDS` (Sage); `_system_prompt` (cached attribute used uniformly across Kai/Mox/Pax/Rex).
- **Reversibility:** every commit is independently reversible. No schema or behaviour changes.

## Out of scope (genuine post-Wave-3 items)

- **Sage LLM wiring decision** — judgment call: should Sage call the LLM for ambiguous classifications (no keyword match), or remain rule-only? Either path is defensible; deserves its own brainstorm.
- **22 → 21 → ? pre-existing test failures cleanup** — separate project. Phase 7 incidentally fixed one (Kai exception-path test); the remaining 21 are real test drift.
- **`devrel ask` natural-language router** — spec defers to v1.1.
- **BudgetGate cap enforcement** — currently records, doesn't enforce.
- **Atlas sub-cycle flags** (`--devrel | --sales | --marketing`).
- **PyPI publish** — release operation, not a phase.
