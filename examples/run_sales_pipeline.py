"""
Run the sales pipeline (Rex → Pax → Mox) for OpenClaw.

Usage:
    python3 run_sales_pipeline.py

Requires ANTHROPIC_API_KEY in .env.
Output saved to deliverables/sales_pipeline_<timestamp>.json
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("sales_pipeline")

PRODUCT_NAME = "OpenClaw"
KB_PATH = Path(__file__).parent / "knowledge_base"
OUTPUT_DIR = Path(__file__).parent / "deliverables"


async def main():
    from devrel_swarm.core.llm import LLMClient
    from devrel_swarm.core.mox import Mox
    from devrel_swarm.core.pax import Pax
    from devrel_swarm.core.rex import Rex
    from devrel_swarm.tools.api_client import PostHogClient
    from devrel_swarm.tools.search_tools import SearchTools

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        print("Error: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    # Lightweight clients (no real PostHog/Instantly needed for content generation)
    api_client = PostHogClient(api_key="", project_id="")
    llm_client = LLMClient(api_key=anthropic_key)
    search_tools = SearchTools(
        firecrawl_api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
        brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
    )

    # Optional: Apollo client for real enrichment
    apollo_client = None
    apollo_key = os.environ.get("APOLLO_API_KEY")
    if apollo_key:
        from devrel_swarm.tools.apollo_client import ApolloClient
        apollo_client = ApolloClient(api_key=apollo_key)
        logger.info("Apollo client configured")

    # Optional: Instantly client for email campaign management
    instantly_client = None
    instantly_key = os.environ.get("INSTANTLY_API_KEY")
    if instantly_key:
        from devrel_swarm.tools.instantly_client import InstantlyClient
        instantly_client = InstantlyClient(api_key=instantly_key)
        logger.info("Instantly client configured")

    # Initialize agents with product_name="OpenClaw"
    rex = Rex(
        api_client=api_client,
        knowledge_base_path=KB_PATH,
        llm_client=llm_client,
        search_tools=search_tools,
        apollo_client=apollo_client,
        product_name=PRODUCT_NAME,
    )
    pax = Pax(
        api_client=api_client,
        knowledge_base_path=KB_PATH,
        llm_client=llm_client,
        instantly_client=instantly_client,
        apollo_client=apollo_client,
        search_tools=search_tools,
        product_name=PRODUCT_NAME,
    )
    mox = Mox(
        api_client=api_client,
        knowledge_base_path=KB_PATH,
        llm_client=llm_client,
        search_tools=search_tools,
        product_name=PRODUCT_NAME,
    )

    results = {}
    context = {}

    try:
        # ── Stage 1: Rex — Competitive Intelligence ──────────────────────
        print("\n" + "=" * 60)
        print("Stage 1: Rex — Competitive Intelligence")
        print("=" * 60)

        rex_result = await rex.execute(
            task=(
                "Analyze the competitive landscape for OpenClaw, an open-source "
                "multi-agent system that replaces an entire DevRel + Sales team with 10 "
                "specialized AI agents (community management, competitive intelligence, "
                "content creation, sales enablement, campaign marketing). Identify the "
                "top 5 competitors, assess threats and opportunities, and highlight "
                "OpenClaw' differentiators — hub-and-spoke orchestration, "
                "knowledge-base retargeting, and full pipeline automation."
            ),
            context=context,
        )
        results["rex"] = rex_result
        context["rex_competitive"] = rex_result

        print(f"\nRex completed: {rex_result.get('status', 'unknown')}")
        if "competitors_discovered" in rex_result:
            print(f"  Competitors found: {len(rex_result['competitors_discovered'])}")
        if "enriched_profiles" in rex_result:
            print(f"  Enriched profiles: {len(rex_result['enriched_profiles'])}")

        # ── Stage 2: Pax — Prospect + Personalize ─────────────────────
        print("\n" + "=" * 60)
        print("Stage 2: Pax — Prospect + Personalize")
        print("=" * 60)

        pax_personalized = await pax.execute(
            task=(
                "Find 15 leads and personalize outreach: Head of Developer Relations "
                "or VP Developer Experience at Series B-C DevTools companies with "
                "50-500 employees. Position OpenClaw as the force multiplier "
                "that replaces a 10-person team with 10 specialized AI agents."
            ),
            context=context,
        )
        results["pax_personalized"] = pax_personalized
        context["pax_personalized"] = pax_personalized

        print(f"\nPersonalized outreach: {pax_personalized.get('status', 'unknown')}")
        print(f"  Contacts found: {pax_personalized.get('contacts_found', 0)}")
        print(f"  Emails generated: {pax_personalized.get('emails_generated', 0)}")
        print(f"  Research hooks found: {pax_personalized.get('hooks_found', 0)}")
        if pax_personalized.get("uploaded_to_instantly"):
            print(f"  Uploaded to Instantly: {pax_personalized['uploaded_to_instantly']}")
        if pax_personalized.get("campaign_id"):
            print(f"  Instantly campaign: {pax_personalized['campaign_id']}")

        # ── Stage 3: Pax — Sales Assets ──────────────────────────────────
        print("\n" + "=" * 60)
        print("Stage 3a: Pax — Battle Card")
        print("=" * 60)

        pax_battlecard = await pax.execute(
            task=(
                "Create a battle card comparing OpenClaw vs the top competitor "
                "identified by Rex. Include feature comparison (10 specialized agents, "
                "hub-and-spoke orchestration, knowledge-base retargeting, MCP tools, "
                "Apollo/Instantly integrations), objection handling, and win themes "
                "for selling to DevRel leaders and VPs of Developer Experience."
            ),
            context=context,
        )
        results["pax_battlecard"] = pax_battlecard
        print(f"\nBattle card: {pax_battlecard.get('status', 'unknown')}")

        print("\n" + "=" * 60)
        print("Stage 3b: Pax — Outreach Email")
        print("=" * 60)

        pax_outreach = await pax.execute(
            task=(
                "Write an outreach email to a Head of Developer Relations at a "
                "Series B DevTools company who is struggling to scale content "
                "production and community management with a small team. Position "
                "OpenClaw as the force multiplier — 10 specialized agents "
                "that handle GitHub triage, social listening, competitive intel, "
                "content creation, and outbound sales automatically."
            ),
            context=context,
        )
        results["pax_outreach"] = pax_outreach
        print(f"\nOutreach email: {pax_outreach.get('status', 'unknown')}")

        # ── Stage 4: Mox — Campaign Marketing ────────────────────────────
        print("\n" + "=" * 60)
        print("Stage 4a: Mox — SEO Blog Post")
        print("=" * 60)

        mox_blog = await mox.execute(
            task=(
                "Write an SEO blog post titled 'How AI Agents Are Replacing "
                "the Traditional DevRel Team (And Why That's a Good Thing)'. "
                "Target keywords: AI DevRel agents, automated developer advocacy, "
                "AI-powered community management. Address the reality that most "
                "DevTools startups can't afford a 10-person DevRel team, and show "
                "how OpenClaw fills the gap with specialized agents for "
                "triage, content, competitive intel, and outbound."
            ),
            context=context,
        )
        results["mox_blog"] = mox_blog
        print(f"\nBlog post: {mox_blog.get('status', 'unknown')}")
        if "code_validation" in mox_blog:
            cv = mox_blog["code_validation"]
            print(f"  Code blocks: {cv['total_blocks']} total, {cv['passed']} passed")

        print("\n" + "=" * 60)
        print("Stage 4b: Mox — Social Media Batch")
        print("=" * 60)

        mox_social = await mox.execute(
            task=(
                "Generate a social media batch for LinkedIn and Twitter/X "
                "promoting OpenClaw. Highlight the 10-agent architecture, "
                "the weekly orchestration cycle, and real deliverables (battle cards, "
                "blog posts, video tutorials, GitHub triage). Create 3 posts per "
                "platform. Developer-authentic tone, no buzzwords."
            ),
            context=context,
        )
        results["mox_social"] = mox_social
        print(f"\nSocial batch: {mox_social.get('status', 'unknown')}")

        # ── Save Output ──────────────────────────────────────────────────
        OUTPUT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"sales_pipeline_{timestamp}.json"

        with open(output_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        print("\n" + "=" * 60)
        print(f"Pipeline complete! Output saved to: {output_path}")
        print("=" * 60)

        # Print LLM usage stats
        if llm_client and hasattr(llm_client, "usage"):
            usage = llm_client.usage
            print("\nLLM Usage:")
            print(f"  Calls: {usage.total_calls}")
            print(f"  Input tokens: {usage.total_input_tokens:,}")
            print(f"  Output tokens: {usage.total_output_tokens:,}")

        # Print personalized outreach preview
        if "pax_personalized" in results:
            pp = results["pax_personalized"]
            if pp.get("outreach"):
                print(f"\n{'─' * 60}")
                print("Preview: pax_personalized")
                print(f"{'─' * 60}")
                for o in pp["outreach"][:2]:
                    print(f"  To: {o['first_name']} {o['last_name']} ({o['title']} at {o['company_name']})")
                    print(f"  Subject: {o['subject']}")
                    print(f"  Hook: {o['research_hook'][:100]}")
                    print()

        # Print content previews
        for key in ("pax_battlecard", "pax_outreach", "mox_blog", "mox_social"):
            content = results.get(key, {}).get("content", "")
            if content:
                print(f"\n{'─' * 60}")
                print(f"Preview: {key}")
                print(f"{'─' * 60}")
                print(content[:500])
                if len(content) > 500:
                    print(f"\n... ({len(content)} chars total)")

    finally:
        if llm_client:
            await llm_client.close()
        if apollo_client:
            await apollo_client.close()
        if instantly_client:
            await instantly_client.close()
        await search_tools.close()
        await api_client.close()


if __name__ == "__main__":
    asyncio.run(main())
