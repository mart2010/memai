from pathlib import Path

import pytest

from memai_server.domain.model import Language, MemoryType
from memai_server.infrastructure.bundle_toml import TomlPersonaBundleSource
from memai_server.services.ports import BundleFormatError


MANIFEST = '''
format_version = 1
persona_key = "meo/spanish-tutor"

[bundle]
name = "spanish-a1"
version = "1.0.0"
author = "meo"
description = "Spanish A1 test bundle."

[provenance]
generator_model = "claude-fable-5"
generated_at = 2026-07-15

[persona]
name = "Profesora Sofía"
system_prompt = "You teach Spanish."
languages = ["es"]
response_language = "es"
strategy = "language_tutor"

[persona.voices]
target_teacher = "ef_dora"

[persona.settings]
elicitation_cap = 2

[persona.settings.pair_difficulty]
en = 1.0
"*" = 1.5
'''

LESSON_GREETINGS = '''
title = "Greetings"

[[items]]
type = "concept"
name = "hola"
category = "function_word"
language = "es"
description = "The standard Spanish greeting."

[[items]]
type = "procedure"
name = "greeting someone politely"
category = "construction"
language = "es"
description = "How to greet politely."
steps = ["hola / buenos días", "¿cómo está?"]
'''

LESSON_FOOD = '''
[[items]]
type = "concept"
name = "la comida"
language = "es"
description = "Food."
'''


def _write_bundle(root: Path, manifest: str = MANIFEST, lessons: dict[str, str] | None = None) -> Path:
    bundle_dir = root / "spanish-a1"
    bundle_dir.mkdir()
    (bundle_dir / "bundle.toml").write_text(manifest, encoding="utf-8")
    if lessons is None:
        lessons = {"01_greetings.toml": LESSON_GREETINGS, "02_food.toml": LESSON_FOOD}
    if lessons:
        (bundle_dir / "lessons").mkdir()
        for filename, content in lessons.items():
            (bundle_dir / "lessons" / filename).write_text(content, encoding="utf-8")
    return bundle_dir


class TestTomlPersonaBundleSourceHappyPath:
    def test_full_bundle_parses(self, tmp_path: Path) -> None:
        bundle = TomlPersonaBundleSource().load(_write_bundle(tmp_path))

        assert bundle.persona_key == "meo/spanish-tutor"
        assert bundle.name == "spanish-a1"
        assert bundle.version == "1.0.0"
        assert bundle.author == "meo"
        assert bundle.description == "Spanish A1 test bundle."

        assert bundle.persona is not None
        assert bundle.persona.name == "Profesora Sofía"
        assert bundle.persona.languages == (Language("es"),)
        assert bundle.persona.response_language == Language("es")
        # "default" may be omitted — the installer derives it from User.primary_language.
        assert bundle.persona.voices == {"target_teacher": "ef_dora"}
        assert bundle.persona.settings == {
            "elicitation_cap": 2,
            "pair_difficulty": {"en": 1.0, "*": 1.5},
        }
        assert bundle.persona.strategy == "language_tutor"

    def test_persona_without_strategy_parses_to_none(self, tmp_path: Path) -> None:
        manifest = MANIFEST.replace('strategy = "language_tutor"\n', "")
        bundle = TomlPersonaBundleSource().load(_write_bundle(tmp_path, manifest=manifest))
        assert bundle.persona is not None
        assert bundle.persona.strategy is None

    def test_items_parse_with_types_and_steps(self, tmp_path: Path) -> None:
        bundle = TomlPersonaBundleSource().load(_write_bundle(tmp_path))
        concept, procedure = bundle.lessons[0].items

        assert concept.memory_type is MemoryType.CONCEPT
        assert concept.name == "hola"
        assert concept.category == "function_word"
        assert concept.language == Language("es")
        assert concept.steps == ()

        assert procedure.memory_type is MemoryType.PROCEDURE
        assert procedure.steps == ("hola / buenos días", "¿cómo está?")

    def test_lessons_ordered_by_filename_sort(self, tmp_path: Path) -> None:
        """Insertion order is the contract: lesson-filename sort defines curriculum order."""
        lessons = {
            "10_later.toml": LESSON_FOOD,
            "01_first.toml": LESSON_GREETINGS,
            "02_second.toml": LESSON_FOOD,
        }
        bundle = TomlPersonaBundleSource().load(_write_bundle(tmp_path, lessons=lessons))
        assert [lesson.filename for lesson in bundle.lessons] == [
            "01_first.toml", "02_second.toml", "10_later.toml",
        ]

    def test_manifest_preserved_verbatim_and_json_safe(self, tmp_path: Path) -> None:
        """[bundle] + [provenance] go to the bundle_installs JSONB verbatim; TOML dates
        must come out JSON-serialisable (ISO strings)."""
        bundle = TomlPersonaBundleSource().load(_write_bundle(tmp_path))
        assert bundle.manifest["bundle"]["name"] == "spanish-a1"
        assert bundle.manifest["provenance"]["generator_model"] == "claude-fable-5"
        assert bundle.manifest["provenance"]["generated_at"] == "2026-07-15"

    def test_content_only_bundle_has_no_persona(self, tmp_path: Path) -> None:
        """Content-only bundles (e.g. cognate accelerators) omit [persona]."""
        manifest = '''
format_version = 1
persona_key = "meo/spanish-tutor"

[bundle]
name = "fr-es-cognates"
version = "1.0.0"
author = "meo"
'''
        bundle = TomlPersonaBundleSource().load(_write_bundle(tmp_path, manifest=manifest))
        assert bundle.persona is None
        assert bundle.description == ""


