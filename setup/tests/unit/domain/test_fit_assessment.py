from memai_setup.domain.model import FitLevel, LLMCatalogueEntry, VRAMEstimate
from memai_setup.domain.services import assess_fit


def _entry(min_gb: float, recommended_gb: float) -> LLMCatalogueEntry:
    return LLMCatalogueEntry(
        model_id="test-model",
        display_name="Test Model",
        vram=VRAMEstimate(min_gb, recommended_gb),
        languages=frozenset({"en"}),
        recommended=True,
        reasoning=False,
        description="",
    )


def test_comfortable_when_headroom_covers_recommended():
    assert assess_fit(_entry(4, 6), available_vram_gb=24).level == FitLevel.COMFORTABLE


def test_tight_when_headroom_covers_min_but_not_recommended():
    assert assess_fit(_entry(4, 10), available_vram_gb=8).level == FitLevel.TIGHT


def test_wont_fit_when_headroom_below_min():
    assert assess_fit(_entry(20, 24), available_vram_gb=8).level == FitLevel.WONT_FIT


def test_undetectable_vram_is_tight_with_warning():
    result = assess_fit(_entry(4, 6), available_vram_gb=None)
    assert result.level == FitLevel.TIGHT
    assert "could not be detected" in result.message
