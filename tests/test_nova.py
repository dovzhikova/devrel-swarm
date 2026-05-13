"""Tests for Nova growth strategist module."""

import pytest

from devrel_origin.core.nova import ExperimentDesign, Nova


@pytest.fixture
def nova(posthog_client, knowledge_base_path):
    return Nova(api_client=posthog_client, knowledge_base_path=knowledge_base_path)


class TestNovaCalculateSampleSize:
    """Test calculate_sample_size() with known inputs."""

    def test_sample_size_default_params(self, nova):
        size = nova.calculate_sample_size(
            baseline_rate=0.10,
            minimum_detectable_effect=0.02,
        )
        assert size > 0
        assert isinstance(size, int)

    def test_sample_size_increases_with_smaller_effect(self, nova):
        size_large = nova.calculate_sample_size(
            baseline_rate=0.10,
            minimum_detectable_effect=0.05,
        )
        size_small = nova.calculate_sample_size(
            baseline_rate=0.10,
            minimum_detectable_effect=0.01,
        )
        assert size_small > size_large

    def test_sample_size_increases_with_higher_power(self, nova):
        size_80 = nova.calculate_sample_size(
            baseline_rate=0.10,
            minimum_detectable_effect=0.02,
            power=0.80,
        )
        size_90 = nova.calculate_sample_size(
            baseline_rate=0.10,
            minimum_detectable_effect=0.02,
            power=0.90,
        )
        assert size_90 > size_80


class TestNovaAnalyzeFunnel:
    """Test analyze_funnel() drop-off detection."""

    @pytest.mark.asyncio
    async def test_detect_funnel_drop_off(self, nova):
        stages = [
            {"name": "signup", "count": 1000},
            {"name": "onboarding", "count": 800},
            {"name": "first_action", "count": 400},
            {"name": "retention_d7", "count": 100},
        ]
        result = await nova.analyze_funnel("activation", stages)
        assert result.biggest_drop_off_stage is not None
        assert result.overall_conversion == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_funnel_with_critical_drop(self, nova):
        stages = [
            {"name": "step_1", "count": 1000},
            {"name": "step_2", "count": 900},
            {"name": "step_3", "count": 100},
        ]
        result = await nova.analyze_funnel("critical_drop", stages)
        assert result.biggest_drop_off_stage == "step_3"
        assert result.overall_conversion == pytest.approx(0.1)


class TestNovaDesignExperiment:
    """Test design_experiment() creates valid ExperimentDesign."""

    @pytest.mark.asyncio
    async def test_design_valid_experiment(self, nova):
        design = await nova.design_experiment(
            hypothesis="Simplified onboarding increases retention",
            primary_metric="d7_retention",
            baseline_rate=0.40,
            minimum_detectable_effect=0.05,
        )
        assert isinstance(design, ExperimentDesign)
        assert design.hypothesis is not None
        assert design.sample_size_per_arm > 0

    @pytest.mark.asyncio
    async def test_experiment_has_required_fields(self, nova):
        design = await nova.design_experiment(
            hypothesis="New feature increases engagement",
            primary_metric="activation_rate",
            baseline_rate=0.50,
            minimum_detectable_effect=0.08,
        )
        assert design.primary_metric == "activation_rate"
        assert design.expected_duration_days > 0
        assert design.expected_duration_days <= 365
        assert design.success_criteria is not None
        assert len(design.success_criteria) > 0
        assert len(design.guardrail_metrics) > 0


