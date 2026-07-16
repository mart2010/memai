# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Live LLM quality gate for the language-tutor's design (persona switch, lazy
selection-batch injection, [FOCUS:] steering, two-teacher voice cast).

**Not part of continuous testing** — gated behind `MEMAI_TEST_LLM_QUALITY_GATE=1` even
when Ollama is reachable, unlike this directory's other tests (which run whenever their
one real dependency is available). This is a report card for a design/model pairing, run
manually whenever the default model or the prompt pack changes — a FAIL here can be
entirely correct and expected (see docs/PLAN.md Phase 12), not a bug in this test.

Deliberately skips STT, TTS, the WebSocket wire protocol, and Postgres — feeds text
straight into `ProcessTurn.execute()` (bypassing STT via a controllable `FakeSTTService`)
and inspects the returned `TurnResult`/`FakeTTSService.synthesised` directly (bypassing
real audio). This trades full-stack coverage for transparency: every prompt and response
under test is plain text, printed verbatim with `-s`, with nothing to reverse-engineer
from audio or log scraping. Real STT/TTS reliability is covered separately by
`test_stt.py`/`test_tts.py` in this directory; this test is only about what the LLM does
with the text it's given, and — for the voice cast specifically — what a real
`LanguageDetector` (`Py3LangidLanguageDetector`, not a Fake) makes of that real text.

Uses the real tutor persona's system prompt straight from `bundles/italian-a0-starter/`
(via `TomlPersonaBundleSource`), so the prompt under test is byte-identical to what ships.

Run (from `server/`):

    MEMAI_TEST_LLM_QUALITY_GATE=1 uv run pytest tests/integration/test_tutor_llm_quality_gate.py -s

Optional env vars: `MEMAI_TEST_LLM_MODEL` (default `aya-expanse`), `MEMAI_TEST_LLM_HOST`,
`MEMAI_TEST_TUTOR_BUNDLE_PATH` (default `bundles/italian-a0-starter` at the repo root),
`MEMAI_TEST_SWITCH_ATTEMPTS` (default 5).

