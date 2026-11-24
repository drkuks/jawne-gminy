from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from jg.site import build, charts
from tests.site_build import build_site

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    return build_site(tmp_path_factory.mktemp("site"))


def test_tokens_match_guidelines():
    """Local check: design guidelines are kept outside the repository."""
    path = ROOT / "DESIGN-GUIDELINES.md"
    if not path.is_file():
        pytest.skip("DESIGN-GUIDELINES.md is local only")
    guidelines = path.read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)```", guidelines.split("## 2. Tokeny", 1)[1], re.S)
    assert json.loads((ROOT / "site" / "design-tokens.json").read_text()) == json.loads(
        block.group(1)
    )


def test_css_uses_only_token_colors():
    css = (ROOT / "site" / "static" / "site.css").read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", css)
    variables = build.css_variables(build.tokens())
    assert "--accent: #0B6E8A;" in variables and "--layout-maxWidth: 1200px;" in variables


def test_pages_built(dist):
    assert (dist / "index.html").is_file() and (dist / "metodologia.html").is_file()
    pages = list((dist / "gmina").glob("*.html"))
    assert len(pages) == 26
    index = json.loads((dist / "szukaj.json").read_text(encoding="utf-8"))
    assert {
        "n": "Oława",
        "t": "gmina miejska",
        "p": "oławski",
        "w": "dolnośląskie",
        "u": "gmina/0215011.html",
    } in index


def test_olawa_card_values_match_data(dist):
    html = (dist / "gmina" / "0215011.html").read_text(encoding="utf-8")
    nb = " "
    assert f"249{nb}710{nb}017,88{nb}zł" in html  # revenue executed 2025 (Rb-27S)
    assert f"66{nb}393{nb}081,00{nb}zł" in html  # liabilities (Rb-Z, E)
    assert "32 928".replace(" ", nb) in html  # GUS population 31.12.2025
    assert "ZAKŁAD WODOCIĄGÓW I KANALIZACJI" in html and "KRS 0000136724" in html
    assert "gmina miejska, ludność 20" in html


def test_missing_data_shown_with_reason(dist):
    html = (dist / "gmina" / "1207132.html").read_text(encoding="utf-8")
    # Szczawa (created in 2025): earlier years in the chart and table show "brak danych".
    assert "brak danych" in html
    assert ">0</td>" not in html


def test_chart_has_table_and_hatching(dist):
    html = (dist / "gmina" / "1207132.html").read_text(encoding="utf-8")
    assert 'fill="url(#na)"' in html and "<table>" in html


def test_no_forbidden_words_and_lang(dist):
    for page in [dist / "index.html", *(dist / "gmina").glob("*.html")]:
        html = page.read_text(encoding="utf-8")
        assert '<html lang="pl">' in html
        assert 'href="#tresc"' in html


def test_map_projection_bounds():
    x, y = charts.map_position(50.95, 17.29)  # Oława
    assert 20 < x < 40 and 65 < y < 80
    assert charts.strip_position(Decimal(5), Decimal(0), Decimal(10)) == 50.0


def test_build_fails_on_forbidden_word(tmp_path, monkeypatch):
    kpi = (("dochody_wykonanie", "Niestety dochody"), *build.KPI[1:])
    monkeypatch.setattr(build, "KPI", kpi)
    with pytest.raises(build.BuildError, match="słowa zakazane"):
        build_site(tmp_path)


def test_refuses_to_delete_foreign_directory(tmp_path):
    out = tmp_path / "dane"
    out.mkdir()
    (out / "wazne.csv").write_text("x")
    with pytest.raises(build.BuildError, match="nie usuwam"):
        build._wyczysc(out)
    assert (out / "wazne.csv").is_file()


def test_404_uses_absolute_paths(dist):
    html = (dist / "404.html").read_text(encoding="utf-8")
    assert 'href="/static/site.css"' in html and 'href="/index.html"' in html
    assert 'href="gminy.html"' not in html and 'href="index.html"' not in html
    assert (dist / build.MARKER).is_file()


def test_404_with_project_base(tmp_path):
    """Project GitHub Pages: site under /jawne-gminy/, 404 page served under any path."""
    html = (build_site(tmp_path, baza="/jawne-gminy/") / "404.html").read_text(encoding="utf-8")
    assert 'href="/jawne-gminy/static/site.css"' in html
    assert 'href="/jawne-gminy/gminy.html"' in html
    assert re.search(r'(href|src)="/(?!jawne-gminy/)', html) is None


def test_cli_rejects_bad_base():
    from typer.testing import CliRunner

    from jg.cli import app

    def plain(output: str) -> str:  # Rich adds colour codes on CI (GITHUB_ACTIONS)
        return re.sub(r"\x1b\[[0-9;]*m", "", output)

    result = CliRunner().invoke(app, ["serwis", "--baza", "jawne-gminy"])
    assert result.exit_code != 0 and "--baza" in plain(result.output)
    # the run rejects an invalid base before fetching (env as in the workflow)
    result = CliRunner().invoke(app, ["przebieg", "--do", "2024"], env={"JG_BAZA": "jawne-gminy/"})
    assert result.exit_code != 0 and "--baza" in plain(result.output)


def test_mnpp_breadcrumb_and_search(dist):
    html = (dist / "gmina" / "0264011.html").read_text(encoding="utf-8")
    assert "dolnośląskie / miasto na prawach powiatu" in html
    index = json.loads((dist / "szukaj.json").read_text(encoding="utf-8"))
    assert next(g for g in index if g["u"] == "gmina/0264011.html")["p"] == ""


def test_company_finance_has_source(dist):
    html = (dist / "gmina" / "0215011.html").read_text(encoding="utf-8")
    assert "RDF, tryb ręczny" in html and "SHA-256" in html
    assert "e-sprawozdanie finansowe" in html.split('id="dokumenty"')[1]


def test_limit_build_has_no_dead_links(tmp_path):
    import hashlib
    import shutil

    from tests.site_build import FIXTURES

    archive = tmp_path / "archive"
    slownik = (FIXTURES / "mf" / "slownik_jst_2025.xlsx").read_bytes()
    sha = hashlib.sha256(slownik).hexdigest()
    (archive / sha[:2]).mkdir(parents=True)
    (archive / sha[:2] / sha).write_bytes(slownik)
    shutil.copytree(FIXTURES / "site", tmp_path / "data")
    build.build(tmp_path / "data", archive, tmp_path / "out", limit=["0215011"])
    index = json.loads((tmp_path / "out" / "szukaj.json").read_text(encoding="utf-8"))
    assert [g["u"] for g in index] == ["gmina/0215011.html"]


def test_build_without_company_finance_file(tmp_path, monkeypatch):
    import shutil

    import tests.site_build as sb

    src = tmp_path / "fx"
    shutil.copytree(sb.FIXTURES, src)
    (src / "site" / "spolki_finanse.csv").unlink()
    monkeypatch.setattr(sb, "FIXTURES", src)
    out = sb.build_site(tmp_path / "b")
    html = (out / "gmina" / "0215011.html").read_text(encoding="utf-8")
    assert "publikowane z przebiegu lokalnego" in html
