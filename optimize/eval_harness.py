"""
Fixed evaluation harness for Pax email quality.

Scores generated emails on 10 measurable criteria (0-100 total).
This file is NEVER modified by the optimizer — only the prompts change.

Usage:
    python3 optimize/eval_harness.py                    # evaluate current prompts
    python3 optimize/eval_harness.py --verbose          # show per-email breakdown
    python3 optimize/eval_harness.py --json             # output JSON for the optimizer
"""

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("eval_harness")

OPTIMIZE_DIR = Path(__file__).parent
PROJECT_DIR = OPTIMIZE_DIR.parent
PROMPT_FILE = OPTIMIZE_DIR / "email_prompt.txt"
SYSTEM_FILE = OPTIMIZE_DIR / "system_prompt.txt"
TEST_CASES_FILE = OPTIMIZE_DIR / "test_cases.json"

PRODUCT_NAME = os.getenv("PRODUCT_NAME", "OpenClaw")
SALES_CTA_URL = os.getenv("SALES_CTA_URL", "https://example.com/book")
SALES_REP_NAME = os.getenv("SALES_REP_NAME", "").lower()


@dataclass
class EmailScore:
    """Score breakdown for a single generated email."""
    test_case_id: str
    total: float = 0.0
    criteria: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    email_subject: str = ""
    email_body: str = ""
    parse_error: bool = False


def score_email(test_case: dict, email_data: dict | None) -> EmailScore:
    """Score a generated email against fixed criteria. Returns 0-100."""
    score = EmailScore(test_case_id=test_case["id"])

    if email_data is None or not isinstance(email_data, dict):
        score.parse_error = True
        score.notes.append("Failed to generate or parse email")
        return score

    subject = email_data.get("subject", "")
    body = email_data.get("body", "")
    pain_points = email_data.get("pain_points_addressed", [])

    score.email_subject = subject
    score.email_body = body

    # 1. Valid JSON output (10 pts)
    has_subject = bool(subject.strip())
    has_body = bool(body.strip())
    if has_subject and has_body:
        score.criteria["valid_json"] = 10
    else:
        score.criteria["valid_json"] = 0
        score.notes.append("Missing subject or body")

    # 2. Word count under 150 (10 pts)
    word_count = len(body.split())
    if word_count <= 150:
        score.criteria["word_count"] = 10
    elif word_count <= 180:
        score.criteria["word_count"] = 5
        score.notes.append(f"Slightly over 150 words ({word_count})")
    else:
        score.criteria["word_count"] = 0
        score.notes.append(f"Way over 150 words ({word_count})")

    # 3. Contains Calendly link (10 pts)
    if SALES_CTA_URL in body:
        score.criteria["calendly_link"] = 10
    elif "calendly" in body.lower():
        score.criteria["calendly_link"] = 5
        score.notes.append("Has calendly reference but wrong URL")
    else:
        score.criteria["calendly_link"] = 0
        score.notes.append("Missing Calendly link")

    # 4. Signed as the sales rep, not Pax/agent (10 pts)
    body_lower = body.lower()
    agent_names = {"pax", "mox", "kai", "rex", "sage", "echo", "iris", "nova", "vox", "dex", "sentinel", "atlas"}
    has_agent_name = any(name in body_lower for name in agent_names)
    if SALES_REP_NAME and SALES_REP_NAME in body_lower:
        if has_agent_name:
            score.criteria["signature"] = 3
            score.notes.append("Contains both rep name and agent name")
        else:
            score.criteria["signature"] = 10
    elif not SALES_REP_NAME:
        # No rep name configured — just check no agent names leak
        score.criteria["signature"] = 10 if not has_agent_name else 3
    else:
        score.criteria["signature"] = 0
        score.notes.append(f"Not signed as {SALES_REP_NAME}")

    # 5. Personalization — uses prospect's name or company (10 pts)
    first_name = test_case["first_name"].lower()
    company = test_case["company_name"].lower()
    has_name = first_name in body_lower
    has_company = company in body_lower
    if has_name and has_company:
        score.criteria["personalization"] = 10
    elif has_name or has_company:
        score.criteria["personalization"] = 6
    else:
        score.criteria["personalization"] = 0
        score.notes.append("No personalization (name or company)")

    # 6. Research hook used — references something from the hook (10 pts)
    hook = test_case.get("research_hook", "")
    if not hook:
        # No hook provided — check if email gracefully handles it
        score.criteria["research_hook"] = 7  # partial credit for handling gracefully
    else:
        # Check for key terms from the hook
        hook_words = set(hook.lower().split())
        # Filter out common words
        stop_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
                      "of", "is", "was", "are", "were", "been", "be", "have", "has", "had",
                      "do", "does", "did", "will", "would", "could", "should", "may", "might",
                      "about", "with", "from", "this", "that", "their", "she", "he", "her",
                      "his", "they", "it", "its", "as", "by", "not", "no", "so", "if", "than",
                      "how", "when", "where", "what", "who", "which", "each", "every", "all",
                      "any", "some", "most", "more", "less", "very", "just", "also", "only",
                      "recently", "vs."}
        hook_keywords = hook_words - stop_words
        body_words = set(body_lower.split())
        overlap = hook_keywords & body_words
        overlap_ratio = len(overlap) / max(len(hook_keywords), 1)

        if overlap_ratio >= 0.2:
            score.criteria["research_hook"] = 10
        elif overlap_ratio >= 0.1:
            score.criteria["research_hook"] = 5
        else:
            score.criteria["research_hook"] = 0
            score.notes.append("Research hook not reflected in email")

    # 7. No banned phrases (10 pts)
    banned = [
        "i hope this email finds you well",
        "i hope this finds you well",
        "just reaching out",
        "touching base",
        "circle back",
        "synergy",
        "game-changer",
        "revolutionize",
        "revolutionary",
        "disruptive",
        "cutting-edge",
        "best-in-class",
        "world-class",
        "leverage",
        "paradigm",
    ]
    found_banned = [b for b in banned if b in body_lower]
    if not found_banned:
        score.criteria["no_banned_phrases"] = 10
    else:
        deduction = min(10, len(found_banned) * 3)
        score.criteria["no_banned_phrases"] = max(0, 10 - deduction)
        score.notes.append(f"Banned phrases: {found_banned}")

    # 8. Has clear CTA (10 pts) — single action, not multiple asks
    cta_signals = ["book", "schedule", "grab", "pick a time", "calendly", "15 minutes",
                   "15-minute", "call", "chat", "meeting"]
    cta_count = sum(1 for s in cta_signals if s in body_lower)
    if 1 <= cta_count <= 4:
        score.criteria["clear_cta"] = 10
    elif cta_count > 4:
        score.criteria["clear_cta"] = 5
        score.notes.append("Too many CTA signals — feels pushy")
    else:
        score.criteria["clear_cta"] = 0
        score.notes.append("No clear CTA found")

    # 9. Pain points addressed (10 pts)
    if isinstance(pain_points, list) and len(pain_points) >= 1:
        score.criteria["pain_points"] = min(10, len(pain_points) * 4)
    else:
        score.criteria["pain_points"] = 0
        score.notes.append("No pain points listed")

    # 10. Subject line quality (10 pts)
    subj_words = len(subject.split())
    if 3 <= subj_words <= 10:
        score.criteria["subject_quality"] = 5
    elif subj_words > 0:
        score.criteria["subject_quality"] = 2
    else:
        score.criteria["subject_quality"] = 0

    # Subject should reference prospect or company
    if first_name in subject.lower() or company in subject.lower():
        score.criteria["subject_quality"] += 5
    elif any(w in subject.lower() for w in ["devrel", "developer", "community"]):
        score.criteria["subject_quality"] += 3

    score.criteria["subject_quality"] = min(10, score.criteria["subject_quality"])

    # Total
    score.total = sum(score.criteria.values())
    return score


