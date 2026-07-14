from datetime import datetime, UTC
from pathlib import Path
from uuid import uuid4

import pytest

from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    EngagementLevel,
    Language,
    MemoryType,
    User,
)
from memai_server.services.bundle_install import BundleInstallError, InstallPersonaBundle
from memai_server.services.ports import (
    BundleItemSpec,
    BundleLesson,
    BundlePersonaDefinition,
    PersonaBundle,
)
from memai_server.services.upsert import MemoryUpserter

from tests.fakes.fakes import (
    FakeBundleInstallLog,
    FakeDisambiguationEvaluator,
    FakeEmbeddingService,
    FakeMemoryRepository,
    FakeMemorySynthesizer,
    FakePersonaBundleSource,
    FakePersonaRepository,
    FakeUnitOfWork,
    FakeUserRepository,
)

PERSONA_KEY = "meo/spanish-tutor"
BUNDLE_PATH = Path("bundles/spanish-a1")


def _concept_item(name: str, description: str = "A description.", category: str | None = None) -> BundleItemSpec:
    return BundleItemSpec(
        memory_type=MemoryType.CONCEPT, name=name, description=description,
        language=Language("es"), category=category,
    )


def _procedure_item(name: str, steps: tuple[str, ...] = ("paso uno",)) -> BundleItemSpec:
    return BundleItemSpec(
        memory_type=MemoryType.PROCEDURE, name=name, description="How to.",
        language=Language("es"), category="construction", steps=steps,
    )


def _definition(**overrides) -> BundlePersonaDefinition:
    defaults = dict(
        name="Profesora Sofía",
        system_prompt="You teach Spanish.",
        languages=(Language("es"),),
        response_language=Language("es"),
        voices={"target_teacher": "ef_dora"},
        settings={"elicitation_cap": 2, "pair_difficulty": {"en": 1.0, "*": 1.5}},
        strategy="language_tutor",
    )
    defaults.update(overrides)
    return BundlePersonaDefinition(**defaults)


def _bundle(
    persona: BundlePersonaDefinition | None = None,
    lessons: tuple[BundleLesson, ...] | None = None,
) -> PersonaBundle:
    if lessons is None:
        lessons = (BundleLesson(filename="01_greetings.toml", items=(_concept_item("hola"),)),)
    return PersonaBundle(
        persona_key=PERSONA_KEY,
        name="spanish-a1",
        version="1.0.0",
        author="meo",
        description="Spanish A1 test bundle.",
        manifest={"bundle": {"name": "spanish-a1"}, "provenance": {"generator_model": "claude-fable-5"}},
        persona=persona,
        lessons=lessons,
    )


def _existing_persona() -> AssistantPersona:
    now = datetime.now(UTC)
    return AssistantPersona(
        id=uuid4(), name="Profesora Sofía", system_prompt="Existing definition.",
        languages=[Language("es"), Language("fr")], response_language=Language("es"),
        voices={"default": "ff_siwis"}, is_system=False, created_at=now, updated_at=now,
        persona_key=PERSONA_KEY,
    )


class _Harness:
    def __init__(
        self,
        bundle: PersonaBundle,
        user: User | None = "unset",
        memory_repo: FakeMemoryRepository | None = None,
        installed_languages: list[Language] | None = None,
    ) -> None:
        if user == "unset":
            user = User(id=uuid4(), primary_language=Language("fr"))
        self.source = FakePersonaBundleSource(bundle)
        self.persona_repo = FakePersonaRepository()
        self.user_repo = FakeUserRepository(user)
        self.memory_repo = memory_repo if memory_repo is not None else FakeMemoryRepository()
        self.unit_of_work = FakeUnitOfWork()
        self.install_log = FakeBundleInstallLog()
        self.derived_voice_requests: list[Language] = []
        self.use_case = InstallPersonaBundle(
            bundle_source=self.source,
            persona_repo=self.persona_repo,
            user_repo=self.user_repo,
            upserter=MemoryUpserter(
                self.memory_repo, FakeEmbeddingService(),
                FakeDisambiguationEvaluator(), FakeMemorySynthesizer(),
            ),
            unit_of_work=self.unit_of_work,
            install_log=self.install_log,
            default_voice_for=self._derive_voice,
            installed_languages=installed_languages,
        )

    def _derive_voice(self, language: Language) -> str:
        self.derived_voice_requests.append(language)
        return "ff_siwis"


