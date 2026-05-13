"""
Autoresearch optimizer for all agents.

Runs the optimization loop across every agent's system prompt.
Each iteration: propose modification → eval → keep/revert.

Usage:
    PYTHONPATH=. python3 optimize/optimize_all.py                      # 5 iterations per agent
    PYTHONPATH=. python3 optimize/optimize_all.py --iterations 10      # 10 per agent
    PYTHONPATH=. python3 optimize/optimize_all.py --agent kai          # one agent only
    PYTHONPATH=. python3 optimize/optimize_all.py --agent pax          # uses existing pax optimizer
"""

import asyncio
import json
import logging
import os
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
logger = logging.getLogger("optimize_all")

OPTIMIZE_DIR = Path(__file__).parent
AGENTS_DIR = OPTIMIZE_DIR / "agents"

ALL_AGENTS = ["iris", "kai", "dex", "rex", "mox", "vox", "sage", "echo", "nova"]

OPTIMIZER_SYSTEM = """You are an AI prompt optimizer. Your job is to improve a system prompt \
used by an AI agent.

You will receive:
1. The agent's role and what it produces
2. The current system prompt
3. Evaluation scores from the last run (criteria breakdown)
4. History of past modifications

Your task: propose ONE specific, targeted modification that will improve the weakest criteria.

Rules:
- Make ONE change per iteration, not a full rewrite
- Keep the agent's identity and core role intact
- Preserve any placeholder variables like {{product_name}}
- Focus on the lowest-scoring evaluation criteria

Return JSON:
{{"change_description": "what you changed and why", "new_prompt": "the complete modified system prompt"}}"""


async def eval_agent_scores(agent_name: str) -> dict:
    """Run eval for one agent and return scores."""
    from devrel_origin.core.llm import LLMClient
    from optimize.agent_eval import AGENTS_DIR, SCORERS, generate_for_agent

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    llm = LLMClient(api_key=api_key)
    scorer = SCORERS[agent_name]

    tc_file = AGENTS_DIR / agent_name / "test_cases.json"
    test_cases = json.loads(tc_file.read_text())

    scores = []
    for tc in test_cases:
        output = await generate_for_agent(llm, agent_name, tc)
        sc = scorer(tc, output)
        scores.append({
            "test_case_id": sc.test_case_id,
            "total": sc.total,
            "criteria": sc.criteria,
            "notes": sc.notes,
        })

    await llm.close()
    avg = sum(s["total"] for s in scores) / len(scores) if scores else 0.0
    return {"average_score": round(avg, 2), "scores": scores}


async def propose_modification(agent_name: str, current_scores: dict, history: list) -> dict | None:
    """Ask LLM to propose a prompt modification."""
    from devrel_origin.core.base import strip_markdown_fences
    from devrel_origin.core.llm import LLMClient

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    llm = LLMClient(api_key=api_key)

    prompt_file = AGENTS_DIR / agent_name / "system_prompt.txt"
    current_prompt = prompt_file.read_text()

    agent_descriptions = {
        "iris": "Feedback Synthesizer — extracts themes from developer feedback, ranks pain points, maps developer journey",
        "kai": "Content Creator — writes tutorials, blog posts, changelogs, social posts grounded in knowledge base",
        "dex": "Documentation Generator — produces API references, architecture docs, module guides from source code",
        "rex": "Competitive Intelligence — competitor profiles, threat assessment, opportunity mapping",
        "mox": "Campaign Marketing — blog posts, landing pages, social batches, press releases, campaign briefs",
        "vox": "Video Producer — transforms written content into structured video scripts with narration and actions",
        "sage": "Community Manager — GitHub issue triage, priority classification, sentiment detection, churn risk, champion identification",
        "echo": "Social Media Listener — monitors Reddit/HN/Twitter for brand mentions, sentiment, engagement opportunities, reputation risks",
        "nova": "Growth Strategist — experiment design, funnel analysis, cohort segmentation, power analysis with statistical rigor",
    }

    history_text = ""
    if history:
        recent = history[-5:]
        history_text = "\n## Recent History\n"
        for h in recent:
            history_text += (
                f"- Iter {h['iteration']}: {h['change_description']} "
                f"→ {h['before']:.1f} → {h['after']:.1f} "
                f"({'KEPT' if h['kept'] else 'REVERTED'})\n"
            )

    user_prompt = f"""## Agent: {agent_name.upper()}
{agent_descriptions.get(agent_name, '')}

## Current System Prompt
```
{current_prompt}
```

## Current Scores (avg: {current_scores['average_score']}/100)
{json.dumps(current_scores['scores'], indent=2)}

{history_text}

Propose ONE modification to improve the weakest scoring criteria."""

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
        logger.error(f"Optimizer failed for {agent_name}: {exc}")
        await llm.close()
        return None


