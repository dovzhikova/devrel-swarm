"""
KB Harvester — Automatic knowledge base population from public content.

Scrapes public content sources (website, Substack, LinkedIn, GitHub README)
and converts them into markdown files for the knowledge base.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class HarvestSource:
    """A content source to harvest."""

    name: str
    url: str
    source_type: str  # "website", "substack", "github", "sitemap"
    category: str  # KB subdirectory (e.g., "about", "blog", "docs")


@dataclass
class HarvestedDoc:
    """A single harvested document."""

    title: str
    source_url: str
    content: str
    category: str
    filename: str  # Sanitized filename for KB


_product_url = os.getenv("PRODUCT_URL", "https://openclaw.ai")
_github_repo = os.getenv("GITHUB_REPO", "openclaw/openclaw")

DEFAULT_SOURCES: list[dict[str, str]] = [
    {
        "name": "Website Homepage",
        "url": _product_url,
        "source_type": "website",
        "category": "about",
    },
    {
        "name": "GitHub README",
        "url": f"https://raw.githubusercontent.com/{_github_repo}/main/README.md",
        "source_type": "github",
        "category": "docs",
    },
]


class KBHarvester:
    """Harvests public content into the knowledge base.

    Usage::

        harvester = KBHarvester(kb_path, firecrawl_api_key="fc-...")
        report = await harvester.harvest_all()
        # Or harvest specific URLs:
        doc = await harvester.harvest_url("https://example.com/blog/post", "blog")
    """

    FIRECRAWL_API = "https://api.firecrawl.dev/v1"

    def __init__(
        self,
        kb_path: Path,
        firecrawl_api_key: str = "",
        sources: list[dict[str, str]] | None = None,
    ):
        self.kb_path = Path(kb_path)
        self.firecrawl_api_key = firecrawl_api_key
        self.sources = [HarvestSource(**s) for s in (sources or DEFAULT_SOURCES)]
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def harvest_all(self) -> dict[str, Any]:
        """Harvest all configured sources in parallel."""
        tasks = [self._harvest_source(s) for s in self.sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        report: dict[str, Any] = {"harvested": 0, "failed": 0, "sources": []}
        for source, result in zip(self.sources, results, strict=True):
            if isinstance(result, Exception):
                report["failed"] += 1
                report["sources"].append(
                    {
                        "name": source.name,
                        "status": "failed",
                        "error": str(result),
                    }
                )
            elif result:
                report["harvested"] += 1
                report["sources"].append(
                    {
                        "name": source.name,
                        "status": "ok",
                        "file": result.filename,
                    }
                )
            else:
                report["failed"] += 1
                report["sources"].append(
                    {
                        "name": source.name,
                        "status": "empty",
                    }
                )

        logger.info(f"Harvest complete: {report['harvested']} OK, {report['failed']} failed")
        return report

    async def _harvest_source(self, source: HarvestSource) -> HarvestedDoc | None:
        """Harvest a single source."""
        if source.source_type == "github":
            return await self._harvest_raw_url(source)
        elif source.source_type in ("website", "substack"):
            return await self._harvest_web_page(source)
        elif source.source_type == "sitemap":
            return await self._harvest_sitemap(source)
        else:
            logger.warning(f"Unknown source type: {source.source_type}")
            return None

    async def _harvest_raw_url(self, source: HarvestSource) -> HarvestedDoc | None:
        """Fetch raw content (e.g., GitHub raw files)."""
        try:
            resp = await self._client.get(source.url, follow_redirects=True)
            resp.raise_for_status()
            content = resp.text

            doc = HarvestedDoc(
                title=source.name,
                source_url=source.url,
                content=content,
                category=source.category,
                filename=self._sanitize_filename(source.name) + ".md",
            )
            self._save_doc(doc)
            return doc
        except Exception as exc:
            logger.warning(f"Failed to harvest {source.url}: {exc}")
            return None

    async def _harvest_web_page(self, source: HarvestSource) -> HarvestedDoc | None:
        """Scrape a web page, preferring Firecrawl for clean markdown."""
        content = ""

        # Try Firecrawl first
        if self.firecrawl_api_key:
            try:
                resp = await self._client.post(
                    f"{self.FIRECRAWL_API}/scrape",
                    headers={
                        "Authorization": f"Bearer {self.firecrawl_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"url": source.url, "formats": ["markdown"]},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("success"):
                    content = data.get("data", {}).get("markdown", "")
            except Exception as exc:
                logger.warning(f"Firecrawl scrape failed for {source.url}: {exc}")

        # Fallback: direct fetch with HTML stripping
        if not content:
            try:
                resp = await self._client.get(
                    source.url,
                    follow_redirects=True,
                    headers={"User-Agent": "DevRelSwarm/1.0"},
                )
                resp.raise_for_status()
                content = self._strip_html(resp.text)
            except Exception as exc:
                logger.warning(f"Direct fetch failed for {source.url}: {exc}")
                return None

        if not content or len(content) < 50:
            return None

        title = self._extract_title(content) or source.name
        doc = HarvestedDoc(
            title=title,
            source_url=source.url,
            content=f"# {title}\n\n> Source: {source.url}\n\n{content}",
            category=source.category,
            filename=self._sanitize_filename(title) + ".md",
        )
        self._save_doc(doc)
        return doc

    async def _harvest_sitemap(self, source: HarvestSource) -> HarvestedDoc | None:
        """Parse a sitemap and harvest linked pages."""
        try:
            resp = await self._client.get(source.url, follow_redirects=True)
            resp.raise_for_status()

            # Extract URLs from sitemap XML
            urls = re.findall(r"<loc>(.*?)</loc>", resp.text)
            if not urls:
                return None

            # Harvest first 20 pages
            pages: list[str] = []
            for url in urls[:20]:
                sub_source = HarvestSource(
                    name=url.split("/")[-1] or "page",
                    url=url,
                    source_type="website",
                    category=source.category,
                )
                doc = await self._harvest_web_page(sub_source)
                if doc:
                    pages.append(doc.filename)

            logger.info(f"Sitemap: harvested {len(pages)}/{len(urls)} pages")
            # Return a summary doc
            return HarvestedDoc(
                title=f"Sitemap: {source.name}",
                source_url=source.url,
                content=f"Harvested {len(pages)} pages from sitemap.",
                category=source.category,
                filename="sitemap-index.md",
            )
        except Exception as exc:
            logger.warning(f"Sitemap harvest failed: {exc}")
            return None

    async def harvest_url(self, url: str, category: str = "misc") -> HarvestedDoc | None:
        """Harvest a single URL into the KB."""
        source = HarvestSource(
            name=url.split("/")[-1] or "page",
            url=url,
            source_type="website",
            category=category,
        )
        return await self._harvest_web_page(source)

    def _save_doc(self, doc: HarvestedDoc) -> None:
        """Save a harvested document to the knowledge base."""
        category_dir = self.kb_path / doc.category
        category_dir.mkdir(parents=True, exist_ok=True)

        filepath = category_dir / doc.filename
        filepath.write_text(doc.content, encoding="utf-8")
        logger.info(f"Saved KB doc: {filepath}")

    @staticmethod
    def _sanitize_filename(text: str) -> str:
        """Convert text to a safe filename."""
        clean = re.sub(r"[^\w\s-]", "", text.lower())
        clean = re.sub(r"[\s_]+", "-", clean)
        return clean[:80].strip("-")

    @staticmethod
    def _extract_title(content: str) -> str:
        """Extract title from markdown/text content."""
        for line in content.split("\n")[:10]:
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
            if len(line) > 10 and not line.startswith(("http", "<", "!")):
                return line[:100]
        return ""

    @staticmethod
    def _strip_html(html: str) -> str:
        """Crude HTML → text conversion."""
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


async def main() -> None:
    """CLI entry point for KB harvesting."""
    import argparse
    import os

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Harvest content into knowledge base")
    parser.add_argument("--kb-path", default="knowledge_base", help="KB directory")
    parser.add_argument("--url", help="Single URL to harvest")
    parser.add_argument("--category", default="misc", help="KB category for --url")
    parser.add_argument(
        "--sources-file",
        help="JSON file with custom harvest sources",
    )
    args = parser.parse_args()

    sources = None
    if args.sources_file:
        import json

        sources = json.loads(Path(args.sources_file).read_text())

    harvester = KBHarvester(
        kb_path=Path(args.kb_path),
        firecrawl_api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
        sources=sources,
    )

    try:
        if args.url:
            doc = await harvester.harvest_url(args.url, args.category)
            if doc:
                print(f"Harvested: {doc.filename} ({len(doc.content)} chars)")
            else:
                print("Failed to harvest URL")
        else:
            report = await harvester.harvest_all()
            print(f"Harvested: {report['harvested']}, Failed: {report['failed']}")
            for s in report["sources"]:
                status = "✓" if s["status"] == "ok" else "✗"
                print(f"  [{status}] {s['name']}: {s.get('file', s.get('error', ''))}")
    finally:
        await harvester.close()


if __name__ == "__main__":
    asyncio.run(main())
