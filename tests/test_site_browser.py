"""Browser tests of the site (Playwright, Chromium): WCAG 2.1 AA (axe-core), keyboard,
375 px viewport without horizontal scrolling, search, values on the gmina page."""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from tests.site_build import build_site

pytestmark = pytest.mark.browser
AXE = Path(__file__).parent.parent / "node_modules" / "axe-core" / "axe.min.js"
PAGES = [
    "index.html",
    "gminy.html",
    "metodologia.html",
    "gmina/0215011.html",
    "gmina/0264011.html",
    "gmina/1207132.html",
]


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    dist = build_site(tmp_path_factory.mktemp("site"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(dist))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def _axe(page):
    if not AXE.is_file():
        pytest.skip("brak node_modules/axe-core (npm install)")
    page.add_script_tag(path=str(AXE))
    return page.evaluate(
        "async () => (await axe.run(document, {runOnly: {type: 'tag', values: "
        "['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations"
        ".map(v => v.id + ': ' + v.nodes.length + ' ' + v.nodes[0].target)"
    )


@pytest.mark.parametrize("width", [1280, 375])
@pytest.mark.parametrize("path", PAGES)
def test_axe_and_no_horizontal_scroll(browser, base_url, path, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto(base_url + path)
    assert _axe(page) == []
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.close()


def test_keyboard_skip_link_and_focus(browser, base_url):
    page = browser.new_page()
    page.goto(base_url + "gmina/0215011.html")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent") == "Przejdź do treści"
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline != "none"
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash") == "#tresc"
    page.close()


def test_search_by_keyboard(browser, base_url):
    page = browser.new_page()
    page.goto(base_url + "index.html")
    page.focus("#q")
    page.keyboard.type("oław")
    page.wait_for_selector("#wyniki a")
    assert "Oława" in page.inner_text("#wyniki")
    assert "Wyniki" in page.inner_text("#wyniki-status")
    page.keyboard.press("Enter")  # focus on the first result
    page.keyboard.press("Enter")
    page.wait_for_url("**/gmina/*.html")
    assert "Oława" in page.inner_text("h1")
    page.close()


def test_search_without_javascript_goes_to_list(browser, base_url):
    ctx = browser.new_context(java_script_enabled=False)
    page = ctx.new_page()
    page.goto(base_url + "index.html")
    page.fill("#q", "Oława")
    page.click("button[type=submit]")
    page.wait_for_url("**/gminy.html?q=*")
    assert page.locator("a", has_text="Oława").count() >= 1
    ctx.close()


def test_card_values_and_table_toggle(browser, base_url):
    page = browser.new_page()
    page.goto(base_url + "gmina/0215011.html")
    kpi = page.inner_text(".kpis")
    assert "249,71" in kpi and "Rb-27S 2025" in kpi
    page.click("summary")
    assert page.locator("details table tbody tr").count() == 7
    assert page.locator(".firm").count() == 3
    page.close()


def test_csv_download_link_resolves(browser, base_url):
    page = browser.new_page()
    page.goto(base_url + "gmina/0215011.html")
    href = page.get_attribute("a[download]", "href")
    response = page.request.get(base_url + "gmina/" + href)
    assert response.status == 200 and response.text().startswith("teryt,rok,kod")
    page.close()