async def optimize_agent(agent_name: str, iterations: int = 5):
    """Run optimization loop for one agent."""
    prompt_file = AGENTS_DIR / agent_name / "system_prompt.txt"
    history_file = AGENTS_DIR / agent_name / "optimization_history.json"

    if not prompt_file.exists():
        print(f"  No system prompt found for {agent_name}")
        return

    history = json.loads(history_file.read_text()) if history_file.exists() else []
    iteration_start = len(history) + 1

    # Baseline
    print("  Running baseline evaluation...")
    baseline = await eval_agent_scores(agent_name)
    best_score = baseline["average_score"]
    print(f"  Baseline: {best_score}/100")

    for i in range(iterations):
        iteration = iteration_start + i
        print(f"\n  Iteration {iteration}:")

        # Propose
        modification = await propose_modification(agent_name, baseline, history)
        if not modification or not modification.get("new_prompt"):
            print("    Failed to get modification. Skipping.")
            continue

        print(f"    Change: {modification['change_description'][:80]}...")

        # Backup → Apply → Eval
        backup = prompt_file.read_text()
        prompt_file.write_text(modification["new_prompt"])

        new_scores = await eval_agent_scores(agent_name)
        new_avg = new_scores["average_score"]

        record = {
            "iteration": iteration,
            "change_description": modification["change_description"],
            "before": best_score,
            "after": new_avg,
            "kept": False,
            "timestamp": datetime.now().isoformat(),
        }

        if new_avg > best_score:
            print(f"    IMPROVED: {best_score:.1f} → {new_avg:.1f} (+{new_avg - best_score:.1f})")
            record["kept"] = True
            best_score = new_avg
            baseline = new_scores
        else:
            print(f"    NO IMPROVEMENT: {best_score:.1f} → {new_avg:.1f} ({new_avg - best_score:+.1f})")
            prompt_file.write_text(backup)

        history.append(record)
        history_file.write_text(json.dumps(history, indent=2, default=str))

    return best_score


async def main():
    iterations = 5
    target_agent = None

    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--iterations" and i + 2 <= len(sys.argv):
            iterations = int(sys.argv[i + 2])
        if arg == "--agent" and i + 2 <= len(sys.argv):
            target_agent = sys.argv[i + 2]

    # Handle pax separately (uses its own optimizer)
    if target_agent == "pax":
        print("Pax uses its own optimizer. Run:")
        print("  PYTHONPATH=. python3 optimize/run_optimizer.py")
        return

    agents = [target_agent] if target_agent else ALL_AGENTS

    print(f"\n{'=' * 60}")
    print("Multi-Agent Optimizer — Autoresearch Loop")
    print(f"  Agents: {', '.join(agents)}")
    print(f"  Iterations per agent: {iterations}")
    print(f"{'=' * 60}")

    results = {}
    for agent in agents:
        print(f"\n{'━' * 50}")
        print(f"  {agent.upper()}")
        print(f"{'━' * 50}")
        final = await optimize_agent(agent, iterations)
        if final is not None:
            results[agent] = final

    # Summary
    print(f"\n{'=' * 60}")
    print("Final Results:")
    print(f"{'=' * 60}")
    for agent, score in sorted(results.items(), key=lambda x: -x[1]):
        bar = "█" * int(score / 2) + "░" * (50 - int(score / 2))
        print(f"  {agent:6s} {bar} {score:.1f}")

    if results:
        overall = sum(results.values()) / len(results)
        print(f"\n  Overall: {overall:.1f}/100")


if __name__ == "__main__":
    asyncio.run(main())
