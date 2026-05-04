"""
Unified evaluation harness for all agents.

Each agent has its own scoring function based on what it produces.
This file is NEVER modified by the optimizer — only prompts change.

Usage:
    PYTHONPATH=. python3 optimize/agent_eval.py iris           # eval one agent
    PYTHONPATH=. python3 optimize/agent_eval.py all            # eval all agents
    PYTHONPATH=. python3 optimize/agent_eval.py kai --verbose  # verbose mode
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
logger = logging.getLogger("agent_eval")

OPTIMIZE_DIR = Path(__file__).parent
AGENTS_DIR = OPTIMIZE_DIR / "agents"


@dataclass
class EvalScore:
    test_case_id: str
    agent: str
    total: float = 0.0
    criteria: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    output_preview: str = ""
    parse_error: bool = False


# ---------------------------------------------------------------------------
# IRIS scoring
# ---------------------------------------------------------------------------
def score_iris(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="iris")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        score.parse_error = True
        score.notes.append("Invalid JSON")
        return score

    themes = data.get("themes", [])
    score.output_preview = json.dumps(themes[:1], indent=2)[:300]

    # 1. Valid JSON with themes array (15 pts)
    score.criteria["valid_structure"] = 15 if themes else 0

    # 2. Each theme has required fields (15 pts)
    required = {"theme_id", "title", "description", "frequency", "severity",
                "sources", "representative_quotes", "product_areas", "recommended_actions"}
    if themes:
        completeness = sum(1 for t in themes for f in required if f in t) / (len(themes) * len(required))
        score.criteria["field_completeness"] = round(completeness * 15)
    else:
        score.criteria["field_completeness"] = 0

    # 3. Themes backed by quotes (15 pts)
    has_quotes = sum(1 for t in themes if t.get("representative_quotes"))
    score.criteria["evidence_backed"] = min(15, round(has_quotes / max(len(themes), 1) * 15))

    # 4. Actionable recommendations (15 pts)
    has_actions = sum(1 for t in themes if t.get("recommended_actions"))
    score.criteria["actionable"] = min(15, round(has_actions / max(len(themes), 1) * 15))

    # 5. Severity scores are reasonable (10 pts)
    severities = [t.get("severity", 0) for t in themes if t.get("severity")]
    if severities and all(1 <= s <= 10 for s in severities):
        score.criteria["severity_range"] = 10
    elif severities:
        score.criteria["severity_range"] = 5
    else:
        score.criteria["severity_range"] = 0

    # 6. Journey stage mapping (10 pts)
    valid_stages = {"discovery", "evaluation", "onboarding", "integration", "scaling"}
    has_stage = sum(1 for t in themes if t.get("journey_stage", "").lower() in valid_stages)
    score.criteria["journey_mapped"] = min(10, round(has_stage / max(len(themes), 1) * 10))

    # 7. Deduplication — themes should be distinct (10 pts)
    titles = [t.get("title", "").lower() for t in themes]
    unique_ratio = len(set(titles)) / max(len(titles), 1)
    score.criteria["no_duplicates"] = round(unique_ratio * 10)

    # 8. Product areas identified (10 pts)
    has_areas = sum(1 for t in themes if t.get("product_areas"))
    score.criteria["product_areas"] = min(10, round(has_areas / max(len(themes), 1) * 10))

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# KAI scoring
# ---------------------------------------------------------------------------
def score_kai(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="kai")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    content = output
    content_type = test_case.get("content_type", "tutorial")
    score.output_preview = content[:300]

    # 1. Non-empty content (10 pts)
    score.criteria["has_content"] = 10 if len(content) > 100 else 0

    # 2. Word count appropriate for type (15 pts)
    words = len(content.split())
    targets = {
        "tutorial": (1500, 2500),
        "blog_post": (800, 1200),
        "changelog": (200, 400),
        "social": (10, 50),
    }
    low, high = targets.get(content_type, (100, 5000))
    if low <= words <= high:
        score.criteria["word_count"] = 15
    elif low * 0.7 <= words <= high * 1.3:
        score.criteria["word_count"] = 8
        score.notes.append(f"Word count {words} slightly outside {low}-{high}")
    else:
        score.criteria["word_count"] = 0
        score.notes.append(f"Word count {words} outside {low}-{high}")

    # 3. Has heading structure (15 pts)
    headings = re.findall(r'^#{1,3}\s+.+', content, re.MULTILINE)
    if content_type in ("tutorial", "blog_post"):
        if len(headings) >= 3:
            score.criteria["heading_structure"] = 15
        elif len(headings) >= 1:
            score.criteria["heading_structure"] = 8
        else:
            score.criteria["heading_structure"] = 0
            score.notes.append("Missing heading structure")
    else:
        score.criteria["heading_structure"] = 15  # N/A for short-form

    # 4. Code examples for tutorials (15 pts)
    code_blocks = re.findall(r'```[\s\S]*?```', content)
    if content_type == "tutorial":
        if len(code_blocks) >= 3:
            score.criteria["code_examples"] = 15
        elif len(code_blocks) >= 1:
            score.criteria["code_examples"] = 8
        else:
            score.criteria["code_examples"] = 0
            score.notes.append("Tutorial missing code examples")
    else:
        score.criteria["code_examples"] = 15 if code_blocks or content_type not in ("blog_post",) else 10

    # 5. No marketing fluff (10 pts)
    fluff = ["revolutionary", "game-changing", "cutting-edge", "best-in-class",
             "world-class", "next-generation", "paradigm"]
    found = [f for f in fluff if f in content.lower()]
    score.criteria["no_fluff"] = max(0, 10 - len(found) * 3)
    if found:
        score.notes.append(f"Fluff words: {found}")

    # 6. Actionable — has clear next steps or CTA (10 pts)
    action_signals = ["try it", "run", "install", "create", "deploy", "get started",
                      "learn more", "check out", "sign up"]
    has_action = any(s in content.lower() for s in action_signals)
    score.criteria["actionable"] = 10 if has_action else 3

    # 7. References knowledge base (10 pts)
    kb_ref_signals = ["knowledge base", "documentation", "docs", "reference",
                      "as described in", "according to"]
    has_kb_ref = any(s in content.lower() for s in kb_ref_signals)
    score.criteria["kb_grounded"] = 10 if has_kb_ref else 3

    # 8. Developer tone (15 pts)
    dev_signals = ["api", "config", "install", "deploy", "endpoint", "function",
                   "class", "import", "terminal", "command", "npm", "pip"]
    dev_count = sum(1 for s in dev_signals if s in content.lower())
    score.criteria["developer_tone"] = min(15, dev_count * 3)

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# DEX scoring
# ---------------------------------------------------------------------------
def score_dex(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="dex")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    content = output
    score.output_preview = content[:300]
    modules = test_case.get("modules", [])

    # 1. Non-empty documentation (15 pts)
    score.criteria["has_content"] = 15 if len(content) > 200 else 0

    # 2. Heading structure (15 pts)
    headings = re.findall(r'^#{1,3}\s+.+', content, re.MULTILINE)
    score.criteria["heading_structure"] = min(15, len(headings) * 3)

    # 3. Covers all modules mentioned (20 pts)
    covered = 0
    for mod in modules:
        for sym in mod.get("symbols", []):
            if sym["name"].lower() in content.lower():
                covered += 1
    total_symbols = sum(len(m.get("symbols", [])) for m in modules)
    score.criteria["symbol_coverage"] = round(covered / max(total_symbols, 1) * 20)

    # 4. Has code examples (15 pts)
    code_blocks = re.findall(r'```[\s\S]*?```', content)
    score.criteria["code_examples"] = min(15, len(code_blocks) * 5)

    # 5. Includes signatures/types (15 pts)
    type_signals = ["->", "def ", "class ", "str", "int", "bool", "list", "dict",
                    "None", "Optional", "async"]
    type_count = sum(1 for s in type_signals if s in content)
    score.criteria["type_accuracy"] = min(15, type_count * 2)

    # 6. No invented APIs (10 pts) — check if random function names appear
    score.criteria["no_hallucination"] = 10  # hard to check without real code; baseline

    # 7. Cross-references (10 pts)
    xref_signals = ["see also", "related:", "see `", "refer to"]
    has_xref = any(s in content.lower() for s in xref_signals)
    score.criteria["cross_references"] = 10 if has_xref else 3

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# REX scoring
# ---------------------------------------------------------------------------
def score_rex(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="rex")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    # Try parsing as JSON first, then treat as text
    try:
        data = json.loads(output)
        content = json.dumps(data)
    except json.JSONDecodeError:
        data = None
        content = output

    score.output_preview = content[:300]
    competitors = test_case.get("competitors", [])

    # 1. Covers all competitors (20 pts)
    covered = sum(1 for c in competitors if c.lower() in content.lower())
    score.criteria["competitor_coverage"] = round(covered / max(len(competitors), 1) * 20)

    # 2. Has strengths and weaknesses (15 pts)
    has_strengths = "strength" in content.lower() or "advantage" in content.lower()
    has_weaknesses = "weakness" in content.lower() or "disadvantage" in content.lower() or "limitation" in content.lower()
    score.criteria["swot_present"] = 15 if (has_strengths and has_weaknesses) else (8 if has_strengths or has_weaknesses else 0)

    # 3. Evidence-based (not speculation) (15 pts)
    evidence_signals = ["according to", "reported", "announced", "raised", "launched",
                        "github stars", "pricing", "revenue", "$"]
    evidence_count = sum(1 for s in evidence_signals if s in content.lower())
    score.criteria["evidence_based"] = min(15, evidence_count * 3)

    # 4. Threats identified (15 pts)
    threat_signals = ["threat", "risk", "danger", "concern", "vulnerability"]
    has_threats = any(s in content.lower() for s in threat_signals)
    score.criteria["threats_identified"] = 15 if has_threats else 0

    # 5. Opportunities identified (15 pts)
    opp_signals = ["opportunity", "gap", "advantage", "differentiat", "positioning"]
    has_opps = any(s in content.lower() for s in opp_signals)
    score.criteria["opportunities_identified"] = 15 if has_opps else 0

    # 6. Actionable recommendations (10 pts)
    action_signals = ["recommend", "should", "action", "respond", "counter", "strategy"]
    has_actions = any(s in content.lower() for s in action_signals)
    score.criteria["actionable"] = 10 if has_actions else 0

    # 7. No empty speculation (10 pts)
    speculation = ["probably", "might be", "could potentially", "it's possible that",
                   "one could argue"]
    found_spec = [s for s in speculation if s in content.lower()]
    score.criteria["no_speculation"] = max(0, 10 - len(found_spec) * 3)

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# MOX scoring
# ---------------------------------------------------------------------------
def score_mox(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="mox")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    content = output
    content_type = test_case.get("content_type", "blog")
    score.output_preview = content[:300]

    # 1. Non-empty output (10 pts)
    score.criteria["has_content"] = 10 if len(content) > 100 else 0

    # 2. Developer-authentic tone (15 pts)
    buzzwords = ["synergy", "paradigm", "leverage", "holistic", "disruptive",
                 "best-in-class", "world-class", "next-generation"]
    found_buzz = [b for b in buzzwords if b in content.lower()]
    score.criteria["dev_authentic"] = max(0, 15 - len(found_buzz) * 4)
    if found_buzz:
        score.notes.append(f"Buzzwords: {found_buzz}")

    # 3. Has CTA (10 pts)
    cta_signals = ["book a", "sign up", "try", "get started", "learn more",
                   "schedule", "download", "join"]
    has_cta = any(s in content.lower() for s in cta_signals)
    score.criteria["has_cta"] = 10 if has_cta else 0

    # 4. Pain-point driven (15 pts)
    pain_signals = ["pain", "challenge", "struggle", "frustrat", "problem",
                    "bottleneck", "costly", "time-consuming", "manual"]
    pain_count = sum(1 for s in pain_signals if s in content.lower())
    score.criteria["pain_driven"] = min(15, pain_count * 3)

    # 5. SEO structure for blogs (10 pts)
    if content_type == "blog":
        headings = re.findall(r'^#{1,3}\s+.+', content, re.MULTILINE)
        score.criteria["seo_structure"] = min(10, len(headings) * 2)
    else:
        score.criteria["seo_structure"] = 10  # N/A

    # 6. Competitive differentiation (10 pts)
    diff_signals = ["unlike", "compared to", "vs", "alternative", "instead of",
                    "differentiat", "better than"]
    has_diff = any(s in content.lower() for s in diff_signals)
    score.criteria["differentiated"] = 10 if has_diff else 3

    # 7. Data/numbers present (10 pts)
    numbers = re.findall(r'\$[\d,]+|\d+%|\d+x|\d+ hours?|\d+ minutes?', content)
    score.criteria["hard_data"] = min(10, len(numbers) * 3)

    # 8. Hormozi value equation elements (10 pts)
    value_signals = ["dream outcome", "risk reversal", "guarantee", "free",
                     "no commitment", "replace a", "save", "roi"]
    value_count = sum(1 for s in value_signals if s in content.lower())
    score.criteria["value_equation"] = min(10, value_count * 3)

    # 9. Platform-appropriate format (10 pts)
    if content_type == "social":
        # Twitter posts should be short
        lines = content.strip().split("\n")
        short_enough = all(len(l) <= 300 for l in lines if l.strip())
        score.criteria["platform_fit"] = 10 if short_enough else 5
    elif content_type == "press_release":
        has_headline = "headline" in content.lower() or content.startswith("#")
        has_quote = '"' in content or "said" in content.lower()
        score.criteria["platform_fit"] = 10 if (has_headline and has_quote) else 5
    else:
        score.criteria["platform_fit"] = 10  # N/A

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# VOX scoring
# ---------------------------------------------------------------------------
def score_vox(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="vox")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    # Vox returns a script, try parsing as JSON steps
    try:
        data = json.loads(output)
        steps = data.get("steps", data if isinstance(data, list) else [])
    except json.JSONDecodeError:
        steps = []
        # Try parsing from markdown
        step_matches = re.findall(r'## Step \d+|### Step \d+|Step \d+:', output)
        if step_matches:
            steps = [{"title": s} for s in step_matches]

    score.output_preview = (output or "")[:300]

    # 1. Has multiple steps (20 pts)
    score.criteria["has_steps"] = min(20, len(steps) * 5)

    # 2. Each step has narration (20 pts)
    has_narration = sum(1 for s in steps if isinstance(s, dict) and s.get("narration"))
    score.criteria["has_narration"] = min(20, round(has_narration / max(len(steps), 1) * 20))

    # 3. Each step has title (15 pts)
    has_title = sum(1 for s in steps if isinstance(s, dict) and s.get("title"))
    score.criteria["has_titles"] = min(15, round(has_title / max(len(steps), 1) * 15))

    # 4. Browser actions present (15 pts)
    has_actions = sum(1 for s in steps if isinstance(s, dict) and s.get("actions"))
    score.criteria["has_actions"] = min(15, round(has_actions / max(len(steps), 1) * 15))

    # 5. URLs present (15 pts)
    has_url = sum(1 for s in steps if isinstance(s, dict) and s.get("url"))
    score.criteria["has_urls"] = min(15, round(has_url / max(len(steps), 1) * 15))

    # 6. Concise narration (15 pts)
    if steps and all(isinstance(s, dict) for s in steps):
        narrations = [s.get("narration", "") for s in steps]
        avg_words = sum(len(n.split()) for n in narrations) / max(len(narrations), 1)
        if 10 <= avg_words <= 60:
            score.criteria["concise_narration"] = 15
        elif avg_words > 0:
            score.criteria["concise_narration"] = 7
        else:
            score.criteria["concise_narration"] = 0
    else:
        score.criteria["concise_narration"] = 0

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# SAGE scoring (community manager — issue triage)
# ---------------------------------------------------------------------------
def score_sage(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="sage")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        # Sage may return structured text instead of JSON
        data = None

    content = json.dumps(data) if data else output
    score.output_preview = content[:300]

    issue = test_case.get("issue", {})
    expected_priority = test_case.get("expected_priority", "").lower()
    expected_sentiment = test_case.get("expected_sentiment", "").lower()

    # 1. Priority classification (20 pts)
    if expected_priority and expected_priority in content.lower():
        score.criteria["priority_accuracy"] = 20
    elif any(p in content.lower() for p in ["critical", "high", "medium", "low"]):
        score.criteria["priority_accuracy"] = 8
        score.notes.append(f"Expected priority '{expected_priority}', got different")
    else:
        score.criteria["priority_accuracy"] = 0
        score.notes.append("No priority classification found")

    # 2. Sentiment detection (15 pts)
    if expected_sentiment and expected_sentiment in content.lower():
        score.criteria["sentiment_accuracy"] = 15
    elif any(s in content.lower() for s in ["positive", "negative", "neutral", "frustrated", "churning"]):
        score.criteria["sentiment_accuracy"] = 7
    else:
        score.criteria["sentiment_accuracy"] = 0

    # 3. Churn risk detection (15 pts)
    churn_signals = ["churn", "switching", "leaving", "cancel", "abandon", "at risk"]
    has_churn_mention = any(s in content.lower() for s in churn_signals)
    expected_churn = test_case.get("expected_churn_risk", False)
    if expected_churn and has_churn_mention:
        score.criteria["churn_detection"] = 15
    elif not expected_churn and not has_churn_mention:
        score.criteria["churn_detection"] = 15
    elif expected_churn and not has_churn_mention:
        score.criteria["churn_detection"] = 0
        score.notes.append("Missed churn risk signal")
    else:
        score.criteria["churn_detection"] = 5
        score.notes.append("False positive churn detection")

    # 4. Champion detection (10 pts)
    champion_signals = ["champion", "advocate", "contributor", "willing to help", "happy to"]
    has_champion = any(s in content.lower() for s in champion_signals)
    expected_champion = test_case.get("expected_champion", False)
    if expected_champion and has_champion:
        score.criteria["champion_detection"] = 10
    elif not expected_champion:
        score.criteria["champion_detection"] = 10  # N/A
    else:
        score.criteria["champion_detection"] = 3

    # 5. Actionable response suggestion (15 pts)
    action_signals = ["respond", "reply", "acknowledge", "escalat", "assign",
                      "investigate", "fix", "patch", "workaround", "follow up"]
    action_count = sum(1 for s in action_signals if s in content.lower())
    score.criteria["actionable_response"] = min(15, action_count * 3)

    # 6. Product area identification (10 pts)
    area_signals = ["orchestrat", "atlas", "mcp", "agent", "knowledge base",
                    "security", "log", "docs", "onboarding", "api", "prompt",
                    "shared context", "tool"]
    area_count = sum(1 for s in area_signals if s in content.lower())
    score.criteria["product_area"] = min(10, area_count * 3)

    # 7. Empathetic tone (appropriate for community manager) (15 pts)
    empathy_signals = ["understand", "frustrat", "sorry", "appreciate", "thank",
                       "concern", "impact", "experience", "hear you"]
    empathy_count = sum(1 for s in empathy_signals if s in content.lower())
    # For negative/churning sentiment, empathy matters more
    if expected_sentiment in ("negative", "frustrated", "churning"):
        score.criteria["empathetic_tone"] = min(15, empathy_count * 3)
    else:
        score.criteria["empathetic_tone"] = min(15, max(8, empathy_count * 3))

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# ECHO scoring (social media listener)
# ---------------------------------------------------------------------------
def score_echo(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="echo")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        data = None

    content = json.dumps(data) if data else output
    score.output_preview = content[:300]

    mention = test_case.get("mention", {})
    expected_sentiment = test_case.get("expected_sentiment", "").lower()
    expected_engagement = test_case.get("expected_engagement_opp", False)
    expected_risk = test_case.get("expected_risk", False)

    # 1. Sentiment classification (20 pts)
    if expected_sentiment and expected_sentiment in content.lower():
        score.criteria["sentiment_accuracy"] = 20
    elif any(s in content.lower() for s in ["positive", "negative", "neutral"]):
        score.criteria["sentiment_accuracy"] = 8
        score.notes.append(f"Expected '{expected_sentiment}', got different sentiment")
    else:
        score.criteria["sentiment_accuracy"] = 0

    # 2. Engagement opportunity detection (20 pts)
    engagement_signals = ["engag", "respond", "reply", "opportunity", "reach out",
                          "comment", "answer", "participate"]
    has_engagement = any(s in content.lower() for s in engagement_signals)
    if expected_engagement and has_engagement:
        score.criteria["engagement_detection"] = 20
    elif not expected_engagement and not has_engagement:
        score.criteria["engagement_detection"] = 20
    elif expected_engagement and not has_engagement:
        score.criteria["engagement_detection"] = 0
        score.notes.append("Missed engagement opportunity")
    else:
        score.criteria["engagement_detection"] = 8

    # 3. Risk detection (15 pts)
    risk_signals = ["risk", "reputation", "damage", "negative", "crisis",
                    "escalat", "monitor", "alert", "warning"]
    has_risk = any(s in content.lower() for s in risk_signals)
    if expected_risk and has_risk:
        score.criteria["risk_detection"] = 15
    elif not expected_risk and not has_risk:
        score.criteria["risk_detection"] = 15
    elif expected_risk and not has_risk:
        score.criteria["risk_detection"] = 0
        score.notes.append("Missed reputation risk")
    else:
        score.criteria["risk_detection"] = 5

    # 4. Platform awareness (10 pts)
    platform = mention.get("platform", "")
    platform_conventions = {
        "reddit": ["subreddit", "thread", "community", "rules", "helpful"],
        "hackernews": ["technical", "hn", "hacker news", "depth", "substantive"],
        "twitter": ["tweet", "thread", "concise", "hashtag", "engagement"],
    }
    conventions = platform_conventions.get(platform, [])
    conv_count = sum(1 for c in conventions if c in content.lower())
    score.criteria["platform_awareness"] = min(10, conv_count * 3)

    # 5. Actionable next steps (15 pts)
    action_signals = ["suggest", "recommend", "draft", "post", "respond with",
                      "template", "talking points", "key message"]
    action_count = sum(1 for s in action_signals if s in content.lower())
    score.criteria["actionable_steps"] = min(15, action_count * 3)

    # 6. Competitive context (10 pts)
    # Mentions of competitors in the post should be analyzed
    competitor_signals = ["competitor", "vs", "alternative", "compared to",
                          "orbit", "common room", "devrev", "chatwoot"]
    comp_count = sum(1 for s in competitor_signals if s in content.lower())
    has_competitors_in_mention = any(
        s in mention.get("body", "").lower()
        for s in ["vs", "orbit", "common room", "devrev", "alternative", "compared"]
    )
    if has_competitors_in_mention:
        score.criteria["competitive_context"] = min(10, comp_count * 3)
    else:
        score.criteria["competitive_context"] = 10  # N/A

    # 7. Engagement score awareness (10 pts)
    engagement_count = mention.get("engagement", 0)
    urgency_signals = ["high engagement", "moderate engagement", "low engagement",
                       "viral", "trending", "popular",
                       "widely shared", str(engagement_count)]
    urgency_count = sum(1 for s in urgency_signals if s in content.lower())
    score.criteria["reach_awareness"] = min(10, max(3, urgency_count * 4))

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# NOVA scoring (growth strategist)
# ---------------------------------------------------------------------------
def score_nova(test_case: dict, output: str | None) -> EvalScore:
    score = EvalScore(test_case_id=test_case["id"], agent="nova")
    if not output:
        score.parse_error = True
        score.notes.append("No output")
        return score

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        data = None

    content = json.dumps(data) if data else output
    score.output_preview = content[:300]

    context = test_case.get("context", {})
    task_type = test_case["id"]

    # 1. Statistical rigor (20 pts)
    stats_signals = ["sample size", "confidence", "significance", "p-value",
                     "power", "alpha", "beta", "MDE", "minimum detectable",
                     "standard deviation", "z-score", "statistical"]
    stats_count = sum(1 for s in stats_signals if s in content.lower())
    score.criteria["statistical_rigor"] = min(20, stats_count * 3)

    # 2. Hypothesis clarity (15 pts)
    hypothesis_signals = ["hypothesis", "h0", "h1", "null", "alternative",
                          "we expect", "will increase", "will decrease",
                          "will improve", "baseline", "target"]
    hyp_count = sum(1 for s in hypothesis_signals if s in content.lower())
    score.criteria["hypothesis_clarity"] = min(15, hyp_count * 3)

    # 3. Metric definition (15 pts)
    metric_signals = ["metric", "kpi", "conversion rate", "activation",
                      "retention", "funnel", "rate", "percentage", "%"]
    metric_count = sum(1 for s in metric_signals if s in content.lower())
    score.criteria["metric_definition"] = min(15, metric_count * 2)

    # 4. Uses real numbers from context (15 pts)
    numbers_from_context = []
    if "baseline_rate" in context:
        numbers_from_context.append(str(context["baseline_rate"]))
    if "d7_retention" in context:
        numbers_from_context.append(str(context["d7_retention"]))
    if "d30_retention" in context:
        numbers_from_context.append(str(context["d30_retention"]))
    if "funnel_stages" in context:
        for stage in context["funnel_stages"]:
            numbers_from_context.append(str(stage["count"]))
    found_numbers = sum(1 for n in numbers_from_context if n in content)
    score.criteria["uses_context_data"] = min(15, round(found_numbers / max(len(numbers_from_context), 1) * 15))

    # 5. Guardrail metrics (10 pts)
    guardrail_signals = ["guardrail", "guard rail", "secondary metric",
                         "counter metric", "regression", "side effect",
                         "unintended", "monitor"]
    has_guardrail = any(s in content.lower() for s in guardrail_signals)
    score.criteria["guardrails"] = 10 if has_guardrail else 0

    # 6. Segmentation / cohort thinking (10 pts)
    segment_signals = ["segment", "cohort", "group", "cluster", "dimension",
                       "channel_type", "llm_provider", "signup_source"]
    seg_count = sum(1 for s in segment_signals if s in content.lower())
    score.criteria["segmentation"] = min(10, seg_count * 2)

    # 7. Actionable next steps (15 pts)
    action_signals = ["implement", "run", "measure", "monitor", "deploy",
                      "test", "evaluate", "analyze", "report", "ship",
                      "recommend", "step 1", "step 2", "phase"]
    action_count = sum(1 for s in action_signals if s in content.lower())
    score.criteria["actionable"] = min(15, action_count * 2)

    score.total = sum(score.criteria.values())
    return score


# ---------------------------------------------------------------------------
# Agent-specific generation functions
# ---------------------------------------------------------------------------
SCORERS = {
    "iris": score_iris,
    "kai": score_kai,
    "dex": score_dex,
    "rex": score_rex,
    "mox": score_mox,
    "vox": score_vox,
    "sage": score_sage,
    "echo": score_echo,
    "nova": score_nova,
}


async def generate_for_agent(llm_client, agent_name: str, test_case: dict) -> str | None:
    """Generate output for one agent + test case."""
    from devrel_swarm.core.base import strip_markdown_fences

    agent_dir = AGENTS_DIR / agent_name
    system_file = agent_dir / "system_prompt.txt"
    if not system_file.exists():
        logger.error(f"No system prompt for {agent_name}")
        return None

    system_prompt = system_file.read_text()
    product_name = test_case.get("product_name", "OpenClaw")

    # Format system prompt if it has placeholders
    try:
        system_prompt = system_prompt.format(product_name=product_name)
    except (KeyError, IndexError):
        pass

    # Build user prompt based on agent
    if agent_name == "iris":
        signals_text = "\n".join(
            f"- [{s['labels'][0] if s.get('labels') else 'other'}] {s['title']}: {s['body']}"
            for s in test_case.get("signals", [])
        )
        user_prompt = (
            f"Task: {test_case['task']}\n\n"
            f"## GitHub Issues\n{signals_text}\n\n"
            "Analyze these signals and extract feedback themes.\n"
            "Return a JSON object with a \"themes\" array. Each theme has:\n"
            "- theme_id, title, description, frequency, severity (1-10), sources,\n"
            "  representative_quotes, product_areas, recommended_actions, journey_stage\n"
            "Return ONLY valid JSON, no markdown fences."
        )
    elif agent_name == "kai":
        user_prompt = (
            f"Task: {test_case['task']}\n\n"
            f"Content type: {test_case.get('content_type', 'tutorial')}\n\n"
            f"## Knowledge Base Context\n{test_case.get('kb_context', '')}\n\n"
            f"Write the {test_case.get('content_type', 'content')} now."
        )
    elif agent_name == "dex":
        modules_text = ""
        for mod in test_case.get("modules", []):
            modules_text += f"\n### {mod['path']}\n"
            for sym in mod.get("symbols", []):
                modules_text += f"- {sym['kind']}: {sym['signature']}\n  {sym['docstring']}\n"
        user_prompt = (
            f"Task: {test_case['task']}\n\n"
            f"## Source Code Analysis\n{modules_text}\n\n"
            "Generate the documentation in Markdown."
        )
    elif agent_name == "rex":
        intel_text = "\n".join(
            f"- {comp}: {intel}"
            for comp, intel in test_case.get("web_intel", {}).items()
        )
        user_prompt = (
            f"Task: {test_case['task']}\n\n"
            f"## Competitors: {', '.join(test_case.get('competitors', []))}\n\n"
            f"## Knowledge Base\n{test_case.get('kb_context', '')}\n\n"
            f"## Web Intelligence\n{intel_text}\n\n"
            "Produce a competitive intelligence report. Return JSON with:\n"
            "profiles, threats, opportunities, recommended_responses."
        )
    elif agent_name == "mox":
        user_prompt = (
            f"Task: {test_case['task']}\n\n"
            f"Content type: {test_case.get('content_type', 'blog')}\n\n"
            f"## Knowledge Base\n{test_case.get('kb_context', '')}\n\n"
            f"## Competitive Context\n{test_case.get('competitive_context', '')}\n\n"
            f"Generate the {test_case.get('content_type', 'content')} now."
        )
    elif agent_name == "vox":
        user_prompt = (
            f"Task: {test_case['task']}\n\n"
            f"## Input Content\n{test_case.get('input_content', '')}\n\n"
            "Parse this into a structured video script. Return JSON:\n"
            '{{"steps": [{{"step_number": N, "title": "...", "narration": "...", '
            '"url": "...", "actions": ["click X", "type Y"], "overlay_text": "..."}}]}}'
        )
    elif agent_name == "sage":
        issue = test_case.get("issue", {})
        labels_text = ", ".join(issue.get("labels", []))
        task_desc = test_case.get("task", "Triage this GitHub issue")
        user_prompt = (
            f"Task: {task_desc}\n\n"
            f"## GitHub Issue #{issue.get('number', 0)}\n"
            f"**Title:** {issue.get('title', '')}\n"
            f"**Author:** {issue.get('author', '')}\n"
            f"**Labels:** {labels_text}\n\n"
            f"**Body:**\n{issue.get('body', '')}\n\n"
            "Triage this issue. Provide:\n"
            "- Priority classification (critical/high/medium/low)\n"
            "- Sentiment analysis (positive/neutral/negative/frustrated/churning)\n"
            "- Retention concern assessment\n"
            "- Whether the author could be a community champion\n"
            "- Suggested response approach\n"
            "- Product area affected\n"
            "Return as JSON with fields: priority, sentiment, retention_concern, "
            "champion_potential, suggested_response, product_area, reasoning."
        )
    elif agent_name == "echo":
        mention = test_case.get("mention", {})
        task_desc = test_case.get("task", "Analyze this social mention")
        user_prompt = (
            f"Task: {task_desc}\n\n"
            f"## Social Mention\n"
            f"**Platform:** {mention.get('platform', '')}\n"
            f"**Subreddit:** {mention.get('subreddit', 'N/A')}\n"
            f"**Title:** {mention.get('title', '')}\n"
            f"**Author:** {mention.get('author', '')}\n"
            f"**Engagement:** {mention.get('engagement', 0)}\n"
            f"**Posted:** {mention.get('posted_at', '')}\n\n"
            f"**Body:**\n{mention.get('body', '')}\n\n"
            "Analyze this mention. Provide:\n"
            "- Sentiment classification (positive/neutral/negative)\n"
            "- Whether this is an engagement opportunity (and why)\n"
            "- Concern level assessment\n"
            "- Platform-appropriate response strategy\n"
            "- Competitive context if relevant\n"
            "Return as JSON with fields: sentiment, engagement_opportunity, "
            "concern_level, response_strategy, competitive_notes, recommended_action."
        )
    elif agent_name == "nova":
        context = test_case.get("context", {})
        context_text = json.dumps(context, indent=2)
        user_prompt = (
            f"Task: {test_case['task']}\n\n"
            f"## Context Data\n```json\n{context_text}\n```\n\n"
            "Provide a rigorous analysis following growth principles:\n"
            "- Pre-register hypothesis and metrics\n"
            "- Include power analysis or sample size calculation where applicable\n"
            "- Define guardrail metrics\n"
            "- Segment by relevant dimensions\n"
            "- Provide actionable next steps\n"
            "Use the specific numbers from the context data."
        )
    else:
        user_prompt = f"Task: {test_case['task']}"

    try:
        raw = await llm_client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=4096,
        )
        return strip_markdown_fences(raw)
    except Exception as exc:
        logger.warning(f"Generation failed for {agent_name}/{test_case['id']}: {exc}")
        return None


async def eval_agent(agent_name: str, verbose: bool = False) -> float:
    """Evaluate one agent. Returns average score 0-100."""
    from devrel_swarm.core.llm import LLMClient

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set")
        return 0.0

    llm = LLMClient(api_key=api_key)
    scorer = SCORERS.get(agent_name)
    if not scorer:
        print(f"No scorer for agent: {agent_name}")
        return 0.0

    tc_file = AGENTS_DIR / agent_name / "test_cases.json"
    if not tc_file.exists():
        print(f"No test cases for agent: {agent_name}")
        return 0.0

    test_cases = json.loads(tc_file.read_text())
    scores: list[EvalScore] = []

    for tc in test_cases:
        output = await generate_for_agent(llm, agent_name, tc)
        sc = scorer(tc, output)
        scores.append(sc)

        if verbose:
            print(f"\n  --- {tc['id']} ---")
            print(f"    Score: {sc.total}/100")
            for k, v in sc.criteria.items():
                max_pts = {
                    "iris": {"valid_structure": 15, "field_completeness": 15, "evidence_backed": 15,
                             "actionable": 15, "severity_range": 10, "journey_mapped": 10,
                             "no_duplicates": 10, "product_areas": 10},
                    "kai": {"has_content": 10, "word_count": 15, "heading_structure": 15,
                            "code_examples": 15, "no_fluff": 10, "actionable": 10,
                            "kb_grounded": 10, "developer_tone": 15},
                }.get(agent_name, {})
                mx = max_pts.get(k, "?")
                print(f"      {k}: {v}/{mx}")
            if sc.notes:
                print(f"    Notes: {'; '.join(sc.notes)}")

    avg = sum(s.total for s in scores) / len(scores) if scores else 0.0
    await llm.close()
    return avg


async def main():
    agents_to_eval = sys.argv[1] if len(sys.argv) > 1 else "all"
    verbose = "--verbose" in sys.argv

    all_agents = ["iris", "kai", "dex", "rex", "mox", "vox", "sage", "echo", "nova"]

    if agents_to_eval == "all":
        targets = all_agents
    else:
        targets = [agents_to_eval]

    print(f"\n{'=' * 60}")
    print("Agent Evaluation Harness")
    print(f"{'=' * 60}")

    results = {}
    for agent in targets:
        print(f"\n--- {agent.upper()} ---")
        avg = await eval_agent(agent, verbose=verbose)
        results[agent] = avg
        print(f"  Average: {avg:.1f}/100")

    if len(results) > 1:
        print(f"\n{'=' * 60}")
        print("Summary:")
        for agent, avg in sorted(results.items(), key=lambda x: -x[1]):
            bar = "█" * int(avg / 2) + "░" * (50 - int(avg / 2))
            print(f"  {agent:6s} {bar} {avg:.1f}")
        overall = sum(results.values()) / len(results)
        print(f"\n  Overall: {overall:.1f}/100")


if __name__ == "__main__":
    asyncio.run(main())
