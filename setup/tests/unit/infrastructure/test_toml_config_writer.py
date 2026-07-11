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
