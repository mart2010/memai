from memai_setup.domain.model import DetectedGPU
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import DetectComputeDevice

from tests.fakes.fakes import FakeGPUDetector, FakeWizardPrompter


def test_cuda_gpu_detected_sets_compute_device_cuda():
    step = DetectComputeDevice(FakeGPUDetector(vram_gb=24))
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.compute_device == "cuda"
    assert prompter.info_messages == []


def test_no_gpu_at_all_falls_back_to_cpu_and_informs_user():
    step = DetectComputeDevice(FakeGPUDetector(vram_gb=None))
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.compute_device == "cpu"
    assert any("CPU" in m for m in prompter.info_messages)
    assert any("No GPU detected" in m for m in prompter.info_messages)


def test_amd_gpu_falls_back_to_cpu_but_names_the_gpu_in_the_message():
    """A real AMD Ryzen AI APU box tested live reported a flat "no GPU
    detected" even though Ollama was accelerating the LLM on it fine — this
    is the fix: compute_device still falls back to cpu (no ROCm STT/TTS
    adapter exists), but the message names the GPU instead of implying
    nothing is there."""
    detector = FakeGPUDetector(vram_gb=None, detected_gpu=DetectedGPU(vendor="amd", vram_gb=24.0))
    step = DetectComputeDevice(detector)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.compute_device == "cpu"
    assert any("AMD" in m and "24" in m for m in prompter.info_messages)


def test_amd_gpu_with_no_memory_estimate_still_named_without_a_figure():
    detector = FakeGPUDetector(vram_gb=None, detected_gpu=DetectedGPU(vendor="amd", vram_gb=None))
    step = DetectComputeDevice(detector)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.compute_device == "cpu"
    assert any("AMD" in m for m in prompter.info_messages)


def test_unknown_vendor_gpu_treated_same_as_no_gpu_in_the_message():
    detector = FakeGPUDetector(vram_gb=None, detected_gpu=DetectedGPU(vendor="unknown", vram_gb=None))
    step = DetectComputeDevice(detector)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.compute_device == "cpu"
    assert any("No GPU detected" in m for m in prompter.info_messages)