class TestPersonaCreation:
    def test_creates_persona_from_definition_when_absent(self):
        """Spec: FR-601, TR-904"""
        harness = _Harness(_bundle(persona=_definition()))
        result = harness.use_case.execute(BUNDLE_PATH)

        assert result.persona_created is True
        created = harness.persona_repo.get_by_key(PERSONA_KEY)
        assert created is not None
        assert created.id == result.persona_id
        assert created.name == "Profesora Sofía"
        assert created.is_system is False
        assert created.response_language == Language("es")
        # [persona.settings] copied verbatim — opaque to generic code.
        assert created.settings == {"elicitation_cap": 2, "pair_difficulty": {"en": 1.0, "*": 1.5}}
        # strategy passed through — resolved against the registry at server startup.
        assert created.strategy == "language_tutor"

    def test_derives_default_voice_from_primary_language_when_omitted(self):
        """Spec: TR-903, TR-904"""
        harness = _Harness(_bundle(persona=_definition()))
        harness.use_case.execute(BUNDLE_PATH)

        created = harness.persona_repo.get_by_key(PERSONA_KEY)
        assert created.voices == {"target_teacher": "ef_dora", "default": "ff_siwis"}
        assert harness.derived_voice_requests == [Language("fr")]

    def test_keeps_bundle_default_voice_when_provided(self):
        """Spec: TR-903"""
        definition = _definition(voices={"default": "em_alex", "target_teacher": "ef_dora"})
        harness = _Harness(_bundle(persona=definition))
        harness.use_case.execute(BUNDLE_PATH)

        created = harness.persona_repo.get_by_key(PERSONA_KEY)
        assert created.default_voice == "em_alex"
        assert harness.derived_voice_requests == []

    def test_languages_are_bundle_targets_plus_primary_language(self):
        """Spec: TR-904"""
        harness = _Harness(_bundle(persona=_definition()))
        harness.use_case.execute(BUNDLE_PATH)
        created = harness.persona_repo.get_by_key(PERSONA_KEY)
        assert created.languages == [Language("es"), Language("fr")]

    def test_primary_language_not_duplicated_when_already_a_target(self):
        """Spec: TR-904"""
        definition = _definition(languages=(Language("es"), Language("fr")))
        harness = _Harness(_bundle(persona=definition))
        harness.use_case.execute(BUNDLE_PATH)
        created = harness.persona_repo.get_by_key(PERSONA_KEY)
        assert created.languages == [Language("es"), Language("fr")]

    def test_fails_when_persona_absent_and_no_definition(self):
        """Spec: FR-601, TR-904"""
        harness = _Harness(_bundle(persona=None))
        with pytest.raises(BundleInstallError, match=PERSONA_KEY):
            harness.use_case.execute(BUNDLE_PATH)

    def test_fails_when_creating_before_onboarding(self):
        """Spec: FR-605, TR-904"""
        harness = _Harness(_bundle(persona=_definition()), user=User(id=uuid4(), primary_language=None))
        with pytest.raises(BundleInstallError, match="onboarding"):
            harness.use_case.execute(BUNDLE_PATH)

    def test_fails_when_target_language_not_installed(self):
        """Spec: FR-609, TR-904 — a bundle whose target language has no TTS voice on
        this machine fails at install with a pointer at the wizard, not at lesson
        time with a missing-voice synthesis error."""
        harness = _Harness(
            _bundle(persona=_definition()),  # target: es
            installed_languages=[Language("en"), Language("fr")],
        )
        with pytest.raises(BundleInstallError, match="es.*memai-setup"):
            harness.use_case.execute(BUNDLE_PATH)
        assert harness.persona_repo.get_by_key(PERSONA_KEY) is None  # nothing created

    def test_installs_when_target_language_installed(self):
        """Spec: FR-609"""
        harness = _Harness(
            _bundle(persona=_definition()),
            installed_languages=[Language("es"), Language("fr")],
        )
        result = harness.use_case.execute(BUNDLE_PATH)
        assert result.persona_created is True


