from pathlib import Path

import pytest

from memai_server.infrastructure.config import load_config


def _write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "memai.toml"
    path.write_text(content)
    return path


class TestLoadConfigComputeDevice:
    def test_tts_device_defaults_to_none_when_tts_section_absent(self, tmp_path: Path):
        """Spec: TR-951"""
        path = _write_toml(
            tmp_path,
            """
            [stt]
            device = "cpu"
            compute_type = "int8"
            [llm]
            model = "aya-expanse"
            """,
        )

        cfg = load_config(path)

        assert cfg.tts_device is None

    def test_tts_device_read_from_config_when_present(self, tmp_path: Path):
        """Spec: TR-951"""
        path = _write_toml(
            tmp_path,
            """
            [tts]
            device = "cpu"
            """,
        )

        cfg = load_config(path)

        assert cfg.tts_device == "cpu"

    def test_stt_device_and_compute_type_default_to_cpu_safe_values_when_section_absent(self, tmp_path: Path):
        """Spec: TR-951"""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.stt_device == "cpu"
        assert cfg.stt_compute_type == "int8"


class TestLoadConfigLLMProvider:
    def test_defaults_to_ollama_when_llm_section_absent(self, tmp_path: Path):
        """Spec: FR-707, TR-955 — fully backward compatible with configs that
        predate this setting."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.llm_provider == "ollama"
        assert cfg.llm_base_url is None
        assert cfg.llm_remote_model is None
        assert cfg.llm_api_key is None

    def test_ollama_provider_does_not_require_base_url_or_remote_model(self, tmp_path: Path):
        """Spec: FR-707 — provider="ollama" (default or explicit) keeps using
        llm_model/llm_ollama_host for the live path too, same as before this
        setting existed."""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "ollama"
            model = "aya-expanse"
            """,
        )

        cfg = load_config(path)

        assert cfg.llm_provider == "ollama"
        assert cfg.llm_model == "aya-expanse"

    def test_openai_compatible_provider_reads_base_url_model_and_key(self, tmp_path: Path):
        """Spec: FR-707, TR-955"""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            base_url = "https://openrouter.ai/api/v1"
            remote_model = "meta-llama/llama-3.3-70b-instruct"
            api_key = "sk-example"
            """,
        )

        cfg = load_config(path)

        assert cfg.llm_provider == "openai_compatible"
        assert cfg.llm_base_url == "https://openrouter.ai/api/v1"
        assert cfg.llm_remote_model == "meta-llama/llama-3.3-70b-instruct"
        assert cfg.llm_api_key == "sk-example"

    def test_openai_compatible_provider_allows_missing_api_key(self, tmp_path: Path):
        """Spec: FR-707 — the key is optional even for the remote provider (some
        self-hosted OpenAI-compatible endpoints don't require one)."""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            base_url = "http://localhost:8080/v1"
            remote_model = "local-model"
            """,
        )

        cfg = load_config(path)

        assert cfg.llm_api_key is None

    def test_openai_compatible_provider_without_base_url_raises(self, tmp_path: Path):
        """Spec: FR-707 — fail fast at config-load time rather than at first live turn."""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            remote_model = "some-model"
            """,
        )

        with pytest.raises(RuntimeError, match="base_url"):
            load_config(path)

    def test_openai_compatible_provider_without_remote_model_raises(self, tmp_path: Path):
        """Spec: FR-707"""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            base_url = "https://openrouter.ai/api/v1"
            """,
        )

        with pytest.raises(RuntimeError, match="remote_model"):
            load_config(path)

    def test_invalid_provider_value_raises(self, tmp_path: Path):
        """Spec: FR-707"""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "anthropic-direct"
            """,
        )

        with pytest.raises(RuntimeError, match="ollama.*openai_compatible"):
            load_config(path)


class TestLoadConfigInstalledLanguages:
    def test_installed_languages_read_from_languages_section(self, tmp_path: Path):
        """Spec: TR-951, FR-705"""
        path = _write_toml(
            tmp_path,
            """
            [languages]
            installed = ["en", "fr"]
            """,
        )

        cfg = load_config(path)

        assert cfg.installed_languages == ("en", "fr")

    def test_installed_languages_empty_when_section_absent(self, tmp_path: Path):
        """Spec: TR-951 — configs written before the key existed: empty tuple, the
        composition root then treats all of SUPPORTED_LANGUAGES as installed."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.installed_languages == ()
