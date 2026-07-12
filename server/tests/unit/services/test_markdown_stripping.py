from memai_server.services.session import _strip_markdown


def test_strips_emphasis_and_code_markers():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("This is **bold** and _italic_ and `code`.") == "This is bold and italic and code."


def test_strips_headers():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("### Updated Profile Brief") == "Updated Profile Brief"


def test_strips_horizontal_rule_line():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("Before\n---\nAfter") == "Before\nAfter"


def test_strips_emoji():
    """Spec: FR-106, TR-308"""
    assert _strip_markdown("Great job! 🎉 Keep going 💾") == "Great job!  Keep going "


def test_real_world_gemma_response():
    """Spec: FR-106, TR-308"""
    # Header marker + its adjoining space are stripped; the space between the emoji and
    # the following word is untouched (only the emoji itself is removed), leaving one
    # leading space — harmless for TTS, unlike the un-stripped "###"/emoji themselves.
    assert _strip_markdown("### 💾 Updated Profile Brief") == " Updated Profile Brief"
