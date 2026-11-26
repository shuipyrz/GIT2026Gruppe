"""Real browser assertions for the three reported layer UI issues."""
import base64
import json
import time

from tests.phase4a_acceptance import wait_for


def check_layer_ui(cdp, tab, state, frame, output):
    def js(expression):
        return cdp.js(tab, expression)
    def screenshot(name):
        (output / name).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png"}, tab)["data"]))
    def send(query):
        before = state()["chat_messages"]
        js("document.querySelector('textarea[data-testid=\"stChatInputTextArea\"]').focus()")
        cdp.call("Input.insertText", {"text": query}, tab)
        for kind in ("keyDown", "keyUp"):
            cdp.call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, tab)
        wait_for(lambda: state()["chat_messages"] == before + 2)
        time.sleep(.5)
    def sidebar_has(number, name):
        return js(f"Array.from(document.querySelectorAll('[data-testid=stSidebar] .geogpt-layer-details')).some(e=>e.textContent.includes('Layer {number}.') && e.textContent.includes({json.dumps(name)}))")
    def click_key(key):
        assert js(f"Boolean(document.querySelector('.st-key-{key} button'))"), key
        js(f"document.querySelector('.st-key-{key} button').click()")

    initial = state()
    wait_for(lambda: js(f"Boolean({frame}.document.querySelector('.leaflet-marker-pane > *'))"))
    position = js(f"""(() => {{
        const outer = document.querySelector('iframe[title="streamlit_folium.st_folium"]').getBoundingClientRect();
        const marker = {frame}.document.querySelector('.leaflet-marker-pane > *').getBoundingClientRect();
        return {{x:outer.x + marker.x + marker.width/2, y:outer.y + marker.y + marker.height/2}};
    }})()""")
    cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **position}, tab)
    wait_for(lambda: js(f"Boolean({frame}.document.querySelector('.leaflet-tooltip'))"))
    hover = js(f"{frame}.document.querySelector('.leaflet-tooltip').textContent.replace(/\\s+/g,' ').trim()")
    for kind in ("mousePressed", "mouseReleased"):
        cdp.call("Input.dispatchMouseEvent", {"type": kind, **position, "button": "left",
                 "buttons": 1 if kind == "mousePressed" else 0, "clickCount": 1}, tab)
    wait_for(lambda: js(f"Boolean({frame}.document.querySelector('.leaflet-popup-content .geogpt-feature-properties'))"))
    popup = js(f"{frame}.document.querySelector('.leaflet-popup-content').textContent.replace(/\\s+/g,' ').trim()")
    assert popup == hover and "Bahnhof Bischofsplatz" in popup and "FULLY_ACCESSIBLE" in popup
    selectable = js(f"""(() => {{
        const win={frame}, root=win.document.querySelector('.geogpt-feature-properties');
        const walk=win.document.createTreeWalker(root, win.NodeFilter.SHOW_TEXT);
        const needle='Bahnhof Bischofsplatz';
        let text;
        while (text=walk.nextNode()) {{
            const index=text.textContent.indexOf(needle);
            if (index >= 0) {{
                const range=win.document.createRange();
                range.setStart(text,index); range.setEnd(text,index+needle.length);
                const selection=win.getSelection(); selection.removeAllRanges(); selection.addRange(range);
                return {{text:selection.toString(), select:win.getComputedStyle(root).userSelect}};
            }}
        }}
    }})()""")
    assert selectable == {"text": "Bahnhof Bischofsplatz", "select": "text"}, selectable
    cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1200, "y": 300}, tab)
    time.sleep(.5)
    assert js(f"Boolean({frame}.document.querySelector('.leaflet-popup-content'))")
    assert state()["runs"] == initial["runs"], "Hover/click triggered a backend rerun"
    screenshot("matching-popup.png")

    send("将车站数据绘制为点图层。")
    created = state()["layer_summaries"][-1]
    assert state()["layers"] == 3 and created["layer_number"] == 3
    wait_for(lambda: sidebar_has(3, created["name"]))
    screenshot("new-layer-immediate.png")
    boundary = initial["layer_summaries"][1]
    click_key("del_map_" + boundary["layer_id"])
    wait_for(lambda: state()["layers"] == 2)
    assert state()["layer_summaries"][-1]["layer_id"] == created["layer_id"]
    assert state()["layer_summaries"][-1]["layer_number"] == 2
    wait_for(lambda: sidebar_has(2, created["name"]))
    click_key("toggle_map_" + created["layer_id"])
    wait_for(lambda: state()["layer_summaries"][-1]["visible"] is False)
    assert state()["layer_summaries"][-1]["layer_number"] == 2
    send("删除图层2。")
    assert state()["layers"] == 1 and state()["layer_summaries"][0]["layer_number"] == 1
    assert state()["layer_summaries"][0]["layer_id"] == initial["layer_summaries"][0]["layer_id"]
    screenshot("renumbered-layers.png")
    (output / "results.json").write_text(json.dumps({"sidebar_immediate": True, "popup_matches_hover": True,
        "popup_selectable": True, "numbers_compacted": True, "current_number_targets_correct_layer": True,
        "initial": initial, "final": state()}, ensure_ascii=False, indent=2), encoding="utf-8")
