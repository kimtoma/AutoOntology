# SPDX-License-Identifier: MIT
"""Playwright smoke test of the web UI (``ui/`` build served at ``/ui/``) against the live
service (``uv run autoontology up``) and a built ontology (e.g. ``hanbit`` + ``hanbit-docs``,
built and exported once).

Run with: AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration tests/test_ui_smoke.py
(browsers: ``uv run playwright install chromium``). With ``AUTOONTOLOGY_SCREENSHOTS=1`` the
last test also rewrites ``docs/screenshots/{home,ontology,graph,exports}.png`` at 1280 px.
"""

import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from autoontology.config import Settings

pytestmark = pytest.mark.integration

#: Headless Chromium has no GPU: software WebGL (SwiftShader through ANGLE) for the 3D graph.
GL_ARGS = ["--use-angle=swiftshader", "--use-gl=angle", "--enable-unsafe-swiftshader"]
VIEWPORTS = {"mobile": (375, 812), "tablet": (768, 1024), "desktop": (1280, 800)}
ROUTES = ("sources", "build", "ontology", "graph", "playground", "exports", "status")
#: sidebar entries (data-nav): the Home page ("Get started", /ui/) comes first
NAV_IDS = ("home", *ROUTES)
STEPS = ["models", "sources", "build", "explore", "export"]
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
STATS = "window.__autoontologyGraphStats"
SCREENSHOTS = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


@pytest.fixture(scope="module")
def live():
    """``(service url, sources, ontology summary)``; skips without a service or a snapshot."""
    settings = Settings.from_env()
    url = settings.service_url
    try:
        httpx.get(f"{url}/health/live", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("service not running (uv run autoontology up)")
    sources = httpx.get(f"{url}/api/sources").json()["sources"]
    if not sources:
        pytest.skip("no source to show")
    resp = httpx.get(f"{url}/api/ontology")
    if resp.status_code == 404:
        pytest.skip("no ontology snapshot (uv run autoontology build)")
    return url, sources, resp.json()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=GL_ARGS)
        yield browser
        browser.close()


def new_page(browser, viewport):
    width, height = viewport
    page = browser.new_page(viewport={"width": width, "height": height}, locale="en-US")
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.errors = errors
    return page


def assert_fits(page, where):
    assert page.evaluate(NO_HSCROLL), f"horizontal scroll on {where}"
    assert page.evaluate(OVERFLOWING) == [], f"overflowing elements on {where}"


@pytest.mark.parametrize("name", list(VIEWPORTS))
def test_shell_sources_and_status(live, browser, name):
    url, sources, _ = live
    page = new_page(browser, VIEWPORTS[name])
    page.goto(f"{url}/")
    page.wait_for_url("**/ui/")  # / -> /ui/ (server): the Get started page
    page.locator("#checklist > li[data-step]").first.wait_for()
    page.goto(f"{url}/ui/sources")
    rows = page.locator("#sources-table tbody tr[data-source]")
    rows.first.wait_for()
    assert sorted(rows.evaluate_all("rs => rs.map(r => r.dataset.source)")) == sorted(
        s["name"] for s in sources
    )
    for s in sources:  # one kind badge per row: structured or documents
        family = "documents" if s["kind"] == "documents" else "structured"
        assert page.locator(f"tr[data-source='{s['name']}'] [data-kind={family}]").count() == 1
    page.locator(
        "#status-badge[data-status=ok], #status-badge[data-status=warn], "
        "#status-badge[data-status=error]"
    ).wait_for()
    assert page.get_by_role("heading", name="Sources").is_visible()
    assert_fits(page, f"sources at {name}")

    sidebar = page.locator("[data-slot=sidebar-container]")
    if name == "mobile":
        # the sidebar is a sheet: hidden until the trigger opens it
        assert sidebar.count() == 0 or not sidebar.is_visible()
        page.get_by_role("button", name="Toggle menu").click()
        page.locator("[data-mobile=true] a[data-nav=status]").click()
    else:
        assert sidebar.is_visible()
        page.locator("a[data-nav=status]").click()
    checks = page.locator("#status-table [data-check]")
    checks.first.wait_for()
    names = checks.evaluate_all("cs => cs.map(c => c.dataset.check)")
    assert {"backend", "postgres", "kordoc"} <= set(names), names
    assert page.locator("#status-meta").get_by_text("Chat").is_visible()
    assert page.get_by_role("combobox").count() == 0  # read-only: no provider switching
    assert_fits(page, f"status at {name}")

    # the add dialog: kind selector and the kordoc hint
    page.goto(f"{url}/ui/sources")
    page.click("#btn-new-source")
    page.locator("#form-source").wait_for()
    page.click("#source-kind [data-family=documents]")
    assert "HWP" in page.locator("#source-kind-hint").inner_text()
    page.keyboard.press("Escape")

    for route in ("", *ROUTES):
        page.goto(f"{url}/ui/{route}")
        page.locator("main h1").first.wait_for()
        page.wait_for_timeout(400)
        assert_fits(page, f"{route} at {name}")
    nav = page.locator("a[data-nav]").evaluate_all("as => as.map(a => a.dataset.nav)")
    if name != "mobile":
        assert nav == list(NAV_IDS), nav
    assert page.errors == []
    page.close()


