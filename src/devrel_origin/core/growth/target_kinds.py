"""Pillar + TargetKind enums and the (pillar, target_kind) collision guard.

Stored as TEXT in SQLite (`analytics_recommendations.pillar` and
`.target_kind`) but typed at the Python boundary so accidental
free-form strings are caught at write time, not at calibration.
"""

from __future__ import annotations

from enum import Enum


class Pillar(str, Enum):
    ARGUS = "argus"
    SEO = "seo"
    GEO = "geo"
    CRO = "cro"


class TargetKind(str, Enum):
    CONTENT_ID = "content_id"
    URL = "url"
    KEYWORD = "keyword"
    FUNNEL_STEP = "funnel_step"
    BRAND_QUERY = "brand_query"
    COMPETITOR = "competitor"


# Per-pillar allowlists. Cross-cutting kinds (URL is in both SEO + GEO)
# are the reason we don't just key off pillar alone in the schema.
_VALID: dict[Pillar, frozenset[TargetKind]] = {
    Pillar.ARGUS: frozenset({TargetKind.CONTENT_ID}),
    Pillar.SEO: frozenset({TargetKind.URL, TargetKind.KEYWORD}),
    Pillar.GEO: frozenset({TargetKind.BRAND_QUERY, TargetKind.URL, TargetKind.COMPETITOR}),
    Pillar.CRO: frozenset({TargetKind.FUNNEL_STEP}),
}


def validate_target_kind_for_pillar(pillar: Pillar, kind: TargetKind) -> None:
    """Raise ValueError if `kind` is not a legal target for `pillar`.

    Called by `persist_recommendation` before INSERT. Keeps the
    cross-pillar query namespace coherent: a `target_kind='url'` row
    is unambiguously SEO or GEO and never anything else.
    """
    if kind not in _VALID[pillar]:
        valid_names = sorted(k.value for k in _VALID[pillar])
        raise ValueError(
            f"target_kind={kind.value!r} not valid for pillar={pillar.value!r}; "
            f"valid kinds: {valid_names}"
        )
