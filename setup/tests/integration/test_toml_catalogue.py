from memai_setup.infrastructure.toml_catalogue import TomlCatalogueRepository


def test_stt_catalogue_marks_engines_by_adapter_availability():
    repo = TomlCatalogueRepository()

    entries = {e.engine: e for e in repo.load_stt_catalogue()}

    assert entries["faster-whisper"].has_adapter is True
    assert entries["whisper.cpp"].has_adapter is False


def test_stt_catalogue_shares_whisper_model_sizes_across_engines():
    repo = TomlCatalogueRepository()

    entries = repo.load_stt_catalogue()

    model_names = {m.name for m in entries[0].models}
    assert model_names == {"small", "medium", "large-v3-turbo", "large-v3"}
    for entry in entries[1:]:
        assert entry.models == entries[0].models


def test_llm_catalogue_loads_expected_entry_count():
    repo = TomlCatalogueRepository()

    entries = repo.load_llm_catalogue()

    assert len(entries) == 11
    reasoning_models = {e.model_id for e in entries if e.reasoning}
    assert reasoning_models == {"qwen3:14b"}
