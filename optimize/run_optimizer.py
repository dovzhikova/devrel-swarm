"""
Autoresearch-style optimizer for Pax email generation.

Based on Karpathy's pattern:
  1. Agent reads current prompt + eval scores
  2. Proposes a modification to the prompt
  3. Runs the fixed eval harness
  4. If score improves → commit; if not → revert
  5. Repeat

Usage:
    python3 optimize/run_optimizer.py                    # run 10 iterations
    python3 optimize/run_optimizer.py --iterations 20    # run 20 iterations
    python3 optimize/run_optimizer.py --target email     # optimize email prompt only
    python3 optimize/run_optimizer.py --target system    # optimize system prompt only
"""

import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("optimizer")

OPTIMIZE_DIR = Path(__file__).parent
PROMPT_FILE = OPTIMIZE_DIR / "email_prompt.txt"
SYSTEM_FILE = OPTIMIZE_DIR / "system_prompt.txt"
HISTORY_FILE = OPTIMIZE_DIR / "optimization_history.json"

OPTIMIZER_SYSTEM = """You are a cold email prompt optimizer. Your job is to improve \
a prompt template that generates personalized sales emails for OpenClaw.

You will receive:
1. The current prompt template
2. Evaluation scores from the last run (0-100 scale, broken down by criteria)
3. History of past modifications and their score impact

Your task: propose ONE specific modification to the prompt that will improve the \
overall score. Focus on the lowest-scoring criteria.

Rules:
- Make ONE targeted change per iteration, not a full rewrite
- Keep the core JSON output format intact: {{"subject": "...", "body": "...", "pain_points_addressed": [...], "sales_psychology": "..."}}
- Keep all placeholder variables: {first_name}, {last_name}, {title}, {company_name}, {research_hook}, {kb_context}, {competitive_context}, {product_name}, {sales_cta_url}
- The sales CTA URL must use the {{sales_cta_url}} placeholder
- The email must be signed as the product owner
- Preserve double braces {{}} around JSON keys in the prompt template

Return your response as JSON:
{{
  "target": "email" or "system",
  "change_description": "what you changed and why",
  "new_prompt": "the complete modified prompt text"
}}"""


async def get_current_scores() -> dict:
    """Run the eval harness and return JSON scores."""
    from optimize.eval_harness import run_eval, generate_email, score_email, PRODUCT_NAME
    from devrel_swarm.core.llm import LLMClient

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    llm = LLMClient(api_key=api_key)

    system_prompt = SYSTEM_FILE.read_text()
    email_prompt = PROMPT_FILE.read_text()
    test_cases = json.loads((OPTIMIZE_DIR / "test_cases.json").read_text())

    scores = []
    for tc in test_cases:
        email_data = await generate_email(llm, system_prompt, email_prompt, tc)
        sc = score_email(tc, email_data)
        scores.append({
            "test_case_id": sc.test_case_id,
            "total": sc.total,
            "criteria": sc.criteria,
            "notes": sc.notes,
        })

    await llm.close()

    avg = sum(s["total"] for s in scores) / len(scores) if scores else 0.0
    return {"average_score": round(avg, 2), "scores": scores}


def load_history() -> list[dict]:
    """Load optimization history."""
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return []


def save_history(history: list[dict]):
    """Save optimization history."""
    HISTORY_FILE.write_text(json.dumps(history, indent=2, default=str))


async def propose_modification(
    current_scores: dict,
    history: list[dict],
    target: str,
) -> dict | None:
    """Ask the LLM to propose a prompt modification."""
    from devrel_swarm.core.llm import LLMClient
    from devrel_swarm.core.base import strip_markdown_fences

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    llm = LLMClient(api_key=api_key)

    current_email_prompt = PROMPT_FILE.read_text()
    current_system_prompt = SYSTEM_FILE.read_text()

    # Build context for the optimizer
    history_summary = ""
    if history:
        recent = history[-5:]  # last 5 modifications
        history_summary = "\n## Recent Modification History\n"
        for h in recent:
            history_summary += (
                f"- Iteration {h['iteration']}: {h['change_description']} "
                f"→ score {h['before_score']:.1f} → {h['after_score']:.1f} "
                f"({'KEPT' if h['kept'] else 'REVERTED'})\n"
            )

    user_prompt = f"""## Current Email Prompt Template
```
{current_email_prompt}
```

## Current System Prompt
```
{current_system_prompt}
```

## Current Evaluation Scores (average: {current_scores['average_score']}/100)
{json.dumps(current_scores['scores'], indent=2)}

{history_summary}

## Target
You should modify the {"email" if target == "email" else "system"} prompt.
Focus on improving the lowest-scoring criteria across test cases.

Propose ONE specific modification. Return JSON with target, change_description, and new_prompt."""

    try:
        raw = await llm.generate(
            system_prompt=OPTIMIZER_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.7,
            max_tokens=4096,
        )
        await llm.close()
        return json.loads(strip_markdown_fences(raw))
    except Exception as exc:
        logger.error(f"Optimizer LLM call failed: {exc}")
        await llm.close()
        return None


