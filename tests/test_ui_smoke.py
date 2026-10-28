"""Playwright smoke test of the web UI (``ui/`` build served at ``/ui/``) against the live
service (``uv run autoontology up``) and the existing data (e.g. ``hanbit`` + ``hanbit-docs``).

Run with: AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration tests/test_ui_smoke.py
(browsers: ``uv run playwright install chromium``).
"""

import httpx
import pytest

from autoontology.config import Settings

pytestmark = pytest.mark.integration

#: Headless Chromium has no GPU: software WebGL (SwiftShader through ANGLE) for the 3D graph.
GL_ARGS = ["--use-angle=swiftshader", "--use-gl=angle", "--enable-unsafe-swiftshader"]
VIEWPORTS = {"mobile": (375, 812), "tablet": (768, 1024), "desktop": (1280, 800)}
NO_HSCROLL = "document.documentElement.scrollWidth <= window.innerWidth"
#: Elements inside <main> that overflow horizontally. An element may be wider than its box only
#: when it scrolls or clips on purpose (overflow-x auto/scroll/hidden/clip: tables, truncated
#: text) and that container itself stays inside the viewport; anything else is a layout bug.
OVERFLOWING = """() => {
  const vw = window.innerWidth, bad = [];
  const inScroller = (el) => {
    for (let p = el.parentElement; p && p.tagName !== 'MAIN'; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o !== 'visible') return true;
    }
    return false;
  };
  for (const el of document.querySelectorAll('main *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || inScroller(el)) continue;
    const o = getComputedStyle(el).overflowX;
    const describe = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      '.' + String(el.className).split(' ').slice(0, 3).join('.');
    if (r.right > vw + 1 || r.left < -1) bad.push('outside viewport: ' + describe);
    else if (el.scrollWidth > el.clientWidth + 1 && o === 'visible' && el.clientWidth)
      bad.push('overflows: ' + describe + ' ' + el.scrollWidth + '>' + el.clientWidth);
  }
  return bad.slice(0, 10);
}"""


@pytest.fixture(scope="module")
def base_url():
    settings = Settings.from_env()
    try:
        httpx.get(f"{settings.service_url}/health/live", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("service not running (uv run autoontology up)")
    sources = httpx.get(f"{settings.service_url}/api/sources").json()["sources"]
    if not sources:
        pytest.skip("no source to show")
    return settings.service_url, sources


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=GL_ARGS)
        yield browser
        browser.close()


def new_page(browser, viewport):
    width, height = viewport
    page = browser.new_page(viewport={"width": width, "height": height}, locale="ko-KR")
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.errors = errors
    return page


@pytest.mark.parametrize("name", list(VIEWPORTS))
def test_shell_sources_and_settings(base_url, browser, name):
    url, sources = base_url
    page = new_page(browser, VIEWPORTS[name])
    page.goto(f"{url}/")
    page.wait_for_url("**/ui/sources")  # / -> /ui/ (server) -> /ui/sources (client)
    rows = page.locator("#sources-table tbody tr[data-source]")
    rows.first.wait_for()
    assert sorted(rows.evaluate_all("rs => rs.map(r => r.dataset.source)")) == sorted(
        s["name"] for s in sources
    )
    if any(s["name"] == "hanbit" for s in sources):
        assert page.locator("#sources-table").get_by_text("hanbit", exact=True).is_visible()
    page.locator(
        "#status-badge[data-status=ok], #status-badge[data-status=warn], "
        "#status-badge[data-status=error]"
    ).wait_for()
    assert page.evaluate(NO_HSCROLL), "horizontal scroll on 소스"
    assert page.evaluate(OVERFLOWING) == [], f"overflowing elements on 소스 at {name}"

    sidebar = page.locator("[data-slot=sidebar-container]")
    if name == "mobile":
        # the sidebar is a sheet: hidden until the trigger opens it
        assert sidebar.count() == 0 or not sidebar.is_visible()
        page.get_by_role("button", name="메뉴 열기·닫기").click()
        page.locator("[data-mobile=true] a[data-nav=settings]").click()
    else:
        assert sidebar.is_visible()
        page.locator("a[data-nav=settings]").click()
    cards = page.locator("#provider-cards [data-provider]")
    cards.first.wait_for()
    assert sorted(cards.evaluate_all("cs => cs.map(c => c.dataset.provider)")) == [
        "claude",
        "codex",
        "nvidia",
        "ollama",
    ]
    assert page.locator("#precedence-note").is_visible()
    assert page.evaluate(NO_HSCROLL), "horizontal scroll on 설정"
    assert page.evaluate(OVERFLOWING) == [], f"overflowing elements on 설정 at {name}"

    for route in ("runs", "concepts", "exports", "status", "graph", "playground"):
        page.goto(f"{url}/ui/{route}")
        page.locator("main h1").first.wait_for()
        page.wait_for_timeout(300)
        assert page.evaluate(NO_HSCROLL), f"horizontal scroll on {route} at {name}"
        assert page.evaluate(OVERFLOWING) == [], f"overflowing elements on {route} at {name}"
    assert page.errors == []
    page.close()


def test_concept_detail_shows_evidence(base_url, browser):
    url, sources = base_url
    if not any(s.get("concepts") for s in sources):
        pytest.skip("no documents source with concepts")
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/concepts")
    first = page.locator("#concept-list [data-concept]").first
    first.wait_for()
    first.click()
    page.locator("#drawer .quote").first.wait_for()
    assert "concept=" in page.url
    assert page.locator("#drawer").get_by_text("근거").count() >= 1
    assert page.errors == []
    page.close()


