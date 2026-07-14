from pathlib import Path

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