async def run_optimizer(iterations: int = 10, target: str = "both"):
    """Run the optimization loop."""
    history = load_history()
    iteration_start = len(history) + 1

    print(f"\n{'=' * 60}")
    print(f"Pax Email Optimizer — Autoresearch Loop")
    print(f"{'=' * 60}")

    # Get baseline score
    print("\nRunning baseline evaluation...")
    baseline = await get_current_scores()
    best_score = baseline["average_score"]
    print(f"Baseline score: {best_score}/100")

    for i in range(iterations):
        iteration = iteration_start + i
        print(f"\n{'─' * 40}")
        print(f"Iteration {iteration}")
        print(f"{'─' * 40}")

        # Decide which prompt to optimize
        if target == "both":
            current_target = "email" if i % 2 == 0 else "system"
        else:
            current_target = target

        # 1. Propose modification
        print(f"  Proposing {current_target} prompt modification...")
        modification = await propose_modification(baseline, history, current_target)

        if not modification:
            print("  Failed to get modification. Skipping.")
            continue

        print(f"  Change: {modification.get('change_description', 'unknown')}")

        # 2. Backup current prompt
        prompt_file = PROMPT_FILE if modification.get("target") == "email" else SYSTEM_FILE
        backup = prompt_file.read_text()

        # 3. Apply modification
        new_prompt = modification.get("new_prompt", "")
        if not new_prompt:
            print("  Empty prompt returned. Skipping.")
            continue

        prompt_file.write_text(new_prompt)

        # 4. Evaluate
        print("  Evaluating modified prompt...")
        new_scores = await get_current_scores()
        new_avg = new_scores["average_score"]

        # 5. Keep or revert
        record = {
            "iteration": iteration,
            "target": modification.get("target", current_target),
            "change_description": modification.get("change_description", ""),
            "before_score": best_score,
            "after_score": new_avg,
            "kept": False,
            "timestamp": datetime.now().isoformat(),
        }

        if new_avg > best_score:
            print(f"  IMPROVED: {best_score:.1f} → {new_avg:.1f} (+{new_avg - best_score:.1f})")
            print(f"  Keeping modification.")
            record["kept"] = True
            best_score = new_avg
            baseline = new_scores
        else:
            print(f"  NO IMPROVEMENT: {best_score:.1f} → {new_avg:.1f} ({new_avg - best_score:+.1f})")
            print(f"  Reverting.")
            prompt_file.write_text(backup)

        history.append(record)
        save_history(history)

    # Final summary
    print(f"\n{'=' * 60}")
    print(f"Optimization Complete")
    print(f"{'=' * 60}")
    print(f"  Iterations: {iterations}")
    print(f"  Final score: {best_score}/100")
    print(f"  Improvements kept: {sum(1 for h in history[-iterations:] if h['kept'])}/{iterations}")

    # Show history of kept changes
    kept = [h for h in history if h["kept"]]
    if kept:
        print(f"\n  Kept modifications:")
        for h in kept[-5:]:
            print(f"    [{h['iteration']}] {h['change_description']}: {h['before_score']:.1f} → {h['after_score']:.1f}")


if __name__ == "__main__":
    iterations = 10
    target = "both"

    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--iterations" and i + 2 < len(sys.argv):
            iterations = int(sys.argv[i + 2])
        if arg == "--target" and i + 2 < len(sys.argv):
            target = sys.argv[i + 2]

    asyncio.run(run_optimizer(iterations=iterations, target=target))
