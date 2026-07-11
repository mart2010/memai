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


def test_no_cuda_gpu_falls_back_to_cpu_and_informs_user():
    step = DetectComputeDevice(FakeGPUDetector(vram_gb=None))
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.compute_device == "cpu"
    assert any("CPU" in m for m in prompter.info_messages)
