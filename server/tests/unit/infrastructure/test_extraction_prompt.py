from datetime import datetime, UTC

from memai_server.domain.model import Conversation, GENERAL_ASSISTANT_ID, Language
from memai_server.infrastructure.llm._common import _extraction_system_prompt, _parse_extraction


def _conversation() -> Conversation:
    return Conversation(id=1, started_at=datetime.now(UTC), persona_id=GENERAL_ASSISTANT_ID)


class TestExtractionSystemPrompt:
    def test_episode_summaries_forced_to_primary_language(self):
        """Spec: INV-10, TR-706"""
        prompt = _extraction_system_prompt(_conversation(), Language("fr"))
        assert "Write every episode summary in the language with IETF code 'fr'" in prompt
        assert "regardless of the language the conversation was held in" in prompt

    def test_no_language_rule_when_primary_language_unknown(self):
        """Spec: TR-706"""
        prompt = _extraction_system_prompt(_conversation(), None)
        assert "Write every episode summary" not in prompt

    def test_asks_for_category_on_concepts_only(self):
        """Spec: TR-706, FR-307 — procedures are never requested from conversation."""
        prompt = _extraction_system_prompt(_conversation(), Language("en"))
        assert prompt.count('"category"') == 1
        assert '"procedures"' not in prompt

    def test_procedures_never_requested_even_with_episodes_enabled(self):
        """Spec: FR-307"""
        prompt = _extraction_system_prompt(_conversation(), Language("en"), extract_episodes=True)
        assert "procedures" not in prompt

    def test_episode_prompt_excludes_meta_and_requires_time_grounding(self):
        """Spec: FR-307 — the 2026-07-18 review found a debugging session about the
        assistant's own TTS bug fabricated into a personal-event episode; the prompt
        must explicitly exclude conversation-about-itself content and require a real
        time/place, not just ask for a summary."""
        prompt = _extraction_system_prompt(_conversation(), Language("en"))
        assert "NOT this conversation itself" in prompt
        assert "assistant's own operation" in prompt
        assert "identifiable time or place" in prompt


class TestParseExtractionCategory:
    def test_category_parsed_when_present(self):
        """Spec: TR-706"""
        data = {
            "concepts": [{"name": "comer", "description": "To eat.", "language": "es", "category": "verb"}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].category == "verb"

    def test_category_defaults_to_none(self):
        """Spec: TR-706"""
        data = {
            "concepts": [{"name": "comer", "description": "To eat.", "language": "es"}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].category is None

    def test_concepts_are_organic(self):
        """Spec: FR-307 — live-conversation extraction always produces organic concepts."""
        data = {"concepts": [{"name": "comer", "description": "To eat.", "language": "es"}]}
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].origin == "organic"

    def test_procedures_in_payload_are_ignored(self):
        """Spec: FR-307 — even if a model disobeys the prompt and emits a "procedures"
        array, ExtractionResult has no such field — parsing must not error on it."""
        data = {
            "concepts": [],
            "procedures": [{"name": "p", "description": "d.", "language": "en", "steps": []}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts == []


class TestParseExtractionEpisodeTimeGrounding:
    def test_episode_without_happened_at_is_dropped(self):
        """Spec: FR-307 — no genuine time grounding means it's not a real episode,
        rather than silently backdating it to the conversation's own timestamp."""
        data = {"episodes": [{"summary": "Debugging the TTS voice download.", "happened_at": None}]}
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.episodes == []

    def test_episode_with_unparseable_happened_at_is_dropped(self):
        """Spec: FR-307"""
        data = {"episodes": [{"summary": "Something happened.", "happened_at": "not-a-date"}]}
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.episodes == []

    def test_episode_with_valid_happened_at_is_kept(self):
        """Spec: FR-307"""
        data = {
            "episodes": [
                {"summary": "Went hiking in the Alps.", "happened_at": "2026-06-01T00:00:00+00:00"}
            ]
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert len(result.episodes) == 1
        assert result.episodes[0].summary == "Went hiking in the Alps."
