from __future__ import annotations

from jg.lint import check_html, check_text


def test_finds_forbidden_words_case_insensitive():
    findings = check_text("Wydatki wzrosły aż o 40%.\nNiestety plan nie został wykonany.")
    assert [(f.line, f.word.lower()) for f in findings] == [(1, "aż"), (2, "niestety")]


def test_no_false_positive_inside_words():
    assert check_text("Wartość: 27% planu; rekordów: 3 012; ukrywanie nie dotyczy.") == []


def test_html_ignores_markup_and_scripts():
    html = '<p class="aż">Plan</p><script>var niestety=1;</script><p>Dochody ogółem</p>'
    assert check_html(html) == []
    assert [f.word for f in check_html("<p>wynik rekordowy</p>")] == ["rekordowy"]


def test_html_entities_and_soft_hyphen():
    assert [f.word for f in check_html("<p>wzrost a&#380; o 40%</p>")] == ["aż"]
    assert [f.word.lower() for f in check_html("<p>Nie&shy;stety</p>")] == ["niestety"]


def test_non_utf8_file_does_not_crash(tmp_path):
    from jg.lint import check_paths

    f = tmp_path / "mf.csv"
    f.write_bytes("Gmina;niestety".encode("cp1250") + b"\xff")
    assert [x.word for x in check_paths([f])] == ["niestety"]