def models_ready(status):
    """The Home page's rule for step 1 (ui/src/lib/checklist.ts::modelsReady)."""
    checks = status["checks"]
    chat = status["providers"].get("chat") or {"available": True, "logged_in": True}
    return (
        checks.get("backend", {}).get("status") == "ok"
        and checks.get("postgres", {}).get("status") == "ok"
        and chat["available"]
        and chat["logged_in"]
        and checks.get("ollama", {"status": "ok"})["status"] == "ok"
    )


@pytest.mark.parametrize("name", list(VIEWPORTS))
def test_home_checklist_reflects_the_api(live, browser, name):
    url, sources, summary = live
    status = httpx.get(f"{url}/api/status").json()
    page = new_page(browser, VIEWPORTS[name])
    page.goto(f"{url}/ui/")
    cards = page.locator("#checklist > li[data-step]")
    cards.first.wait_for()
    assert cards.evaluate_all("cs => cs.map(c => c.dataset.step)") == STEPS
    models = page.locator("[data-step=models]")
    want = "done" if models_ready(status) else "active"
    page.locator(f"[data-step=models][data-state={want}]").wait_for(timeout=15000)
    chat = status["providers"]["chat"]
    assert models.locator("#provider-chat").inner_text() == f"{chat['name']} · {chat['model']}"
    assert (
        models.locator("[data-service=backend]").get_attribute("data-status")
        == (status["checks"]["backend"]["status"])
    )
    if want == "active":  # not ready: the fix is shown as code
        assert "uv run autoontology down && uv run autoontology up" in models.inner_text()
    # sources and a snapshot exist (the live fixture): steps 2-4 are done
    for step in ("sources", "build", "explore"):
        page.locator(f"[data-step={step}][data-state=done]").wait_for(timeout=15000)
    counts = page.locator("#explore-counts").inner_text()
    assert (
        f"{summary['counts']['classes_structured']} business concepts found in your tables"
        in counts
    )
    page.locator("[data-help=build]").click()
    page.locator("[data-help-content=build]").wait_for()
    page.keyboard.press("Escape")
    assert page.get_attribute("#guide-link", "href").endswith("/docs/GUIDE.md")
    assert_fits(page, f"home at {name}")
    assert page.errors == []
    page.close()


def test_home_sample_dialog_lists_the_sets(live, browser):
    url, _, _ = live
    sets = httpx.get(f"{url}/api/samples").json()
    if not sets:
        pytest.skip("no sample sets next to the service")
    page = new_page(browser, VIEWPORTS["mobile"])
    page.goto(f"{url}/ui/")
    page.click("#btn-load-samples")
    page.locator("#sample-sets [data-sample-set]").first.wait_for()
    shown = page.locator("#sample-sets [data-sample-set]").evaluate_all(
        "ls => ls.map(l => l.dataset.sampleSet)"
    )
    assert shown == [s["set"] for s in sets], shown
    for s in sets:
        card = page.locator(f"[data-sample-set={s['set']}]")
        for src in s["sources"]:
            text = card.locator(f"[data-sample-source='{src['name']}']").inner_text()
            assert str(src["files"]) in text and all(ext in text for ext in src["formats"]), text
        assert "Expected build time" in card.locator("[data-expected-time]").inner_text()
    # loading is intercepted (no data changes): a 409 sample_conflict first, then success
    first = sets[0]
    bodies = []

    def fake_load(route):
        bodies.append(json.loads(route.request.post_data or "{}"))
        if len(bodies) == 1:
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps(
                    {
                        "error": "sample_conflict",
                        "detail": 'source(s) x already exist; send "replace": true',
                        "conflicts": [
                            {"name": src["name"], "kind": src["kind"], "registered": "/elsewhere"}
                            for src in first["sources"]
                        ],
                    }
                ),
            )
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "set": first["set"],
                        "messages": [],
                        "sources": [
                            {"name": src["name"], "kind": src["kind"], "action": "replaced"}
                            for src in first["sources"]
                        ],
                    }
                ),
            )

    page.route("**/api/samples/load", fake_load)
    card = page.locator(f"[data-sample-set={first['set']}]")
    button = card.locator(f"[data-load-set={first['set']}]")
    button.click()
    panel = page.locator("#sample-conflict")
    panel.wait_for()
    for src in first["sources"]:
        assert panel.locator(f"[data-conflict='{src['name']}']").count() == 1
    assert "/elsewhere" not in page.locator("#samples-dialog").inner_text()
    page.click("#btn-sample-replace")
    page.locator("#samples-result").wait_for()
    assert bodies == [{"set": first["set"]}, {"set": first["set"], "replace": True}], bodies
    for src in first["sources"]:
        assert (
            page.locator(f"[data-loaded-source='{src['name']}']").inner_text().endswith("replaced")
        )
    assert page.evaluate(NO_HSCROLL)
    assert page.errors == []
    page.close()


