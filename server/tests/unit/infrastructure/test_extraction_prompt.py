from datetime import datetime, UTC

from memai_server.domain.model import Conversation, GENERAL_ASSISTANT_ID, Language
from memai_server.infrastructure.llm._common import _extraction_system_prompt, _parse_extraction


def _conversation() -> Conversation:
    return Conversation(id=1, started_at=datetime.now(UTC), persona_id=GENERAL_ASSISTANT_ID)


class TestExtractionSystemPrompt:
    def test_episode_summaries_forced_to_primary_language(self):
        prompt = _extraction_system_prompt(_conversation(), Language("fr"))
        assert "Write every episode summary in the language with IETF code 'fr'" in prompt
        assert "regardless of the language the conversation was held in" in prompt

    def test_no_language_rule_when_primary_language_unknown(self):
        prompt = _extraction_system_prompt(_conversation(), None)
        assert "Write every episode summary" not in prompt

    def test_asks_for_category_on_concepts_and_procedures(self):
        prompt = _extraction_system_prompt(_conversation(), Language("en"))
        assert prompt.count('"category"') == 2


class TestParseExtractionCategory:
    def test_category_parsed_when_present(self):
        data = {
            "concepts": [{"name": "comer", "description": "To eat.", "language": "es", "category": "verb"}],
            "procedures": [{"name": "-er conjugation", "description": "Paradigm.", "language": "fr",
                            "steps": [], "category": "morphological_pattern"}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].category == "verb"
        assert result.procedures[0].category == "morphological_pattern"

    def test_category_defaults_to_none(self):
        data = {
            "concepts": [{"name": "comer", "description": "To eat.", "language": "es"}],
            "procedures": [{"name": "p", "description": "d.", "language": "en", "steps": [], "category": ""}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].category is None
        assert result.procedures[0].category is None  # empty string coerced to None
