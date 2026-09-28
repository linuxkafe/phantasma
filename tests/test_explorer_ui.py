"""Headless verification of the 3D memory explorer.

Run: pytest tests/test_explorer_ui.py

This drives the real page in Chromium with software WebGL and asserts that
the scene actually renders pixels -- a blank canvas is the failure mode that
API tests and syntax checks cannot see.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--use-gl=swiftshader",
    "--enable-unsafe-swiftshader",
]

failures: list[str] = []
passes: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        passes.append(label)
        print(f"  ok   {label}" + (f"  [{detail}]" if detail else ""))
    else:
        failures.append(label)
        print(f"  FAIL {label}" + (f"  [{detail}]" if detail else ""))


def count_lit_pixels(page) -> dict:
    """Count non-backdrop pixels inside a canvas-only region.

    The renderer runs with preserveDrawingBuffer:false, so reading the GL
    framebuffer directly returns a cleared buffer. A clipped page screenshot
    measures what the user actually sees instead. The clip box is chosen to
    avoid every HUD panel (controls end at x=270, details start at x=1056,
    legend sits below y=848).
    """
    from io import BytesIO

    from PIL import Image

    shot = page.screenshot(clip={"x": 320, "y": 220, "width": 720, "height": 480})
    img = Image.open(BytesIO(shot)).convert("RGB")
    pixels = list(img.getdata())
    lit = 0
    colours: dict[tuple[int, int, int], int] = {}
    for r, g, b in pixels:
        if r + g + b > 40:  # backdrop #06060c sums to 24
            lit += 1
            key = (r // 32 * 32, g // 32 * 32, b // 32 * 32)
            colours[key] = colours.get(key, 0) + 1
    top = sorted(colours.items(), key=lambda kv: -kv[1])[:5]
    return {
        "total": len(pixels),
        "lit": lit,
        "ratio": lit / max(len(pixels), 1),
        "distinct": len(colours),
        "top": top,
    }


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        console_errors: list[str] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        page.on(
            "console",
            lambda m: console_errors.append(m.text) if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on(
            "requestfailed",
            lambda r: failed_requests.append(f"{r.url} :: {r.failure}"),
        )

        print("== load ==")
        response = page.goto(f"{BASE}/memory/3d", wait_until="domcontentloaded")
        check(
            "page responds 200",
            response is not None and response.status == 200,
            str(response.status if response else None),
        )

        # the explorer hides #loading once the graph is built
        page.wait_for_function(
            "document.getElementById('loading').style.display === 'none'", timeout=45000
        )
        check("loading overlay hidden (graph built)", True)

        check("no uncaught page errors", not page_errors, "; ".join(page_errors[:3]))
        check(
            "no failed network requests",
            not failed_requests,
            "; ".join(failed_requests[:3]),
        )

        print("== stats rendered from the real payload ==")
        stats = page.evaluate(
            """() => ({
                mem: document.getElementById('s-mem').textContent,
                con: document.getElementById('s-con').textContent,
                link: document.getElementById('s-link').textContent,
                mmd: document.getElementById('s-mmd').textContent,
                fly: document.getElementById('s-fly').textContent,
                dangleHidden: document.getElementById('s-dangle-box').hidden,
                visible: document.getElementById('visible-count').textContent,
            })"""
        )
        check("memory count shown", stats["mem"] == "61", stats["mem"])
        check("mermaid-bearing memories shown", stats["mmd"] == "8", stats["mmd"])
        check("links shown", stats["link"] not in ("", "–"), stats["link"])
        check("dangling-ref badge shown", stats["dangleHidden"] is False)
        check(
            "flybrain reported as absent (table is empty)",
            stats["fly"] == "sem estado",
            stats["fly"],
        )

        print("== the canvas actually draws ==")
        page.wait_for_timeout(1500)
        canvas_present = page.evaluate(
            """() => {
                const c = document.querySelector('#stage canvas');
                if (!c) return {ok: false, why: 'no canvas element'};
                return {ok: true, w: c.width, h: c.height,
                        gl: !!(c.getContext('webgl2') || c.getContext('webgl'))};
            }"""
        )
        check(
            "canvas element exists",
            canvas_present.get("ok") is True,
            str(canvas_present.get("why", "")),
        )
        check("canvas has a webgl context", canvas_present.get("gl") is True)
        check(
            "canvas sized to viewport",
            canvas_present.get("w", 0) > 0 and canvas_present.get("h", 0) > 0,
            f"{canvas_present.get('w')}x{canvas_present.get('h')}",
        )

        pixels = count_lit_pixels(page)
        check(
            "scene renders lit pixels in canvas region",
            pixels["ratio"] > 0.005,
            f"{pixels['lit']}/{pixels['total']} ({pixels['ratio'] * 100:.2f}%) "
            f"distinct={pixels['distinct']}",
        )
        check(
            "scene is not a flat fill (multiple colours)",
            pixels["distinct"] >= 3,
            str([c for c, _ in pixels["top"]]),
        )

        print("== node meshes correspond to payload nodes ==")
        node_info = page.evaluate(
            """() => {
                const s = window.__explorer.state;
                let visible = 0, inScene = 0;
                for (const n of s.payload.nodes) {
                    if (n.degree >= s.minDegree) visible += 1;
                    if (s.meshById.has(n.id)) inScene += 1;
                }
                return {payload: s.payload.nodes.length, links: s.payload.links.length,
                        visible, inScene, meshes: s.meshById.size};
            }"""
        )
        check(
            "payload has the real 242 nodes",
            node_info["payload"] == 242,
            str(node_info["payload"]),
        )
        check(
            "every visible node has a mesh",
            node_info["visible"] == node_info["inScene"],
            f"{node_info['inScene']}/{node_info['visible']}",
        )
        check("link buffer built", node_info["links"] == 259, str(node_info["links"]))

        print("== selection opens the details drawer ==")
        first_memory = page.evaluate(
            "() => window.__explorer.state.payload.nodes.find(n => n.kind === 'memory').id"
        )
        opened = page.evaluate("(id) => window.__explorer.select(id)", first_memory)
        check("select() accepted a memory id", opened is True, first_memory)
        page.wait_for_timeout(400)
        drawer = page.evaluate(
            """() => ({
                open: document.getElementById('details').classList.contains('open'),
                text: document.getElementById('detail-body').innerText.slice(0, 120),
                hasPayload: !!document.querySelector('#detail-body pre.raw'),
            })"""
        )
        check("details drawer opened", drawer["open"] is True)
        check(
            "drawer shows memory content",
            len(drawer["text"]) > 10,
            drawer["text"].replace("\n", " ")[:80],
        )
        check("raw stored payload shown", drawer["hasPayload"] is True)

        print("== mermaid: valid diagram renders ==")
        # Only 3 of the 8 stored diagrams are valid Mermaid; the other 5 were
        # written by the model with syntax Mermaid itself rejects (parens
        # inside [], quoted ids without brackets, `a [label=...]`).
        # mem:73 is `A[Capitalismo Tardio]-->B[Depende de Plataformas Digitais]`.
        page.evaluate("(id) => window.__explorer.select(id)", "mem:73")
        page.wait_for_selector("#btn-render-mermaid", timeout=5000)
        page.click("#btn-render-mermaid")
        page.wait_for_function(
            "document.querySelector('#mermaid-host svg') !== null", timeout=40000
        )
        diagram = page.evaluate(
            """() => {
                const svg = document.querySelector('#mermaid-host svg');
                const box = svg.getBoundingClientRect();
                return {w: box.width, h: box.height,
                        texts: svg.querySelectorAll('text, .nodeLabel, .label').length,
                        summary: document.getElementById('mermaid-host').innerText.slice(0, 60)};
            }"""
        )
        check(
            "valid stored diagram produced a visible svg",
            diagram["w"] > 20 and diagram["h"] > 20,
            f"{diagram['w']:.0f}x{diagram['h']:.0f}",
        )
        check("svg carries node labels", diagram["texts"] > 0, str(diagram["texts"]))
        check(
            "parser summary shown",
            "nós" in diagram["summary"],
            diagram["summary"].replace("\n", " "),
        )

        print("== mermaid: invalid stored diagram degrades honestly ==")
        # mem:71 is `"o capuchinho verde"-->"um projeto de pastelaria vegan"`,
        # which Mermaid's own parser rejects. The UI must say so, not fake it.
        page.evaluate("(id) => window.__explorer.select(id)", "mem:71")
        page.wait_for_selector("#btn-render-mermaid", timeout=5000)
        page.click("#btn-render-mermaid")
        page.wait_for_function(
            "document.querySelector('#mermaid-host .warnbox') !== null", timeout=40000
        )
        bad = page.evaluate(
            """() => ({
                warn: document.querySelector('#mermaid-host .warnbox').innerText.slice(0, 90),
                rawShown: !!document.querySelector('#mermaid-host pre.raw'),
            })"""
        )
        check(
            "unrenderable diagram shows a warning, not a fake picture",
            "não conseguiu desenhar" in bad["warn"],
            bad["warn"].replace("\n", " "),
        )
        check("unrenderable diagram still shows its source", bad["rawShown"] is True)
        check("no crash from the bad diagram", not page_errors, "; ".join(page_errors[:2]))

        print("== folding a diagram into the 3D scene ==")
        before = page.evaluate("() => window.__explorer.state.payload.links.length")
        page.click("#btn-fold-mermaid")
        page.wait_for_timeout(600)
        after = page.evaluate("() => window.__explorer.state.payload.links.length")
        check("folding added mermaid edges", after > before, f"{before} -> {after}")
        report = page.evaluate(
            "() => document.getElementById('mermaid-host').innerText.slice(0, 120)"
        )
        check(
            "fold report shown to the user",
            "integrado" in report.lower(),
            report.replace("\n", " ")[:80],
        )

        print("== filters ==")
        page.evaluate("() => window.__explorer.search('veganismo')")
        page.wait_for_timeout(400)
        filtered = page.evaluate("() => window.__explorer.visibleCount()")
        check("search narrows the scene", 0 < filtered < 242, str(filtered))

        page.evaluate("() => window.__explorer.search('')")
        page.wait_for_timeout(300)
        page.fill("#degree", "6")
        page.dispatch_event("#degree", "input")
        page.wait_for_timeout(400)
        deg_filtered = page.evaluate("() => window.__explorer.visibleCount()")
        check(
            "degree filter hides the single-use tail",
            deg_filtered < 242,
            str(deg_filtered),
        )

        page.uncheck("#k-memory")
        page.wait_for_timeout(300)
        no_mem = page.evaluate(
            """() => [...window.__explorer.state.meshById.values()]
                 .every(m => window.__explorer.state.nodeById.get(m.userData.nodeId).kind !== 'memory')"""
        )
        check("unchecking memories removes every memory mesh", no_mem is True)

        print("== camera and controls ==")
        cam = page.evaluate(
            """() => {
                const s = window.__explorer.state;
                return {hasReset: !!document.getElementById('btn-reset')};
            }"""
        )
        check("camera controls present", cam["hasReset"] is True)
        page.click("#btn-rotate")
        check(
            "auto-rotate toggles",
            page.evaluate("() => window.__explorer.state.autoRotate") is False,
        )
        page.click("#btn-labels")
        check(
            "labels toggle off",
            page.evaluate("() => window.__explorer.state.showLabels") is False,
        )
        page.click("#btn-reset")
        page.wait_for_timeout(900)
        check("no errors after interaction", not page_errors, "; ".join(page_errors[:3]))

        page.screenshot(path="/tmp/opencode/explorer.png", full_page=False)
        print("\nscreenshot: /tmp/opencode/explorer.png")

        browser.close()

    print(f"\n{len(passes)} passed, {len(failures)} failed")
    if failures:
        print("failed:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