def test_run_detail_streams_the_log(base_url, browser):
    url, _ = base_url
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/runs")
    row = page.locator("#runs-table tbody tr[data-run-id]").first
    row.wait_for()
    row.click()
    page.locator("#live-status[data-ended=true]").wait_for(timeout=30000)
    assert page.locator("#live-log[role=log]").inner_text().strip()
    assert page.locator("#live-steps").inner_text().strip()
    assert page.errors == []
    page.close()


STATS = "window.__autoontologyGraphStats"


def test_graph_3d_layers_search_and_detail(base_url, browser):
    url, _ = base_url
    graph = httpx.get(f"{url}/api/graph").json()
    if len(graph["nodes"]) < 80:
        pytest.skip("needs the hanbit + hanbit-docs data (>= 80 graph nodes)")
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/graph")
    page.wait_for_function(f"{STATS} && {STATS}.nodes > 0", timeout=30000)
    assert page.get_attribute("#graph-page", "data-renderer") == "3d"  # WebGL, not the fallback
    canvas = page.locator("#graph-canvas canvas")
    canvas.first.wait_for()
    assert canvas.first.bounding_box()["width"] >= 1000  # full-bleed under the header
    stats = page.evaluate(STATS)
    assert stats["nodes"] >= 80 and stats["links"] >= 60, stats

    page.click("#layer-documents")  # 문서 layer off
    page.wait_for_function(f"{STATS}.nodes < {stats['nodes']}")
    off = page.evaluate(STATS)
    assert off["links"] < stats["links"] and "documents" not in off["layers"], off
    page.click("#layer-documents")
    page.wait_for_function(f"{STATS}.nodes === {stats['nodes']}")

    page.click("#graph-search-open")
    page.fill("[cmdk-input]", "Contracts")
    first = page.locator("[cmdk-item]").first
    first.wait_for()
    page.keyboard.press("Enter")
    detail = page.locator("#graph-detail")
    detail.wait_for()
    assert detail.get_by_role("heading", name="동의어").count() == 1
    assert detail.locator("section", has_text="동의어").locator("[data-slot=badge]").count() >= 1
    assert "public.contracts" in detail.inner_text()
    assert page.evaluate(NO_HSCROLL)
    assert page.errors == []
    page.close()


def test_graph_mobile_sheets(base_url, browser):
    url, _ = base_url
    page = new_page(browser, VIEWPORTS["mobile"])
    page.goto(f"{url}/ui/graph")
    page.wait_for_function(f"{STATS} && {STATS}.nodes > 0", timeout=30000)
    page.click("#graph-controls-open")
    page.locator("[data-slot=sheet-content] #graph-controls").wait_for()
    assert page.evaluate(NO_HSCROLL)
    assert page.errors == []
    page.close()


@pytest.mark.parametrize("name", ["desktop", "mobile"])
def test_playground_sparql_example_shows_rows(base_url, browser, name):
    url, sources = base_url

    def exported(source):
        found = httpx.get(f"{url}/api/exports", params={"source": source}).json()
        return any(f["name"] == "ontology.ttl" for e in found["exports"] for f in e["files"])

    docs = [
        s["name"]
        for s in sorted(sources, key=lambda s: -(s.get("concepts") or 0))
        if s["kind"] == "documents" and s.get("concepts") and exported(s["name"])
    ]
    if not docs:
        pytest.skip("no documents source with concepts and an exported ontology.ttl")
    page = new_page(browser, VIEWPORTS[name])
    page.goto(f"{url}/ui/playground?source={docs[0]}&tab=sparql")
    page.locator("#playground-provider").wait_for()  # header: provider + model
    page.click("#sparql-example")
    page.click("[role=option][data-example=classes]")
    result = page.locator("#sparql-result[data-type=SELECT]")
    result.wait_for(timeout=30000)
    rows = int(result.locator("[data-rows]").get_attribute("data-rows"))
    assert rows >= 1
    assert result.locator("tbody tr").count() >= 1
    assert "os:" in result.locator("tbody tr").first.inner_text()
    # an invalid query shows the parser error instead of rows
    page.fill("#sparql-query", "SELECT * WHERE {")
    page.click("#sparql-run")
    page.locator("#sparql-error").wait_for()
    for tab in ("chat", "sql", "tests"):
        page.click(f"[data-tab={tab}]")
        page.locator(f"#playground-{tab}").wait_for()
        assert page.evaluate(NO_HSCROLL), f"horizontal scroll on the {tab} tab at {name}"
        assert page.evaluate(OVERFLOWING) == [], f"overflowing elements on {tab} at {name}"
    assert page.errors == []
    page.close()


def test_graph_focus_param_selects_the_node(base_url, browser):
    url, _ = base_url
    graph = httpx.get(f"{url}/api/graph").json()
    terms = [n for n in graph["nodes"] if n["id"].startswith("term-")]
    if not terms:
        pytest.skip("no Term in the graph")
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/graph?focus={terms[0]['id']}")
    detail = page.locator("#graph-detail")
    detail.wait_for(timeout=30000)
    assert terms[0]["label"] in detail.inner_text()
    assert page.errors == []
    page.close()