def test_sources_preview_classifies_files(live, browser, tmp_path):
    url, _, _ = live
    (tmp_path / "manual.doc").write_bytes(b"x")
    (tmp_path / "data.xyz").write_bytes(b"x")
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/sources")
    page.click("#btn-new-source")
    page.click("#source-kind [data-family=documents]")
    accept = page.get_attribute("#source-files", "accept")
    formats = httpx.get(f"{url}/api/status").json()["formats"]
    assert accept == ",".join(sorted(formats["documents"])), accept
    page.set_input_files(
        "#source-files", [str(tmp_path / "manual.doc"), str(tmp_path / "data.xyz")]
    )
    doc = page.locator("[data-file='manual.doc']")
    doc.wait_for()
    assert doc.get_attribute("data-state") == "supported"
    assert doc.get_attribute("data-engine") == "native"
    xyz = page.locator("[data-file='data.xyz']")
    assert xyz.get_attribute("data-state") == "unsupported"
    assert "cannot be read" in xyz.inner_text()
    page.fill("#source-name", "preview-only")
    assert page.locator("#form-source button[type=submit]").is_disabled()  # never sent
    page.click("#source-kind [data-family=structured]")
    hint = page.locator("#source-kind-hint").inner_text()
    assert "XLS" in hint and "SQLite database URL" not in hint, hint
    assert page.errors == []
    page.close()


def test_ontology_list_by_origin(live, browser):
    url, _, summary = live
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/ontology")
    page.locator(f"#ontology-summary[data-revision='{summary['revision']}']").wait_for()
    page.locator("#concept-list [data-concept]").first.wait_for()
    shown = {}
    for origin in ("structured", "document", "aligned"):
        api_total = httpx.get(
            f"{url}/api/ontology/concepts", params={"origin": origin, "size": 1}
        ).json()["total"]
        page.goto(f"{url}/ui/ontology?origin={origin}")
        if api_total:
            items = page.locator(f"#concept-list [data-concept][data-origin={origin}]")
            items.first.wait_for()
            assert page.locator("#concept-list [data-concept]").count() == items.count()
            # and each item carries the origin badge
            assert items.first.locator(f"[data-slot=badge][data-origin={origin}]").count() == 1
        else:
            page.get_by_text("No matching concepts").wait_for()
        shown[origin] = api_total
        summary_text = page.locator("#concept-summary").inner_text()
        assert summary_text.startswith(f"{api_total} concept"), (origin, summary_text)
    # the T2 sample data: documents and aligned Terms (all 3 Terms fold a `same` concept)
    assert shown["document"] >= 50 and shown["aligned"] >= 1, shown
    print(f"origins in the UI list: {shown}")
    assert page.errors == []
    page.close()


def test_concept_detail_shows_evidence(live, browser):
    url, _, _ = live
    items = httpx.get(f"{url}/api/ontology/concepts", params={"size": 200}).json()["items"]
    with_evidence = [i for i in items if i["evidence_count"] and i["origin"] == "document"]
    if not with_evidence:
        pytest.skip("no document concept with evidence")
    target = with_evidence[0]
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/ontology?q={quote(target['name'])}")
    item = page.locator(f"#concept-list [data-concept='{target['id']}']")
    item.wait_for()
    item.click()
    page.locator("#drawer .quote").first.wait_for()
    assert f"concept={target['id']}" in page.url
    assert page.locator("#drawer [data-section=evidence]").get_by_text("Evidence").count() >= 1
    link = page.locator("#concept-graph-link")
    assert link.get_attribute("href") == f"/ui/graph?focus={quote(target['id'], safe='')}"
    # an aligned Term shows its merged concepts and columns
    aligned = [i for i in items if i["origin"] == "aligned"]
    if aligned:
        page.goto(f"{url}/ui/ontology?concept={aligned[0]['id']}")
        page.locator("#drawer [data-section=merged]").wait_for()
        assert page.locator("#drawer [data-section=columns] tbody tr").count() >= 1
    assert page.errors == []
    page.close()


