from memai_setup.domain.model import FitLevel, VRAMEstimate
from memai_setup.domain.services import assess_fit

_RESERVED_GB = 3.0


def test_comfortable_when_headroom_covers_recommended():
    vram = VRAMEstimate(min_gb=4, recommended_gb=6)
    assert assess_fit(vram, available_vram_gb=24, reserved_gb=_RESERVED_GB).level == FitLevel.COMFORTABLE


def test_tight_when_headroom_covers_min_but_not_recommended():
    vram = VRAMEstimate(min_gb=4, recommended_gb=10)
    assert assess_fit(vram, available_vram_gb=8, reserved_gb=_RESERVED_GB).level == FitLevel.TIGHT


def test_wont_fit_when_headroom_below_min():
    vram = VRAMEstimate(min_gb=20, recommended_gb=24)
    assert assess_fit(vram, available_vram_gb=8, reserved_gb=_RESERVED_GB).level == FitLevel.WONT_FIT


def test_undetectable_vram_is_tight_with_warning():
    vram = VRAMEstimate(min_gb=4, recommended_gb=6)
    result = assess_fit(vram, available_vram_gb=None, reserved_gb=_RESERVED_GB)
    assert result.level == FitLevel.TIGHT
    assert "could not be detected" in result.message


def test_reserved_gb_shifts_the_effective_headroom():
    # Same VRAM budget, but a bigger reservation (e.g. an already-chosen LLM
    # sitting alongside a Whisper model pick) pushes a comfortable fit to tight.
    vram = VRAMEstimate(min_gb=3, recommended_gb=6)
    assert assess_fit(vram, available_vram_gb=12, reserved_gb=2.0).level == FitLevel.COMFORTABLE
    assert assess_fit(vram, available_vram_gb=12, reserved_gb=8.0).level == FitLevel.TIGHT
