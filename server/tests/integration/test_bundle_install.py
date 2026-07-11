# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""End-to-end bundle install against real Postgres: real TOML reader on the committed
spanish_mini fixture, real repositories/UnitOfWork/pgvector upsert path. Fakes only for
the LLM-dependent ports (disambiguation, synthesis) — same posture as the consolidation
pipeline test. Embeddings use a deterministic hash-seeded fake: distinct texts get
near-orthogonal vectors (auto-insert), identical text gets the identical vector
(reinstall → similarity 1.0 → exact-duplicate merge) — so the full insert/merge
behaviour is exercised without loading the real model."""
import math
import random
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from memai_server.domain.model import EngagementLevel, Language, MemoryType, User
from memai_server.infrastructure.bundle_toml import TomlPersonaBundleSource
from memai_server.infrastructure.postgres import (
    PSBundleInstallLog,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUnitOfWork,
    PSUserRepository,
)
from memai_server.services.bundle_install import InstallPersonaBundle
from memai_server.services.upsert import MemoryUpserter

from tests.fakes.fakes import FakeDisambiguationEvaluator, FakeMemorySynthesizer

FIXTURE_BUNDLE = Path(__file__).parent / "fixtures" / "spanish_mini"
PERSONA_KEY = "memai-test/spanish-mini"
_DIM = 1024


class HashEmbeddingService:
    def embed(self, text: str) -> list[float]:
        rng = random.Random(text)  # str seeds hash deterministically across runs
        v = [rng.gauss(0.0, 1.0) for _ in range(_DIM)]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]


def _make_installer(conn: psycopg.Connection, synthesizer: FakeMemorySynthesizer) -> InstallPersonaBundle:
    return InstallPersonaBundle(
        bundle_source=TomlPersonaBundleSource(),
        persona_repo=PSPersonaRepository(conn),
        user_repo=PSUserRepository(conn),
        upserter=MemoryUpserter(
            PSMemoryRepository(conn),
            HashEmbeddingService(),
            FakeDisambiguationEvaluator(),
            synthesizer,
        ),
        unit_of_work=PSUnitOfWork(conn),
        install_log=PSBundleInstallLog(conn),
        default_voice_for=lambda language: "ff_siwis",
    )


@pytest.fixture
def onboarded_user(pg_conn: psycopg.Connection) -> User:
    user = User(id=uuid4(), primary_language=Language("fr"))
    PSUserRepository(pg_conn).save(user)
    return user


class TestBundleInstallPipeline:
    def test_fresh_install_creates_persona_and_content(
        self, pg_conn: psycopg.Connection, onboarded_user: User
    ) -> None:
        installer = _make_installer(pg_conn, FakeMemorySynthesizer())

        result = installer.execute(FIXTURE_BUNDLE)

        assert result.persona_created is True
        assert result.items_inserted == 5
        assert result.items_merged == 0

        persona = PSPersonaRepository(pg_conn).get_by_key(PERSONA_KEY)
        assert persona is not None
        assert persona.name == "Profesora Mini"
        # Pair-independence: "default" omitted in the bundle, derived at install.
        assert persona.voices == {"target_teacher": "ef_dora", "default": "ff_siwis"}
        # Bundle targets + User.primary_language.
        assert persona.languages == [Language("es"), Language("fr")]
        # Settings copied verbatim, including the learner-language-keyed map.
        assert persona.settings == {
            "elicitation_cap": 2,
            "pair_difficulty": {"en": 1.0, "fr": 1.2, "*": 1.5},
        }

        # Insertion order is the contract: lesson-filename sort → item order → ascending
        # SERIAL id (curriculum order for Phase 12's UNSEEN tiebreak).
        with pg_conn.cursor() as cur:
            cur.execute("SELECT name, engagement_level, category FROM concepts ORDER BY id")
            concepts = cur.fetchall()
            cur.execute("SELECT name, engagement_level, steps FROM procedures ORDER BY id")
            procedures = cur.fetchall()
        assert [c[0] for c in concepts] == ["hola", "buenos días", "el café"]
        assert [p[0] for p in procedures] == ["greeting someone politely", "ordering a coffee"]
        # A bundle cannot claim the user knows things.
        assert all(c[1] == "unseen" for c in concepts)
        assert all(p[1] == "unseen" for p in procedures)
        assert concepts[0][2] == "function_word"
        assert procedures[1][2] == ["perdone", "quisiera un café, por favor", "gracias"]

        # Items are queryable through the real pgvector search path.
        memory_repo = PSMemoryRepository(pg_conn)
        query = HashEmbeddingService().embed(
            "hola: 'Hola' is the universal Spanish greeting, usable at any time of day "
            "in both formal and informal situations."
        )
        [(similarity, found)] = memory_repo.search(
            embedding=query, memory_types=(MemoryType.CONCEPT,), top_n=1, persona_id=persona.id
        )
        assert similarity == pytest.approx(1.0, abs=1e-5)
        assert found.name == "hola"
        assert found.engagement_level == EngagementLevel.UNSEEN

    def test_reinstall_is_idempotent_and_skips_synthesis(
        self, pg_conn: psycopg.Connection, onboarded_user: User
    ) -> None:
        synthesizer = FakeMemorySynthesizer()
        installer = _make_installer(pg_conn, synthesizer)

        first = installer.execute(FIXTURE_BUNDLE)
        second = installer.execute(FIXTURE_BUNDLE)

        assert (first.items_inserted, first.items_merged) == (5, 0)
        # Recovery contract: re-running merges every item into itself…
        assert (second.items_inserted, second.items_merged) == (0, 5)
        # …via the exact-duplicate short-circuit — no LLM synthesis calls at all.
        assert synthesizer.concept_calls == []
        assert synthesizer.procedure_calls == []
        # Persona untouched on reinstall ([persona] ignored, notice raised).
        assert second.persona_created is False
        assert any("[persona]" in notice for notice in second.notices)
        assert len(PSPersonaRepository(pg_conn).list_all()) == 1

        with pg_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM concepts")
            assert cur.fetchone()[0] == 3
            cur.execute("SELECT count(*) FROM procedures")
            assert cur.fetchone()[0] == 2
            # Provenance log is append-only and deliberately NOT a reinstall guard.
            cur.execute("SELECT items_inserted, items_merged FROM bundle_installs ORDER BY id")
            assert cur.fetchall() == [(5, 0), (0, 5)]