def test_build_page_lists_runs_and_streams_the_log(live, browser):
    url, _, _ = live
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/build")
    page.locator("#btn-build").wait_for()
    assert page.locator("#btn-export").is_enabled()
    row = page.locator("#runs-table tbody tr[data-kind=build]").first
    row.wait_for()
    row.click()
    page.locator("#live-status[data-ended=true]").wait_for(timeout=30000)
    groups = page.locator("#live-steps [data-group]").evaluate_all(
        "gs => gs.map(g => g.dataset.group)"
    )
    assert any(g.startswith("structured:") for g in groups), groups
    assert any(g.startswith("documents:") for g in groups), groups
    assert {"align", "merge", "snapshot"} <= set(groups), groups
    steps = page.locator("#live-steps").inner_text()
    for label in (
        "Load tables",
        "Compile business concepts (LLM)",
        "Extract concepts (LLM)",
        "Match document concepts to table concepts",
        "Build the unified ontology",
        "Save revision",
    ):
        assert label in steps, (label, steps)
    assert "structured:" not in steps and ":compile" not in steps, steps
    assert page.locator("#live-total").inner_text().startswith("Started")
    assert page.locator("#live-log[role=log]").inner_text().strip()
    assert page.errors == []
    page.close()


def test_graph_layers_origins_and_focus(live, browser):
    url, _, _ = live
    graph = httpx.get(f"{url}/api/ontology/graph").json()
    if len(graph["nodes"]) < 60:
        pytest.skip("needs the hanbit + hanbit-docs data (>= 60 graph nodes)")
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/graph")
    page.wait_for_function(f"{STATS} && {STATS}.nodes > 0", timeout=30000)
    assert page.get_attribute("#graph-page", "data-renderer") == "3d"  # WebGL, not the fallback
    canvas = page.locator("#graph-canvas canvas")
    canvas.first.wait_for()
    assert canvas.first.bounding_box()["width"] >= 1000  # full-bleed under the header
    stats = page.evaluate(STATS)
    assert stats["nodes"] >= 60 and stats["links"] >= 40, stats
    assert stats["origins"].get("document", 0) >= 1 and stats["origins"].get("aligned", 0) >= 1
    assert stats["rings"] == stats["origins"]["aligned"], stats  # aligned Terms are ringed
    for key in ("document", "aligned"):
        assert page.locator(f"#graph-legend [data-legend={key}]").count() == 1

    page.click("#layer-structured")
    page.wait_for_function(f"{STATS}.layers.join() === 'structured'")
    structured = page.evaluate(STATS)
    assert structured["nodes"] < stats["nodes"], structured
    page.click("#layer-aligned")
    page.wait_for_function(f"{STATS}.layers.join() === 'aligned'")
    assert page.evaluate(STATS)["origins"].get("aligned", 0) >= 1
    page.click("#layer-all")
    page.wait_for_function(f"{STATS}.nodes === {stats['nodes']}")
    page.click("#layer-evidence")  # adds the source documents
    page.wait_for_function(f"{STATS}.nodes > {stats['nodes']}")
    assert "evidence=1" in page.url

    term = next(n for n in graph["nodes"] if n["origin"] == "aligned")
    page.goto(f"{url}/ui/graph?focus={term['id']}")
    detail = page.locator("#graph-detail")
    detail.wait_for(timeout=30000)
    assert term["label"] in detail.inner_text()
    assert detail.get_attribute("data-origin") == "aligned"
    detail.locator("[data-section=evidence]").wait_for()  # the concept detail loaded

    page.goto(f"{url}/ui/graph?renderer=2d&layer=documents")
    page.wait_for_function(f"{STATS} && {STATS}.renderer === '2d'", timeout=30000)
    assert page.evaluate(STATS)["layers"] == ["documents"]
    assert page.locator("#graph-2d-badge").is_visible()
    assert page.evaluate(NO_HSCROLL)
    assert page.errors == []
    page.close()


