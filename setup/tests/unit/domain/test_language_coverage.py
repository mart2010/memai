from memai_setup.domain.language_coverage import offered_languages
from memai_setup.domain.model import STTCatalogueEntry, TTSCatalogueEntry, VRAMEstimate, WhisperModelEntry


def _stt(languages: set[str], has_adapter: bool = True) -> STTCatalogueEntry:
    return STTCatalogueEntry(
        engine="test-stt",
        models=(WhisperModelEntry("small", VRAMEstimate(1, 2), recommended=False),),
        languages=frozenset(languages),
        has_adapter=has_adapter,
        description="",
    )


def _tts(languages: set[str], bundled: bool = True) -> TTSCatalogueEntry:
    return TTSCatalogueEntry(
        engine="test-tts",
        licence="MIT",
        languages=frozenset(languages),
        voices=(),
        bundled=bundled,
        description="",
    )


def test_offered_languages_is_union_of_tts_restricted_to_stt_coverage():
    stt = (_stt({"*"}),)
    tts = (_tts({"en", "fr"}), _tts({"de"}))
    assert offered_languages(stt, tts) == frozenset({"en", "fr", "de"})


def test_offered_languages_ignores_stt_engines_without_adapter():
    stt = (_stt({"en"}, has_adapter=False),)
    tts = (_tts({"en"}),)
    assert offered_languages(stt, tts) == frozenset()


def test_offered_languages_restricts_to_stt_coverage_when_not_wildcard():
    stt = (_stt({"en"}),)
    tts = (_tts({"en", "de"}),)
    assert offered_languages(stt, tts) == frozenset({"en"})
