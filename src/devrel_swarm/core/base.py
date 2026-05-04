"""Shared base classes and utilities for all agents."""

import logging
import math
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Common stop words excluded from KB keyword matching
STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "must",
        "ought",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "not",
        "only",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "because",
        "as",
        "until",
        "while",
        "of",
        "at",
        "by",
        "for",
        "with",
        "about",
        "against",
        "between",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "to",
        "from",
        "up",
        "down",
        "in",
        "out",
        "on",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "if",
        "else",
    }
)


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM output.

    Handles ```json, ```python, ```text, and bare ``` fences.
    """
    text = text.strip()
    text = re.sub(r"^```(?:json|python|text)?\s*\n?", "", text, count=1)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _resolve_optimize_dir() -> Path | None:
    """Walk up from this file to find a repo with `optimize/` + `pyproject.toml`.

    The repo-root `optimize/` tree carries per-agent prompt overrides and
    self-improvement `known_issues.txt` files. It only exists in dev
    checkouts; pipx-installed users never see it and always get the
    inline defaults that callers pass to `load_agent_prompt`.
    """
    candidate = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = candidate.parent
        if (candidate / "optimize").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate / "optimize"
    return None


_OPTIMIZE_DIR = _resolve_optimize_dir()


def _find_prompt_dir(agent_name: str) -> Path | None:
    """Return the directory holding `{agent_name}`'s prompts, or None.

    Accepts both layouts the repo currently uses: top-level `optimize/{agent}/`
    (Argus) and nested `optimize/agents/{agent}/` (Kai/Echo/Iris/Nova/Rex/Vox/
    Dex/Sage/Mox).
    """
    if _OPTIMIZE_DIR is None:
        return None
    for parent in (_OPTIMIZE_DIR, _OPTIMIZE_DIR / "agents"):
        candidate = parent / agent_name
        if candidate.is_dir():
            return candidate
    return None


def load_agent_prompt(agent_name: str, prompt_name: str, default: str) -> str:
    """Load a prompt from the repo's `optimize/` tree if present, else default.

    Searches `optimize/{agent}/{prompt_name}` and the legacy nested
    `optimize/agents/{agent}/{prompt_name}` layout. Appends `known_issues.txt`
    from the same dir when the self-improvement loop has produced one.
    """
    agent_dir = _find_prompt_dir(agent_name)

    if agent_dir is not None and (agent_dir / prompt_name).exists():
        prompt_path = agent_dir / prompt_name
        logger.info(f"Loaded optimized prompt: {prompt_path}")
        prompt = prompt_path.read_text(encoding="utf-8")
    elif _OPTIMIZE_DIR is not None and (_OPTIMIZE_DIR / prompt_name).exists():
        prompt = (_OPTIMIZE_DIR / prompt_name).read_text(encoding="utf-8")
    else:
        prompt = default

    if agent_dir is not None:
        issues_path = agent_dir / "known_issues.txt"
        if issues_path.exists():
            prompt += "\n\n" + issues_path.read_text(encoding="utf-8")

    return prompt


def _tokenize(text: str, stop_words: frozenset[str] = STOP_WORDS) -> list[str]:
    """Tokenize text into lowercase words, removing stop words and short tokens."""
    return [w.lower() for w in re.split(r"\W+", text) if w.lower() not in stop_words and len(w) > 2]


_kb_cache: dict[tuple[str, frozenset[str] | None], "KnowledgeBaseSearch"] = {}


def get_kb_search(
    knowledge_base_path: Path,
    extra_stop_words: frozenset[str] | None = None,
) -> "KnowledgeBaseSearch":
    """Return a cached KnowledgeBaseSearch instance.

    Multiple agents sharing the same KB path reuse a single index
    instead of each reading all files from disk independently.
    """
    cache_key = (str(knowledge_base_path), extra_stop_words)
    if cache_key not in _kb_cache:
        _kb_cache[cache_key] = KnowledgeBaseSearch(knowledge_base_path, extra_stop_words)
    return _kb_cache[cache_key]


class KnowledgeBaseSearch:
    """Reusable knowledge base indexer and TF-IDF searcher.

    Uses TF-IDF scoring for relevance ranking instead of simple keyword
    overlap. Documents that contain rare, query-specific terms score higher
    than documents matching only common terms.

    Usage::

        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("feature flags setup", limit=5)
        text = kb.search_as_text("feature flags setup", limit=5)
    """

    def __init__(
        self,
        knowledge_base_path: Path,
        extra_stop_words: frozenset[str] | None = None,
    ):
        self.path = knowledge_base_path
        self.stop_words = STOP_WORDS | (extra_stop_words or frozenset())
        self.index: dict[str, Path] = {}
        self._doc_tokens: dict[str, list[str]] = {}
        self._doc_contents: dict[str, str] = {}
        self._idf: dict[str, float] = {}
        self._build_index()

    def _build_index(self) -> None:
        """Index all markdown files and compute IDF weights."""
        if not self.path.exists():
            logger.info("KB path does not exist, index empty")
            return

        for file in self.path.rglob("*.md"):
            key = file.stem.lower().replace("-", " ").replace("_", " ")
            self.index[key] = file
            try:
                content = file.read_text(encoding="utf-8")
            except OSError:
                continue
            source = str(file.relative_to(self.path))
            self._doc_contents[source] = content
            # Tokenize filename + content for TF-IDF
            self._doc_tokens[source] = _tokenize(
                f"{key} {key} {content}",
                self.stop_words,
            )

        # Compute IDF: log(N / df) for each term
        n_docs = max(len(self._doc_tokens), 1)
        df: dict[str, int] = {}
        for tokens in self._doc_tokens.values():
            seen = set(tokens)
            for term in seen:
                df[term] = df.get(term, 0) + 1

        self._idf = {term: math.log(n_docs / count) for term, count in df.items()}
        logger.info(f"KB indexed {len(self.index)} documents, {len(self._idf)} terms")

    def _tfidf_score(self, query_tokens: list[str], source: str) -> float:
        """Compute TF-IDF cosine-like score between query and document."""
        doc_tokens = self._doc_tokens.get(source, [])
        if not doc_tokens or not query_tokens:
            return 0.0

        # Term frequencies in document
        doc_tf: dict[str, float] = {}
        for t in doc_tokens:
            doc_tf[t] = doc_tf.get(t, 0) + 1
        doc_len = len(doc_tokens)

        # Score: sum of (query_tf * doc_tf/doc_len * idf^2) for matching terms
        score = 0.0
        query_tf: dict[str, int] = {}
        for t in query_tokens:
            query_tf[t] = query_tf.get(t, 0) + 1

        for term, qtf in query_tf.items():
            if term in doc_tf:
                idf = self._idf.get(term, 1.0)
                tf_norm = doc_tf[term] / doc_len
                score += qtf * tf_norm * idf * idf

        return score

    def search(
        self,
        query: str,
        limit: int = 5,
        content_truncate: int = 3000,
        pad_with_remaining: bool = True,
    ) -> list[dict[str, Any]]:
        """Search the knowledge base using TF-IDF scoring.

        Args:
            query: Search query string.
            limit: Maximum number of results.
            content_truncate: Truncate content to this many characters.
            pad_with_remaining: If fewer than *limit* results match, pad with
                unmatched KB docs (preserves the fallback behaviour agents rely on).

        Returns:
            List of dicts with ``source`` (relative path), ``content``, and
            ``relevance`` (float score) keys, sorted by relevance desc.
        """
        query_tokens = _tokenize(query, self.stop_words)

        scored: list[dict[str, Any]] = []
        for source, content in self._doc_contents.items():
            score = self._tfidf_score(query_tokens, source)
            if score > 0:
                scored.append(
                    {
                        "source": source,
                        "content": content[:content_truncate],
                        "relevance": round(score, 4),
                    }
                )

        scored.sort(key=lambda x: x["relevance"], reverse=True)

        # Fallback: pad with remaining KB docs so callers always get context
        if pad_with_remaining and len(scored) < limit:
            existing_sources = {r["source"] for r in scored}
            for source, content in self._doc_contents.items():
                if source in existing_sources:
                    continue
                scored.append(
                    {
                        "source": source,
                        "content": content[:content_truncate],
                        "relevance": 0,
                    }
                )
                if len(scored) >= limit:
                    break

        return scored[:limit]

    def search_as_text(self, query: str, limit: int = 5) -> str:
        """Search and return results as concatenated text.

        Convenience wrapper used by agents (Pax, Mox) that pass KB context
        as a single string to the LLM prompt.
        """
        results = self.search(query, limit=limit, content_truncate=2000)
        return "\n\n".join(f"[{r['source']}]\n{r['content']}" for r in results)
