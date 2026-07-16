import stat
import tomllib
from pathlib import Path

from memai_setup.domain.plan import InstallationPlan
from memai_setup.infrastructure.config_writer import TomlConfigWriter


def _write_and_read(tmp_path: Path, plan: InstallationPlan) -> dict:
    path = tmp_path / "memai.toml"
    TomlConfigWriter(path).write_server_config(plan)
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_writes_cpu_device_and_int8_compute_type_when_plan_says_cpu(tmp_path: Path):
    plan = InstallationPlan(compute_device="cpu")

    config = _write_and_read(tmp_path, plan)

    assert config["stt"]["device"] == "cpu"
    assert config["stt"]["compute_type"] == "int8"


def test_writes_cuda_device_and_float16_compute_type_when_plan_says_cuda(tmp_path: Path):
    plan = InstallationPlan(compute_device="cuda")

    config = _write_and_read(tmp_path, plan)

    assert config["stt"]["device"] == "cuda"
    assert config["stt"]["compute_type"] == "float16"


def test_writes_tts_section_with_matching_device(tmp_path: Path):
    plan = InstallationPlan(compute_device="cpu")

    config = _write_and_read(tmp_path, plan)

    assert config["tts"]["device"] == "cpu"


def test_writes_selected_languages_as_installed_languages(tmp_path: Path):
    """Spec: FR-705 — the wizard's language selection is the installed-languages
    contract the server reads back for onboarding and response mirroring."""
    plan = InstallationPlan(languages=["en", "fr", "es"])

    config = _write_and_read(tmp_path, plan)

    assert config["languages"]["installed"] == ["en", "fr", "es"]


def test_ollama_provider_writes_only_model_no_extra_llm_keys(tmp_path: Path):
    """Spec: FR-707 — the common case's [llm] section is unchanged from before
    this setting existed."""
    plan = InstallationPlan(llm_model_id="aya-expanse", llm_provider="ollama")

    config = _write_and_read(tmp_path, plan)

    assert config["llm"] == {"model": "aya-expanse"}


def test_openai_compatible_provider_writes_provider_base_url_and_remote_model(tmp_path: Path):
    """Spec: FR-707"""
    plan = InstallationPlan(
        llm_model_id="aya-expanse",
        llm_provider="openai_compatible",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_remote_model="meta-llama/llama-3.3-70b-instruct",
    )

    config = _write_and_read(tmp_path, plan)

    assert config["llm"]["model"] == "aya-expanse"
    assert config["llm"]["provider"] == "openai_compatible"
    assert config["llm"]["base_url"] == "https://openrouter.ai/api/v1"
    assert config["llm"]["remote_model"] == "meta-llama/llama-3.3-70b-instruct"
    assert "api_key" not in config["llm"]


def test_openai_compatible_provider_writes_api_key_when_present(tmp_path: Path):
    """Spec: FR-707"""
    plan = InstallationPlan(
        llm_provider="openai_compatible",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_remote_model="some-model",
        llm_api_key="sk-example",
    )

    config = _write_and_read(tmp_path, plan)

    assert config["llm"]["api_key"] == "sk-example"


def test_written_config_is_only_readable_by_owner(tmp_path: Path):
    # database.url may carry a plaintext password (password-auth fallback) —
    # this file must never be group/world-readable.
    path = tmp_path / "memai.toml"
    TomlConfigWriter(path).write_server_config(InstallationPlan())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_permissions_tightened_even_on_a_pre_existing_looser_file(tmp_path: Path):
    path = tmp_path / "memai.toml"
    path.write_text("")
    path.chmod(0o644)

    TomlConfigWriter(path).write_server_config(InstallationPlan())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