class TestExistingPersonaAttach:
    def test_attaches_content_without_touching_existing_definition(self):
        """Spec: FR-601, TR-904"""
        harness = _Harness(_bundle(persona=_definition()))
        existing = _existing_persona()
        harness.persona_repo.save(existing)

        result = harness.use_case.execute(BUNDLE_PATH)

        assert result.persona_created is False
        assert result.persona_id == existing.id
        assert len(harness.persona_repo.list_all()) == 1
        # Upgrade semantics deferred: existing definition kept untouched, notice raised.
        assert harness.persona_repo.get(existing.id).system_prompt == "Existing definition."
        assert any("[persona]" in notice for notice in result.notices)

    def test_content_only_bundle_attaches_silently(self):
        """Spec: FR-601"""
        harness = _Harness(_bundle(persona=None))
        harness.persona_repo.save(_existing_persona())

        result = harness.use_case.execute(BUNDLE_PATH)

        assert result.persona_created is False
        assert result.notices == ()


class TestItemInstallation:
    def test_items_inserted_unseen_with_persona_id(self):
        """Spec: FR-602, INV-12"""
        lessons = (
            BundleLesson(
                filename="01_greetings.toml",
                items=(_concept_item("hola", category="function_word"), _procedure_item("greeting politely")),
            ),
        )
        harness = _Harness(_bundle(persona=_definition(), lessons=lessons))
        result = harness.use_case.execute(BUNDLE_PATH)

        concept = harness.memory_repo.concepts[0]
        assert concept.persona_id == result.persona_id
        assert concept.engagement_level == EngagementLevel.UNSEEN
        assert concept.category == "function_word"
        assert concept.language == Language("es")
        assert concept.embedding is not None  # computed at install

        procedure = harness.memory_repo.procedures[0]
        assert procedure.engagement_level == EngagementLevel.UNSEEN
        assert procedure.steps == ["paso uno"]

    def test_insertion_order_follows_lessons_then_items(self):
        """Spec: FR-606, INV-11, TR-904 — Insertion order is the contract: curriculum order survives as ascending ids."""
        lessons = (
            BundleLesson(filename="01_first.toml", items=(_concept_item("uno"), _concept_item("dos"))),
            BundleLesson(filename="02_second.toml", items=(_concept_item("tres"),)),
        )
        harness = _Harness(_bundle(persona=_definition(), lessons=lessons))
        harness.use_case.execute(BUNDLE_PATH)

        names = [c.name for c in harness.memory_repo.concepts]
        ids = [c.id for c in harness.memory_repo.concepts]
        assert names == ["uno", "dos", "tres"]
        assert ids == sorted(ids)

    def test_one_unit_of_work_per_lesson(self):
        """Spec: TR-904"""
        lessons = (
            BundleLesson(filename="01_first.toml", items=(_concept_item("uno"),)),
            BundleLesson(filename="02_second.toml", items=(_concept_item("dos"),)),
            BundleLesson(filename="03_third.toml", items=(_concept_item("tres"),)),
        )
        harness = _Harness(_bundle(persona=_definition(), lessons=lessons))
        harness.use_case.execute(BUNDLE_PATH)
        assert harness.unit_of_work.enter_count == 3

    def test_counts_split_inserted_vs_merged(self):
        """Spec: TR-905"""
        class _CannedSearch(FakeMemoryRepository):
            """First item finds an exact duplicate (merge); the rest insert as new."""
            def search(self, embedding, memory_types, top_n, persona_id=None):
                if not self.concepts:
                    return [(1.0, Concept(
                        id=42, persona_id=persona_id, name="hola",
                        description="A description.", language=Language("es"),
                    ))]
                return []

        lessons = (
            BundleLesson(filename="01_first.toml", items=(_concept_item("hola"), _concept_item("adiós"))),
        )
        harness = _Harness(_bundle(persona=_definition(), lessons=lessons), memory_repo=_CannedSearch())
        result = harness.use_case.execute(BUNDLE_PATH)

        assert result.items_merged == 1
        assert result.items_inserted == 1