def test_graph_mobile_sheets(live, browser):
    url, _, _ = live
    page = new_page(browser, VIEWPORTS["mobile"])
    page.goto(f"{url}/ui/graph")
    page.wait_for_function(f"{STATS} && {STATS}.nodes > 0", timeout=30000)
    page.click("#graph-controls-open")
    page.locator("[data-slot=sheet-content] #graph-controls").wait_for()
    assert page.evaluate(NO_HSCROLL)
    assert page.errors == []
    page.close()


@pytest.mark.parametrize("name", ["desktop", "mobile"])
def test_playground_sparql_example_shows_rows(live, browser, name):
    url, _, _ = live
    page = new_page(browser, VIEWPORTS[name])
    page.goto(f"{url}/ui/playground?tab=sparql")
    page.locator("#playground-provider").wait_for()  # header: provider + model
    tabs = page.locator("#playground-tabs [role=tab]").evaluate_all(
        "ts => ts.map(t => t.dataset.tab)"
    )
    assert tabs == ["chat", "sql", "sparql"], tabs
    page.click("#sparql-example")
    page.click("[role=option][data-example=classes]")
    result = page.locator("#sparql-result[data-type=SELECT]")
    result.wait_for(timeout=30000)
    rows = int(result.locator("[data-rows]").get_attribute("data-rows"))
    assert rows >= 1
    assert result.locator("tbody tr").count() >= 1
    assert "ao:" in result.locator("tbody tr").first.inner_text()
    # an invalid query shows the parser error instead of rows
    page.fill("#sparql-query", "SELECT * WHERE {")
    page.click("#sparql-run")
    page.locator("#sparql-error").wait_for()
    for tab in ("chat", "sql"):
        page.click(f"[data-tab={tab}]")
        page.locator(f"#playground-{tab}").wait_for()
        assert_fits(page, f"the {tab} tab at {name}")
    assert page.errors == []
    page.close()


def test_exports_show_ossie_and_okf_with_validation(live, browser):
    url, _, _ = live
    exports = httpx.get(f"{url}/api/exports").json()["exports"]
    latest = next((e for e in exports if {"ossie", "okf"} <= {d["name"] for d in e["dirs"]}), None)
    if latest is None:
        pytest.skip("no export with ossie/ and okf/ (uv run autoontology export)")
    page = new_page(browser, VIEWPORTS["desktop"])
    page.goto(f"{url}/ui/exports")
    card = page.locator(f"[data-export={latest['dir']}]")
    card.wait_for()
    for sub in ("ossie", "okf"):
        assert card.locator(f"[data-dir={sub}]").count() == 1
        card.locator(f"[data-validation={sub}][data-ok=true]").first.wait_for()
    assert card.locator("[data-ok=false]").count() == 0
    # expand okf/ and preview a Hangul-named concept file
    card.locator("[data-dir=okf] > summary").click()
    hangul = next(f for f in latest["dirs"] if f["name"] == "okf")["files"]
    concept = next(f for f in hangul if f.startswith("concepts/") and not f.isascii())
    card.locator(f"[data-preview='okf/{concept}']").click()
    preview = page.locator("#file-preview")
    preview.wait_for()
    assert "type:" in preview.inner_text()
    page.keyboard.press("Escape")
    card.locator("[data-preview='ontology.ttl']").click()
    page.locator("#file-preview").get_by_text("@prefix").first.wait_for()
    assert page.errors == []
    page.close()


@pytest.mark.skipif(
    not os.environ.get("AUTOONTOLOGY_SCREENSHOTS"), reason="AUTOONTOLOGY_SCREENSHOTS unset"
)
def test_screenshots(live, browser):
    url, _, _ = live
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page = new_page(browser, VIEWPORTS["desktop"])
    page.emulate_media(color_scheme="light")
    shots = {
        "home": ("/ui/", "[data-step=export]:not([data-state=checking])"),
        "ontology": ("/ui/ontology", "#concept-list [data-concept]"),
        "graph": ("/ui/graph", "#graph-canvas canvas"),
        "exports": ("/ui/exports", "[data-validation=okf][data-ok=true]"),
    }
    for name, (path, ready) in shots.items():
        page.goto(f"{url}{path}")
        page.locator(ready).first.wait_for(timeout=30000)
        if name == "graph":
            page.wait_for_function(f"{STATS} && {STATS}.nodes > 0", timeout=30000)
            page.wait_for_timeout(9000)  # layout settled, intro rotation over
        if name == "exports":
            page.locator("[data-dir=ossie] > summary").first.click()
        page.wait_for_timeout(500)
        out = SCREENSHOTS / f"{name}.png"
        page.screenshot(path=str(out))
        assert out.stat().st_size <= 300 * 1024, (out, out.stat().st_size)
    assert page.errors == []
    page.close()
