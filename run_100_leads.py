"""
Find 100 unique leads via Apollo, generate personalized emails, upload to Instantly.

Usage:
    python3 run_100_leads.py
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("100_leads")

PRODUCT_NAME = "OpenClaw"
KB_PATH = Path(__file__).parent / "knowledge_base"
OUTPUT_DIR = Path(__file__).parent / "deliverables"

# Emails to skip (already contacted)
EXISTING_EMAILS: set[str] = set()


def load_existing_emails() -> set[str]:
    """Collect emails from previous pipeline runs to avoid duplicates."""
    emails: set[str] = set()
    for f in OUTPUT_DIR.glob("sales_pipeline_*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            for o in data.get("pax_personalized", {}).get("outreach", []):
                if o.get("email"):
                    emails.add(o["email"].lower())
        except Exception:
            pass
    # Also load from any previous 100-lead runs
    for f in OUTPUT_DIR.glob("100_leads_*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            for o in data.get("outreach", []):
                if o.get("email"):
                    emails.add(o["email"].lower())
        except Exception:
            pass
    return emails


async def main():
    from devrel_swarm.core.llm import LLMClient
    from devrel_swarm.core.pax import Pax
    from devrel_swarm.tools.api_client import PostHogClient
    from devrel_swarm.tools.apollo_client import ApolloClient
    from devrel_swarm.tools.instantly_client import InstantlyClient, InstantlyLead
    from devrel_swarm.tools.search_tools import SearchTools

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    apollo_key = os.environ.get("APOLLO_API_KEY", "")
    if not apollo_key:
        print("Error: APOLLO_API_KEY not set")
        sys.exit(1)

    instantly_key = os.environ.get("INSTANTLY_API_KEY", "")
    if not instantly_key:
        print("Error: INSTANTLY_API_KEY not set")
        sys.exit(1)

    # Initialize clients
    api_client = PostHogClient(api_key="", project_id="")
    llm_client = LLMClient(api_key=anthropic_key)
    search_tools = SearchTools(
        firecrawl_api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
        brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
    )
    apollo_client = ApolloClient(api_key=apollo_key)
    instantly_client = InstantlyClient(api_key=instantly_key)

    # Load existing emails to skip
    existing = load_existing_emails()
    logger.info(f"Loaded {len(existing)} existing emails to skip")

    # Search queries — different title variations to get diverse leads
    search_configs = [
        {"titles": ["Head of Developer Relations", "VP Developer Experience"], "per_page": 50, "page": 1},
        {"titles": ["Head of Developer Relations", "VP Developer Experience"], "per_page": 50, "page": 2},
        {"titles": ["Director of Developer Relations", "Developer Advocate Lead"], "per_page": 50, "page": 1},
        {"titles": ["Head of DevRel", "Developer Relations Manager"], "per_page": 50, "page": 1},
        {"titles": ["VP Developer Relations", "Head of Developer Advocacy"], "per_page": 50, "page": 1},
        {"titles": ["Developer Experience Lead", "Head of Community"], "per_page": 50, "page": 1},
    ]

    # Collect unique contacts
    all_contacts = []
    seen_emails: set[str] = set()
    seen_ids: set[str] = set()

    print(f"\n{'=' * 60}")
    print("Step 1: Searching Apollo for leads")
    print(f"{'=' * 60}")

    for config in search_configs:
        if len(all_contacts) >= 120:  # fetch extra to account for enrichment failures
            break
        try:
            result = await apollo_client.search_people(
                titles=config["titles"],
                per_page=config["per_page"],
                page=config["page"],
                organization_num_employees_ranges=["50,100000"],
            )
            for contact in result.contacts:
                cid = contact.id or contact.email or ""
                email_lower = (contact.email or "").lower()
                if cid in seen_ids:
                    continue
                if email_lower and email_lower in existing:
                    continue
                if email_lower and email_lower in seen_emails:
                    continue
                seen_ids.add(cid)
                if email_lower:
                    seen_emails.add(email_lower)
                all_contacts.append(contact)
            logger.info(
                f"Search {config['titles'][:2]} page {config['page']}: "
                f"got {len(result.contacts)}, total unique: {len(all_contacts)}"
            )
        except Exception as e:
            logger.warning(f"Search failed: {e}")
        await asyncio.sleep(1)

    print(f"\nFound {len(all_contacts)} unique contacts from Apollo")

    # Step 2: Enrich contacts to reveal emails
    print(f"\n{'=' * 60}")
    print("Step 2: Enriching contacts (revealing emails)")
    print(f"{'=' * 60}")

    enriched_count = 0
    contacts_with_email = []

    for i, contact in enumerate(all_contacts):
        if len(contacts_with_email) >= 100:
            break

        if not contact.email:
            try:
                enrich_kwargs = {}
                if contact.id:
                    enrich_kwargs["person_id"] = contact.id
                elif contact.linkedin_url:
                    enrich_kwargs["linkedin_url"] = contact.linkedin_url
                if enrich_kwargs:
                    enriched = await apollo_client.enrich_person(**enrich_kwargs)
                    if enriched and enriched.email:
                        email_lower = enriched.email.lower()
                        if email_lower not in existing and email_lower not in seen_emails:
                            contact = enriched
                            seen_emails.add(email_lower)
                            enriched_count += 1
                        else:
                            continue  # duplicate
            except Exception as exc:
                logger.debug(f"Enrichment failed for {contact.first_name}: {exc}")

        if contact.email:
            email_lower = contact.email.lower()
            if email_lower not in existing:
                contacts_with_email.append(contact)

        if (i + 1) % 20 == 0:
            logger.info(f"Enriched {i + 1}/{len(all_contacts)}, with email: {len(contacts_with_email)}")

    print(f"Contacts with email: {len(contacts_with_email)}")
    print(f"Newly enriched: {enriched_count}")

    # Step 3: Create Instantly campaign
    print(f"\n{'=' * 60}")
    print("Step 3: Creating Instantly campaign")
    print(f"{'=' * 60}")

    # Fetch sending accounts
    sending_accounts = []
    try:
        acct_data = await instantly_client._request(
            "GET", "/api/v2/accounts", params={"limit": 10},
        )
        acct_items = acct_data.get("items", acct_data if isinstance(acct_data, list) else [])
        sending_accounts = [a["email"] for a in acct_items if a.get("email")]
    except Exception:
        pass

    campaign = await instantly_client.create_campaign(
        name=f"{PRODUCT_NAME} 100-Lead Outreach {datetime.now().strftime('%Y-%m-%d')}",
        sequences=[{
            "steps": [{
                "type": "email",
                "delay": 0,
                "variants": [{
                    "subject": "{{personalized_subject}}",
                    "body": "{{personalized_body}}",
                }],
            }],
        }],
        accounts=sending_accounts or None,
    )
    campaign_id = campaign.id
    print(f"Campaign created: {campaign.name} ({campaign_id})")

    # Step 4: Research + generate personalized emails
    print(f"\n{'=' * 60}")
    print("Step 4: Researching contacts & generating emails")
    print(f"{'=' * 60}")

    pax = Pax(
        api_client=api_client,
        knowledge_base_path=KB_PATH,
        llm_client=llm_client,
        instantly_client=instantly_client,
        apollo_client=apollo_client,
        search_tools=search_tools,
        product_name=PRODUCT_NAME,
    )

    outreach_list = []
    errors = []

    for i, contact in enumerate(contacts_with_email):
        logger.info(
            f"[{i + 1}/{len(contacts_with_email)}] Researching "
            f"{contact.first_name} {contact.last_name} at {contact.company_name}"
        )

        # Research
        try:
            hook, source_url = await pax._research_prospect(contact)
        except Exception as exc:
            hook = ""
            logger.debug(f"Research failed: {exc}")

        # Get KB context
        kb_context = ""
        try:
            kb_results = pax._kb.search(
                f"{contact.title} {contact.company_name} developer relations",
                max_results=3,
            )
            kb_context = "\n\n".join(r.content[:500] for r in kb_results)
        except Exception:
            pass

        # Generate email
        try:
            email_data = await pax._generate_personalized_email(
                contact, hook, kb_context, "",
            )
        except Exception as exc:
            logger.warning(f"Email generation failed for {contact.email}: {exc}")
            email_data = None

        if email_data:
            outreach_list.append({
                "contact_id": contact.id,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email,
                "title": contact.title or "",
                "company_name": contact.company_name or "",
                "subject": email_data.get("subject", ""),
                "body": email_data.get("body", ""),
                "research_hook": hook or "",
            })

        if (i + 1) % 10 == 0:
            print(f"  Progress: {i + 1}/{len(contacts_with_email)}, emails generated: {len(outreach_list)}")

        await asyncio.sleep(1.0)

    print(f"\nEmails generated: {len(outreach_list)}")

    # Step 5: Upload to Instantly
    print(f"\n{'=' * 60}")
    print("Step 5: Uploading leads to Instantly")
    print(f"{'=' * 60}")

    leads = [
        InstantlyLead(
            email=o["email"],
            first_name=o["first_name"],
            last_name=o["last_name"],
            company_name=o["company_name"],
            title=o["title"],
            custom_variables={
                "personalized_subject": o["subject"],
                "personalized_body": o["body"],
                "research_hook": o["research_hook"],
            },
        )
        for o in outreach_list
    ]

    result = await instantly_client.add_leads_bulk(campaign_id, leads)
    uploaded = result.get("added", 0)
    print(f"Uploaded: {uploaded} leads to campaign {campaign_id}")

    # Save output
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"100_leads_{timestamp}.json"

    output = {
        "campaign_id": campaign_id,
        "campaign_name": campaign.name,
        "total_searched": len(all_contacts),
        "total_with_email": len(contacts_with_email),
        "emails_generated": len(outreach_list),
        "uploaded": uploaded,
        "outreach": outreach_list,
        "errors": errors,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print(f"Complete! Output saved to: {output_path}")
    print(f"  Searched: {len(all_contacts)} contacts")
    print(f"  With email: {len(contacts_with_email)}")
    print(f"  Emails generated: {len(outreach_list)}")
    print(f"  Uploaded to Instantly: {uploaded}")
    print(f"  Campaign: {campaign_id}")
    print(f"{'=' * 60}")

    # Preview first 3
    for o in outreach_list[:3]:
        print(f"\n  To: {o['first_name']} {o['last_name']} ({o['title']} at {o['company_name']})")
        print(f"  Subject: {o['subject']}")

    # Cleanup
    await llm_client.close()
    await apollo_client.close()
    await instantly_client.close()
    await search_tools.close()
    await api_client.close()


if __name__ == "__main__":
    asyncio.run(main())
