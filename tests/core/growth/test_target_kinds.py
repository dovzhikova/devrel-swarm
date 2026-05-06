"""Tests for Pillar + TargetKind enums and the (pillar, target_kind) collision guard."""

import pytest

from devrel_swarm.core.growth.target_kinds import (
    Pillar,
    TargetKind,
    validate_target_kind_for_pillar,
)


class TestPillar:
    def test_all_pillars_present(self):
        names = {p.value for p in Pillar}
        assert names == {"argus", "seo", "geo", "cro"}

    def test_pillar_lookup_by_value(self):
        assert Pillar("argus") == Pillar.ARGUS
        assert Pillar("seo") == Pillar.SEO


class TestTargetKind:
    def test_all_kinds_present(self):
        names = {k.value for k in TargetKind}
        assert names == {
            "content_id",
            "url",
            "keyword",
            "funnel_step",
            "brand_query",
            "competitor",
        }


class TestValidator:
    @pytest.mark.parametrize(
        "pillar,kind",
        [
            (Pillar.ARGUS, TargetKind.CONTENT_ID),
            (Pillar.SEO, TargetKind.URL),
            (Pillar.SEO, TargetKind.KEYWORD),
            (Pillar.GEO, TargetKind.BRAND_QUERY),
            (Pillar.GEO, TargetKind.URL),
            (Pillar.GEO, TargetKind.COMPETITOR),
            (Pillar.CRO, TargetKind.FUNNEL_STEP),
        ],
    )
    def test_valid_pairs(self, pillar: Pillar, kind: TargetKind):
        # Should not raise
        validate_target_kind_for_pillar(pillar, kind)

    @pytest.mark.parametrize(
        "pillar,kind",
        [
            (Pillar.ARGUS, TargetKind.URL),
            (Pillar.SEO, TargetKind.FUNNEL_STEP),
            (Pillar.GEO, TargetKind.CONTENT_ID),
            (Pillar.CRO, TargetKind.KEYWORD),
        ],
    )
    def test_invalid_pairs_raise(self, pillar: Pillar, kind: TargetKind):
        with pytest.raises(ValueError, match="not valid for pillar"):
            validate_target_kind_for_pillar(pillar, kind)
