"""Browser geometry and interaction checks for the active-layer sidebar only."""
import base64
import json
import time

from tests.phase4a_acceptance import wait_for


def check_sidebar_layout(cdp, tab, state, frame, output):
    def js(expression):
        return cdp.js(tab, expression)

    def screenshot(name):
        data = cdp.call("Page.captureScreenshot", {"format": "png"}, tab)["data"]
        (output / name).write_bytes(base64.b64decode(data))

    def resize(width):
        js(f"""(() => {{
            const sidebar = document.querySelector('[data-testid="stSidebar"]');
            sidebar.style.width = '{width}px';
            sidebar.style.minWidth = '0';
            sidebar.style.maxWidth = '{width}px';
            sidebar.style.flex = '0 0 {width}px';
        }})()""")
        time.sleep(.3)

    def inspect():
        return js("""(() => {
            const region = document.querySelector('.st-key-active_map_layers');
            const rows = region.querySelectorAll('[class*="st-key-active_layer_row_"]');
            const rect = element => {
                const r = element.getBoundingClientRect();
                return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};
            };
            return {region:rect(region), rows:Array.from(rows, row => {
                const name = row.querySelector('.geogpt-layer-name');
                return {rect:rect(row), text:row.textContent, name:rect(name),
                    title:name.title, clientWidth:name.clientWidth, scrollWidth:name.scrollWidth,
                    overflow:getComputedStyle(name).textOverflow,
                    buttons:Array.from(row.querySelectorAll('button'))
                    .filter(button => button.getBoundingClientRect().width > 0).map(button => {
                        const icon=button.querySelector('[data-testid="stIconMaterial"]');
                        return {rect:rect(button), icon:rect(icon), glyph:icon.textContent,
                            fontSize:getComputedStyle(icon).fontSize,
                            background:getComputedStyle(button).backgroundColor};
                    })};
            })};
        })()""")

    initial = state()
    assert initial["layers"] == 3
    wait_for(lambda: js(f"{frame}.document.querySelectorAll('.leaflet-marker-pane > *').length === 2"))
    results = []
    for width in (380, 300, 240, 200, 170, 150):
        resize(width)
        metrics = inspect()
        (output / "last-metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        assert len(metrics["rows"]) == 3, metrics
        for row, layer in zip(metrics["rows"], initial["layer_summaries"]):
            assert row["title"] == layer["name"] and row["overflow"] == "ellipsis"
            assert "sichtbar" not in row["text"] and "ausgeblendet" not in row["text"]
            expected = "visibility" if layer["visible"] else "visibility_off"
            assert [button["glyph"] for button in row["buttons"]] == [expected, "close"], row
            for button in row["buttons"]:
                box, icon = button["rect"], button["icon"]
                assert abs(box["width"] - 32) < .5 and abs(box["height"] - 32) < .5, (width, row)
                assert button["fontSize"] == "20px"
                assert abs((icon["x"] + icon["width"]/2) - (box["x"] + 16)) < 1
                assert abs((icon["y"] + icon["height"]/2) - (box["y"] + 16)) < 1
                assert box["x"] >= row["rect"]["x"] - .5
                assert box["right"] <= row["rect"]["right"] + .5, (width, row)
                assert box["x"] >= metrics["region"]["x"] - .5
                assert box["right"] <= metrics["region"]["right"] + .5
                assert row["name"]["right"] <= box["x"] + .5, (width, row)
            first, second = [button["rect"] for button in row["buttons"]]
            assert first["right"] <= second["x"] or first["bottom"] <= second["y"], (width, row)
        assert metrics["rows"][-1]["scrollWidth"] > metrics["rows"][-1]["clientWidth"]
        assert state()["runs"] == initial["runs"], "Sidebar resizing invoked the backend"
        results.append({"width":width,"metrics":metrics})
        if width in (380, 240, 170, 150):
            screenshot(f"sidebar-{width}.png")

    resize(300)
    def hover(key, text):
        rect = js(f"""(() => {{
            const r=document.querySelector('.st-key-{key} button').getBoundingClientRect();
            return {{x:r.x+r.width/2,y:r.y+r.height/2}};
        }})()""")
        cdp.call("Input.dispatchMouseEvent", {"type":"mouseMoved",**rect}, tab)
        wait_for(lambda: js(f"Array.from(document.querySelectorAll('[role=tooltip]')).some(e=>e.textContent.includes({json.dumps(text)}))"))
        return js(f"getComputedStyle(document.querySelector('.st-key-{key} button')).backgroundColor")

    first, hidden, last = initial["layer_summaries"]
    hover_background = hover("toggle_map_" + first["layer_id"], "Layer ausblenden")
    assert hover_background != "rgba(0, 0, 0, 0)"
    hover("toggle_map_" + hidden["layer_id"], "Layer einblenden")
    hover("del_map_" + last["layer_id"], "Layer entfernen")
    screenshot("tooltips.png")

    def click(key, condition):
        js(f"document.querySelector('.st-key-{key} button').click()")
        wait_for(condition)
        time.sleep(.5)

    def icon(layer_id):
        return js(f"document.querySelector('.st-key-toggle_map_{layer_id} [data-testid=stIconMaterial]').textContent")

    click("toggle_map_" + first["layer_id"], lambda: not state()["layer_summaries"][0]["visible"])
    assert icon(first["layer_id"]) == "visibility_off"
    wait_for(lambda: js(f"{frame}.document.querySelectorAll('.leaflet-marker-pane > *').length === 0"))
    click("toggle_map_" + hidden["layer_id"], lambda: state()["layer_summaries"][1]["visible"])
    assert icon(hidden["layer_id"]) == "visibility"
    wait_for(lambda: js(f"{frame}.document.querySelectorAll('.leaflet-marker-pane > *').length === 2"))
    # Map projection and sidebar summaries must agree on the visible IDs.
    assert state()["stats"]["visible_layers"] == 2
    click("del_map_" + last["layer_id"], lambda: state()["layers"] == 2)
    assert [layer["layer_number"] for layer in state()["layer_summaries"]] == [1, 2]
    assert state()["stats"]["visible_layers"] == 1
    assert len(inspect()["rows"]) == 2
    wait_for(lambda: js(f"{frame}.document.querySelectorAll('.leaflet-marker-pane > *').length === 2 && {frame}.document.querySelectorAll('.leaflet-overlay-pane svg path').length === 0"))
    screenshot("after-actions.png")
    (output / "results.json").write_text(json.dumps({
        "passed":True, "widths":results, "german_tooltips":True,
        "actions_and_icons":True, "initial":initial, "final":state(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
