from memai_setup.infrastructure.gpu import SystemGPUDetector


def _write_card(drm_root, card_name: str, vendor_id: str | None, **memory_files: int):
    device_dir = drm_root / card_name / "device"
    device_dir.mkdir(parents=True)
    if vendor_id is not None:
        (device_dir / "vendor").write_text(vendor_id)
    for filename, value in memory_files.items():
        (device_dir / filename).write_text(str(value))


def test_no_drm_root_returns_none(tmp_path):
    detector = SystemGPUDetector(drm_root=tmp_path / "does-not-exist")

    assert detector.detect_gpu() is None


def test_no_card_entries_returns_none(tmp_path):
    detector = SystemGPUDetector(drm_root=tmp_path)

    assert detector.detect_gpu() is None


def test_amd_card_reports_vendor_and_summed_vram_plus_gtt(tmp_path):
    _write_card(
        tmp_path,
        "card0",
        vendor_id="0x1002",
        mem_info_vram_total=17_179_869_184,  # 16 GiB
        mem_info_gtt_total=8_589_934_592,  # 8 GiB
    )
    detector = SystemGPUDetector(drm_root=tmp_path)

    detected = detector.detect_gpu()

    assert detected is not None
    assert detected.vendor == "amd"
    assert detected.vram_gb == 24.0


def test_amd_card_without_memory_files_reports_vendor_with_no_estimate(tmp_path):
    _write_card(tmp_path, "card0", vendor_id="0x1002")
    detector = SystemGPUDetector(drm_root=tmp_path)

    detected = detector.detect_gpu()

    assert detected is not None
    assert detected.vendor == "amd"
    assert detected.vram_gb is None


def test_intel_card_reports_vendor_with_no_amd_only_memory_fields(tmp_path):
    _write_card(tmp_path, "card0", vendor_id="0x8086")
    detector = SystemGPUDetector(drm_root=tmp_path)

    detected = detector.detect_gpu()

    assert detected is not None
    assert detected.vendor == "intel"
    assert detected.vram_gb is None


def test_unrecognized_vendor_id_reports_unknown(tmp_path):
    _write_card(tmp_path, "card0", vendor_id="0xffff")
    detector = SystemGPUDetector(drm_root=tmp_path)

    detected = detector.detect_gpu()

    assert detected is not None
    assert detected.vendor == "unknown"


def test_connector_nodes_are_not_mistaken_for_cards(tmp_path):
    # Real sysfs layout includes non-card entries like "card0-DP-1" alongside
    # "card0" — these must never be treated as a second GPU.
    _write_card(tmp_path, "card0", vendor_id="0x1002")
    (tmp_path / "card0-DP-1").mkdir()
    detector = SystemGPUDetector(drm_root=tmp_path)

    detected = detector.detect_gpu()

    assert detected is not None
    assert detected.vendor == "amd"


def test_card_with_no_vendor_file_is_skipped_in_favor_of_the_next_one(tmp_path):
    (tmp_path / "card0" / "device").mkdir(parents=True)  # no vendor file at all
    _write_card(tmp_path, "card1", vendor_id="0x1002")
    detector = SystemGPUDetector(drm_root=tmp_path)

    detected = detector.detect_gpu()

    assert detected is not None
    assert detected.vendor == "amd"