class TestTomlPersonaBundleSourceRejections:
    def _load(self, bundle_dir: Path) -> None:
        TomlPersonaBundleSource().load(bundle_dir)

    def test_rejects_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(BundleFormatError, match="not a directory"):
            self._load(tmp_path / "absent")

    def test_rejects_missing_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "empty-bundle").mkdir()
        with pytest.raises(BundleFormatError, match="bundle.toml"):
            self._load(tmp_path / "empty-bundle")

    def test_rejects_invalid_toml_naming_the_file(self, tmp_path: Path) -> None:
        bundle_dir = _write_bundle(tmp_path, lessons={"01_bad.toml": "items = [[["})
        with pytest.raises(BundleFormatError, match="01_bad.toml"):
            self._load(bundle_dir)

    @pytest.mark.parametrize("version_line", ["format_version = 2", ""])
    def test_rejects_wrong_or_missing_format_version(self, tmp_path: Path, version_line: str) -> None:
        manifest = MANIFEST.replace("format_version = 1", version_line)
        with pytest.raises(BundleFormatError, match="format_version"):
            self._load(_write_bundle(tmp_path, manifest=manifest))

    def test_rejects_missing_persona_key(self, tmp_path: Path) -> None:
        manifest = MANIFEST.replace('persona_key = "meo/spanish-tutor"', "")
        with pytest.raises(BundleFormatError, match="persona_key"):
            self._load(_write_bundle(tmp_path, manifest=manifest))

    def test_rejects_missing_bundle_table_fields(self, tmp_path: Path) -> None:
        manifest = MANIFEST.replace('version = "1.0.0"', "")
        with pytest.raises(BundleFormatError, match="version"):
            self._load(_write_bundle(tmp_path, manifest=manifest))

    def test_rejects_bundle_without_lessons(self, tmp_path: Path) -> None:
        with pytest.raises(BundleFormatError, match="lesson"):
            self._load(_write_bundle(tmp_path, lessons={}))

    def test_rejects_lesson_without_items(self, tmp_path: Path) -> None:
        bundle_dir = _write_bundle(tmp_path, lessons={"01_empty.toml": 'title = "Empty"'})
        with pytest.raises(BundleFormatError, match="01_empty.toml"):
            self._load(bundle_dir)

    def test_rejects_item_claiming_engagement_level(self, tmp_path: Path) -> None:
        """A bundle cannot claim the user knows things — engagement_level is installer-owned."""
        lesson = LESSON_FOOD + 'engagement_level = "integrated"\n'
        bundle_dir = _write_bundle(tmp_path, lessons={"01_sneaky.toml": lesson})
        with pytest.raises(BundleFormatError, match="engagement_level"):
            self._load(bundle_dir)

    def test_rejects_item_with_persona_state(self, tmp_path: Path) -> None:
        """Single-writer contract: persona_state is written only by the owning persona's
        assessment strategy, never shipped in a bundle."""
        lesson = LESSON_FOOD + "persona_state = { streak = 3 }\n"
        bundle_dir = _write_bundle(tmp_path, lessons={"01_sneaky.toml": lesson})
        with pytest.raises(BundleFormatError, match="persona_state"):
            self._load(bundle_dir)

    def test_rejects_unknown_item_type(self, tmp_path: Path) -> None:
        lesson = LESSON_FOOD.replace('type = "concept"', 'type = "episode"')
        bundle_dir = _write_bundle(tmp_path, lessons={"01_bad.toml": lesson})
        with pytest.raises(BundleFormatError, match="concept.*procedure"):
            self._load(bundle_dir)

    def test_rejects_steps_on_a_concept(self, tmp_path: Path) -> None:
        lesson = LESSON_FOOD + 'steps = ["step one"]\n'
        bundle_dir = _write_bundle(tmp_path, lessons={"01_bad.toml": lesson})
        with pytest.raises(BundleFormatError, match="steps"):
            self._load(bundle_dir)

    def test_rejects_item_missing_required_field(self, tmp_path: Path) -> None:
        lesson = LESSON_FOOD.replace('description = "Food."', "")
        bundle_dir = _write_bundle(tmp_path, lessons={"01_bad.toml": lesson})
        with pytest.raises(BundleFormatError, match="description"):
            self._load(bundle_dir)

    def test_rejects_persona_with_unknown_keys(self, tmp_path: Path) -> None:
        manifest = MANIFEST.replace(
            'name = "Profesora Sofía"', 'name = "Profesora Sofía"\nis_system = true'
        )
        with pytest.raises(BundleFormatError, match="is_system"):
            self._load(_write_bundle(tmp_path, manifest=manifest))

    def test_rejects_persona_without_languages(self, tmp_path: Path) -> None:
        manifest = MANIFEST.replace('languages = ["es"]', "languages = []")
        with pytest.raises(BundleFormatError, match="languages"):
            self._load(_write_bundle(tmp_path, manifest=manifest))