async def generate_email(llm_client, system_prompt: str, email_prompt: str, test_case: dict) -> dict | None:
    """Generate an email using the current prompts and a test case."""
    from agents.base import strip_markdown_fences

    rendered = email_prompt.format(
        first_name=test_case["first_name"],
        last_name=test_case["last_name"],
        title=test_case["title"],
        company_name=test_case["company_name"],
        research_hook=test_case["research_hook"] or "No specific hook found — use title and company context.",
        kb_context=test_case["kb_context"],
        competitive_context=test_case["competitive_context"],
        product_name=PRODUCT_NAME,
    )

    try:
        raw = await llm_client.generate(
            system_prompt=system_prompt.format(product_name=PRODUCT_NAME),
            user_prompt=rendered,
            temperature=0.5,
            max_tokens=1024,
        )
        return json.loads(strip_markdown_fences(raw))
    except Exception as exc:
        logger.warning(f"Generation failed for {test_case['id']}: {exc}")
        return None


async def run_eval(verbose: bool = False, output_json: bool = False) -> float:
    """Run evaluation on all test cases. Returns average score 0-100."""
    from agents.llm import LLMClient

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    llm = LLMClient(api_key=api_key)

    # Load current prompts from files
    system_prompt = SYSTEM_FILE.read_text()
    email_prompt = PROMPT_FILE.read_text()

    # Load test cases
    test_cases = json.loads(TEST_CASES_FILE.read_text())

    scores: list[EmailScore] = []
    for tc in test_cases:
        email_data = await generate_email(llm, system_prompt, email_prompt, tc)
        sc = score_email(tc, email_data)
        scores.append(sc)

        if verbose:
            print(f"\n--- {tc['id']} ---")
            print(f"  Score: {sc.total}/100")
            for k, v in sc.criteria.items():
                print(f"    {k}: {v}/10")
            if sc.notes:
                print(f"  Notes: {'; '.join(sc.notes)}")
            if sc.email_subject:
                print(f"  Subject: {sc.email_subject}")

    avg = sum(s.total for s in scores) / len(scores) if scores else 0.0

    if output_json:
        result = {
            "average_score": round(avg, 2),
            "scores": [
                {
                    "test_case_id": s.test_case_id,
                    "total": s.total,
                    "criteria": s.criteria,
                    "notes": s.notes,
                    "subject": s.email_subject,
                }
                for s in scores
            ],
        }
        print(json.dumps(result, indent=2))
    elif not verbose:
        print(f"Average score: {avg:.1f}/100 across {len(scores)} test cases")

    await llm.close()
    return avg


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv
    output_json = "--json" in sys.argv
    asyncio.run(run_eval(verbose=verbose, output_json=output_json))