A single run is one data point, not a verdict — this model's behaviour is genuinely
non-deterministic; re-run a handful of times before concluding anything about a rate.
History: 8 manual runs against `aya-expanse` on 2026-07-13, back when cast voice
switching was still LLM-tag-based (`[SPEAKER:role]`), found `persona_switch`/
`selection_batch`/`focus_marker` reliably PASS (8/8, after front-loading the
`[PERSONA:]`/`[FOCUS:]` few-shot — see `_compose_working_context`) but
`cast_voice_switch` only ~50% (4/8). That mechanism is now retired: voice selection is
per-segment language detection, not an LLM tag (see `docs/BRIEF_phase12_tutor.md`'s
"Cast mechanism" correction) — the 4/8 figure no longer applies to what this test now
checks and needs fresh data.
"""
import os
from datetime import datetime, UTC
from pathlib import Path
from uuid import uuid4

import pytest

from memai_server.domain.model import AssistantPersona, Concept, Language, User
from memai_server.infrastructure.bundle_toml import TomlPersonaBundleSource
from memai_server.infrastructure.language_detection import Py3LangidLanguageDetector
from memai_server.infrastructure.llm import OllamaLLMService
from memai_server.services.ports import SelectedItem
from memai_server.services.session import ProcessTurn, StartSession, TurnResult

from tests.fakes.fakes import (
    FakeEmbeddingService,
    FakeMemoryBriefRepository,
    FakeMemoryRepository,
    FakePersonaRepository,
    FakePersonaSelectionPort,
    FakeRecallGate,
    FakeSessionLogReader,
    FakeSTTService,
    FakeTTSService,
    FakeTurnLogger,
    FakeUserRepository,
)

_ENABLED = os.environ.get("MEMAI_TEST_LLM_QUALITY_GATE") is not None
_MODEL = os.environ.get("MEMAI_TEST_LLM_MODEL", "aya-expanse")
_HOST = os.environ.get("MEMAI_TEST_LLM_HOST")
_BUNDLE_PATH = Path(
    os.environ.get(
        "MEMAI_TEST_TUTOR_BUNDLE_PATH",
        str(Path(__file__).resolve().parents[3] / "bundles" / "italian-a0-starter"),
    )
)
_MAX_SWITCH_ATTEMPTS = int(os.environ.get("MEMAI_TEST_SWITCH_ATTEMPTS", "5"))

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="MEMAI_TEST_LLM_QUALITY_GATE not set — manual quality-gate run, see module docstring",
)

# The real seed text from migrations/001_initial_schema.sql — kept in sync by hand since
# there's no DB in this test; drift here just makes the gate slightly less faithful, not
# broken (the "Available personas" reinforcement under test is generic code, not this).
_GA_SYSTEM_PROMPT = (
    "You are a helpful, honest voice assistant. Your text is spoken aloud verbatim — never use markdown "
    "formatting (no **bold**, no _italics_, no bullet points, no headers, no code blocks)."
)


def _load_tutor_persona() -> AssistantPersona:
    bundle = TomlPersonaBundleSource().load(_BUNDLE_PATH)
    definition = bundle.persona
    assert definition is not None, f"{_BUNDLE_PATH} has no [persona] table"
    now = datetime.now(UTC)
    voices = dict(definition.voices)
    voices.setdefault("default", "af_heart")  # bundle omits it; the real installer derives it, irrelevant here
    return AssistantPersona(
        id=uuid4(),
        name=definition.name,
        system_prompt=definition.system_prompt,
        languages=list(definition.languages),
        response_language=definition.response_language,
        voices=voices,
        is_system=False,
        created_at=now,
        updated_at=now,
        strategy=definition.strategy,
        settings=definition.settings,
    )


def _concept(persona_id, name: str, id_: int) -> Concept:
    return Concept(id=id_, persona_id=persona_id, name=name, description=name, language=Language("it"))


@pytest.fixture
def ollama_model() -> None:
    import ollama

    try:
        ollama.Client(host=_HOST).show(_MODEL)
    except Exception as e:
        pytest.skip(f"Ollama/{_MODEL} not reachable at {_HOST or 'default host'}: {e}")


async def _say(process_turn: ProcessTurn, ctx, stt: FakeSTTService, text: str) -> TurnResult | None:
    stt.transcript = text
    print(f"\n>>> USER: {text}")
    result = await process_turn.execute(ctx, audio=b"unused", now=datetime.now(UTC))
    print(f"<<< ASSISTANT: {result.assistant_content if result else '(empty turn)'}")
    return result


@pytest.mark.asyncio
async def test_tutor_llm_quality_gate(ollama_model) -> None:
    """Spec: FR-202 (persona switch), TR-306 (selection batch), FR-502 ([FOCUS:]),
    FR-205/TR-305 (two-teacher voice cast) — live reliability against a real model's
    real output (and, for the cast, a real LanguageDetector's real classification of
    that output) — not plumbing correctness (see test_session.py for that)."""
    general = AssistantPersona.general_assistant(_GA_SYSTEM_PROMPT)
    tutor = _load_tutor_persona()

    persona_repo = FakePersonaRepository()
    persona_repo.save(general)
    persona_repo.save(tutor)

    strategy = FakePersonaSelectionPort(
        items=[
            SelectedItem(item=_concept(tutor.id, "ciao", 1)),
            SelectedItem(item=_concept(tutor.id, "come stai?", 2)),
        ],
        focused_items=[SelectedItem(item=_concept(tutor.id, "buongiorno", 3))],
    )

    stt = FakeSTTService()
    tts = FakeTTSService()
    process_turn = ProcessTurn(
        stt=stt,
        llm=OllamaLLMService(model=_MODEL, host=_HOST),
        tts=tts,
        embedding_service=FakeEmbeddingService(),
        memory_repo=FakeMemoryRepository(),
        default_recall_gate=FakeRecallGate(),
        persona_repo=persona_repo,
        turn_logger=FakeTurnLogger(),
        language_detector=Py3LangidLanguageDetector(),
        selection_strategies={tutor.id: strategy},
    )
    start_session = StartSession(
        user_repo=FakeUserRepository(user=User(id=uuid4(), primary_language=Language("en"))),
        persona_repo=persona_repo,
        memory_brief_repo=FakeMemoryBriefRepository(),
        session_log_reader=FakeSessionLogReader(),
    )
    ctx = start_session.execute(session_id=uuid4(), started_at=datetime.now(UTC))

    results: dict[str, str] = {}

    switched = False
    for attempt in range(1, _MAX_SWITCH_ATTEMPTS + 1):
        text = f"Switch to {tutor.name}." if attempt == 1 else "Yes, please switch me."
        result = await _say(process_turn, ctx, stt, text)
        if result is not None and result.persona_switched is not None:
            switched = True
            results["persona_switch"] = f"PASS (confirmed on attempt {attempt}/{_MAX_SWITCH_ATTEMPTS})"
            break
    if not switched:
        results["persona_switch"] = f"FAIL (not confirmed within {_MAX_SWITCH_ATTEMPTS} attempts)"

    if not switched:
        results["selection_batch"] = "SKIP (persona switch prerequisite failed)"
        results["cast_voice_switch"] = "SKIP (persona switch prerequisite failed)"
        results["focus_marker"] = "SKIP (persona switch prerequisite failed)"
    else:
        await _say(process_turn, ctx, stt, "Ciao! I'm ready for today's lesson.")
        fetched = [call for call in strategy.calls if call[0] == tutor.id]
        results["selection_batch"] = "PASS" if fetched else "FAIL (select_items never called for the tutor)"

        voices = {voice for _, voice, _ in tts.synthesised}
        results["cast_voice_switch"] = (
            f"PASS (voices used: {voices})" if len(voices) > 1 else f"FAIL (single voice: {voices})"
        )

        await _say(process_turn, ctx, stt, "Can we just review old vocabulary today instead?")
        focused = [call for call in strategy.calls if call[1] is not None]
        results["focus_marker"] = "PASS" if focused else "FAIL (no [FOCUS:] re-fetch observed)"

    print("\n=== Tutor LLM quality gate ===")
    for name, outcome in results.items():
        print(f"  {name}: {outcome}")

    failed = [name for name, outcome in results.items() if outcome.startswith("FAIL")]
    assert not failed, f"quality gate failed: {failed} — see printed dialogue above (run with -s)"