class TestSameRunSiblingExclusion:
    """Bundle authors write short, structurally similar but deliberately distinct items
    (e.g. two one-line "regular -are verb" definitions) — a later item in the same run
    must not be allowed to merge into an earlier item's row just inserted moments ago,
    even when the (embedding, disambiguator) pipeline would otherwise call it a match.
    """

    def test_high_similarity_sibling_from_same_run_still_inserts_separately(self):
        """Spec: FR-604, TR-904"""
        class _CannedSearch(FakeMemoryRepository):
            """No existing content for the first item; from the second item onward,
            returns whatever was inserted so far as an auto-merge-band match — simulating
            the parlare/mangiare false positive if same-run exclusion didn't exist."""

            def search(self, embedding, memory_types, top_n, persona_id=None):
                if not self.concepts:
                    return []
                return [(0.95, self.concepts[-1])]

        lessons = (
            BundleLesson(filename="03_verbi.toml", items=(_concept_item("parlare"), _concept_item("mangiare"))),
        )
        harness = _Harness(_bundle(persona=_definition(), lessons=lessons), memory_repo=_CannedSearch())
        result = harness.use_case.execute(BUNDLE_PATH)

        assert result.items_inserted == 2
        assert result.items_merged == 0
        assert [c.name for c in harness.memory_repo.concepts] == ["parlare", "mangiare"]

    def test_still_merges_against_content_predating_this_run(self):
        """Spec: FR-604, FR-603 — The exclusion only covers items inserted THIS run — a genuine pre-existing
        match (an earlier install, an earlier bundle, live extraction) still merges."""

        class _CannedSearch(FakeMemoryRepository):
            def search(self, embedding, memory_types, top_n, persona_id=None):
                return [(0.95, self._pre_existing)]

        repo = _CannedSearch()
        repo._pre_existing = Concept(
            id=42, persona_id=uuid4(), name="hola", description="A description.", language=Language("es"),
        )
        lessons = (BundleLesson(filename="01_greetings.toml", items=(_concept_item("hola"),)),)
        harness = _Harness(_bundle(persona=_definition(), lessons=lessons), memory_repo=repo)
        result = harness.use_case.execute(BUNDLE_PATH)

        assert result.items_inserted == 0
        assert result.items_merged == 1


class TestProvenanceLog:
    def test_appends_one_record_with_counts_and_manifest(self):
        """Spec: FR-607, TR-905"""
        harness = _Harness(_bundle(persona=_definition()))
        harness.use_case.execute(BUNDLE_PATH)

        assert len(harness.install_log.records) == 1
        record = harness.install_log.records[0]
        assert record.persona_key == PERSONA_KEY
        assert record.bundle_name == "spanish-a1"
        assert record.bundle_version == "1.0.0"
        assert record.bundle_author == "meo"
        assert record.items_inserted == 1
        assert record.items_merged == 0
        assert record.manifest["provenance"]["generator_model"] == "claude-fable-5"

    def test_loads_bundle_from_given_path(self):
        """Spec: TR-904"""
        harness = _Harness(_bundle(persona=_definition()))
        harness.use_case.execute(BUNDLE_PATH)
        assert harness.source.loaded_paths == [BUNDLE_PATH]