class TestNovaExecuteWired:
    """Test that execute() uses upstream themes to design experiments."""

    @pytest.mark.asyncio
    async def test_execute_designs_experiments_from_themes(self, nova):
        context = {
            "iris_themes": {
                "themes": [
                    {
                        "title": "SDK init failures",
                        "severity": 7.0,
                        "product_areas": ["sdks"],
                        "recommended_actions": ["Fix React Native init"],
                    },
                    {
                        "title": "Outdated docs",
                        "severity": 4.0,
                        "product_areas": ["feature_flags"],
                        "recommended_actions": ["Update tutorial"],
                    },
                ],
            },
        }
        result = await nova.execute("Design experiments", context=context)
        assert result["status"] == "designed"
        assert len(result["experiments"]) >= 1
        assert result["experiments"][0]["sample_size_per_arm"] > 0
        assert result["upstream_themes_used"] == 2

    @pytest.mark.asyncio
    async def test_execute_includes_funnel_analysis(self, nova):
        context = {
            "iris_themes": {
                "themes": [
                    {
                        "title": "Test",
                        "severity": 5.0,
                        "product_areas": ["analytics"],
                        "recommended_actions": ["Fix"],
                    }
                ]
            }
        }
        result = await nova.execute("Design experiments", context=context)
        assert result["funnel_analysis"] is not None
        assert result["funnel_analysis"]["biggest_drop_off_stage"] is not None

    @pytest.mark.asyncio
    async def test_execute_without_themes_returns_empty(self, nova):
        result = await nova.execute("Design experiments", context={})
        assert result["experiments"] == []
        assert result["upstream_themes_used"] == 0


class TestNovaExperimentIdStability:
    """experiment_id must be stable across process restarts (no Python hash randomization)."""

    @pytest.mark.asyncio
    async def test_experiment_id_is_stable_across_calls(self, nova):
        """sha256-based experiment_id must be deterministic for the same hypothesis."""
        hypothesis = "Larger CTA increases signups"
        d1 = await nova.design_experiment(
            hypothesis=hypothesis,
            primary_metric="signup_rate",
            baseline_rate=0.10,
            minimum_detectable_effect=0.02,
        )
        d2 = await nova.design_experiment(
            hypothesis=hypothesis,
            primary_metric="signup_rate",
            baseline_rate=0.10,
            minimum_detectable_effect=0.02,
        )
        assert d1.experiment_id == d2.experiment_id
        # And it should look like our sha-based format, not the old hash() form.
        assert d1.experiment_id.startswith("exp_")
        assert len(d1.experiment_id) == len("exp_") + 8


class TestNovaDailySignupsGuard:
    """DAILY_SIGNUPS_ESTIMATE below the floor must be clamped, not used directly."""

    @pytest.mark.asyncio
    async def test_low_daily_signups_clamped_to_floor(self, nova, monkeypatch, caplog):
        monkeypatch.setenv("DAILY_SIGNUPS_ESTIMATE", "1")
        with caplog.at_level("WARNING"):
            design = await nova.design_experiment(
                hypothesis="floor-test",
                primary_metric="signup_rate",
                baseline_rate=0.10,
                minimum_detectable_effect=0.05,
            )
        # Floor of 10 keeps duration finite and reasonable; without it,
        # large sample sizes / 1 daily signup → multi-decade durations.
        assert design.expected_duration_days < 10000
        assert any("below floor" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_zero_daily_signups_does_not_raise(self, nova, monkeypatch):
        monkeypatch.setenv("DAILY_SIGNUPS_ESTIMATE", "0")
        # Must not raise ZeroDivisionError — the floor guards the divisor.
        design = await nova.design_experiment(
            hypothesis="zero-test",
            primary_metric="signup_rate",
            baseline_rate=0.10,
            minimum_detectable_effect=0.05,
        )
        assert design.expected_duration_days > 0


class TestNovaFunnelDataSource:
    """Funnel block must declare whether counts came from the API or are default estimates."""

    @pytest.mark.asyncio
    async def test_funnel_marks_default_estimates_when_api_client_lacks_get_funnel(self, nova):
        """Without a real funnel API the funnel must be marked as default estimates."""
        # The default PostHog api_client doesn't expose ``get_funnel`` —
        # consumers reading the result need an unambiguous signal so they
        # don't write the hardcoded mock counts into reports as if real.
        context = {
            "iris_themes": {
                "themes": [
                    {
                        "title": "Test",
                        "severity": 5.0,
                        "product_areas": ["analytics"],
                        "recommended_actions": ["Fix"],
                    }
                ]
            }
        }
        result = await nova.execute("Design experiments", context=context)
        funnel = result.get("funnel_analysis") or {}
        assert funnel.get("data_source") == "default_estimates"
