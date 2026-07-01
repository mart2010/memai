from memai_setup.domain.languages import format_language


def test_format_language_known_code():
    assert format_language("de") == "German (de)"


def test_format_language_unknown_code_falls_back_to_bare_code():
    assert format_language("xx") == "xx (xx)"
