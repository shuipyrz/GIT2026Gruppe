"""Real Chrome/CDP map interaction regression; uses no model credentials."""
import base64
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.phase4a_acceptance import CDP, wait_for


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stress", action="store_true", help="100 historical turns, 30 retained datasets and 4 visible layers")
    parser.add_argument("--stale-runtime", action="store_true", help="Start with a cached SessionData class missing ensure_compatible")
    parser.add_argument("--layer-ui", action="store_true", help="Check sidebar updates, copyable feature details, and renumbering")
    parser.add_argument("--sidebar-layout", action="store_true", help="Check layer buttons and long names at narrow sidebar widths")
    parser.add_argument("--workspace-layout", action="store_true", help="Check fixed uploads, scrollable sidebar lists and viewport sizing")
    args = parser.parse_args()
    output = ROOT / ".review-browser"
    if args.stress:
        output = output / "stress"
    if args.stale_runtime:
        output = output / "stale-runtime"
    if args.layer_ui:
        output = output / "layer-ui"
    if args.sidebar_layout:
        output = output / "sidebar-layout"
    if args.workspace_layout:
        output = output / "workspace-layout"
    output.mkdir(parents=True, exist_ok=True)
    (output / ".gitignore").write_text("*\n", encoding="utf-8")
    state_file = output / "state.json"
    if state_file.exists():
        state_file.unlink()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {**os.environ, "FEEDBACK_BROWSER_STATE": str(state_file), "PYTHONDONTWRITEBYTECODE": "1",
                   "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false", "LANGSMITH_TRACING": "false"}
    if args.stress:
        environment["FEEDBACK_STRESS"] = "1"
    if args.stale_runtime:
        environment["FEEDBACK_STALE_MODULES"] = "1"
    if args.layer_ui:
        environment["FEEDBACK_LAYER_UI"] = "1"
    if args.sidebar_layout:
        environment["FEEDBACK_SIDEBAR_LAYOUT"] = "1"
    if args.workspace_layout:
        environment["FEEDBACK_WORKSPACE_LAYOUT"] = "1"
    handles, processes, cdp = [], [], None
    def start(command, filename):
        handle = (output / filename).open("w", encoding="utf-8")
        handles.append(handle)
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=handle, stderr=handle,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        processes.append(process)
        return process
    def state():
        return json.loads(state_file.read_text(encoding="utf-8"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        server = start([sys.executable, "-B", "-m", "streamlit", "run", "tests/feedback_app.py",
            "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true",
            "--browser.gatherUsageStats=false", "--server.fileWatcherType=none"], "streamlit.log")
        def ready():
            assert server.poll() is None, "Streamlit exited"
            return opener.open(f"http://127.0.0.1:{port}/_stcore/health", timeout=1).read() == b"ok"
        wait_for(ready)
        profile = output / ("chrome-profile-" + uuid4().hex)
        profile.mkdir(exist_ok=True)
        active = profile / "DevToolsActivePort"
        if active.exists():
            active.unlink()
        chrome = start([str(Path(os.environ["PROGRAMFILES"]) / "Google/Chrome/Application/chrome.exe"),
            "--headless=new", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--user-data-dir=" + str(profile), "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-sync", "--disable-gpu", "--in-process-gpu",
            "about:blank"], "chrome.log")
        def browser_ready():
            assert chrome.poll() is None, "Chrome exited"
            return active.read_text().splitlines()[0] if active.exists() else None
        browser_port = wait_for(browser_ready)
        info = json.loads(opener.open(f"http://127.0.0.1:{browser_port}/json/version", timeout=2).read())
        cdp = CDP(info["webSocketDebuggerUrl"])
        target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        for method in ("Page.enable", "Runtime.enable", "Network.enable"):
            cdp.call(method, session=session)
        cdp.call("Network.setBlockedURLs", {"urls": ["*tile.openstreetmap.org*"]}, session)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1500, "height": 1100,
                 "deviceScaleFactor": 1, "mobile": False}, session)
        cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{port}"}, session)
        frame = "document.querySelector('iframe[title=\"streamlit_folium.st_folium\"]')?.contentWindow"
        wait_for(lambda: cdp.js(session, f"Boolean({frame}?.document?.querySelector('.leaflet-control-zoom-out'))"), timeout=45)
        frame = frame.replace("?.contentWindow", ".contentWindow")
        time.sleep(1.5)
        initial = state()
        if args.workspace_layout:
            from tests.workspace_layout_acceptance import check_workspace_layout
            check_workspace_layout(cdp, session, state, frame, output)
            print("PASS: uniform sidebar, fixed uploads, viewport workspace and internal scrolling", flush=True)
            return
        if args.sidebar_layout:
            from tests.sidebar_layout_acceptance import check_sidebar_layout
            check_sidebar_layout(cdp, session, state, frame, output)
            print("PASS: fixed layer buttons, truncated names, tooltips and visibility at narrow widths", flush=True)
            return
        if args.layer_ui:
            from tests.layer_ui_acceptance import check_layer_ui
            check_layer_ui(cdp, session, state, frame, output)
            print("PASS: immediate sidebar, matching selectable popup, and current layer numbering", flush=True)
            return
        if args.stale_runtime:
            assert initial["legacy_session_preserved"] is True
            assert initial["agent_runtime"] >= 19
        cdp.js(session, f"{frame}.__feedback_marker='before-delete'")
        before_path = cdp.js(session, f"{frame}.document.querySelector('svg path').getAttribute('d')")
        for _ in range(4):
            cdp.js(session, f"{frame}.document.querySelector('.leaflet-control-zoom-out').click()")
            time.sleep(.35)
        time.sleep(1.5)
        after_path = cdp.js(session, f"{frame}.document.querySelector('svg path').getAttribute('d')")
        assert before_path != after_path, "Zoom did not change rendered geometry"
        assert state()["runs"] == initial["runs"], (initial, state())
        assert cdp.js(session, f"{frame}.__feedback_marker") == "before-delete"
        def screenshot(name):
            data = cdp.call("Page.captureScreenshot", {"format": "png"}, session)["data"]
            (output / name).write_bytes(base64.b64decode(data))
        screenshot("choropleth-zoom.png")
        def saved_view():
            return cdp.js(session, f"""(() => {{
                const storage = {frame}.sessionStorage;
                const key = Object.keys(storage).find(key => key.startsWith('geogpt-view-'));
                return key ? JSON.parse(storage.getItem(key)) : null;
            }})()""")
        def client_counts():
            return cdp.js(session, f"""(() => {{
                const storage = {frame}.sessionStorage;
                const key = Object.keys(storage).find(key => key.startsWith('geogpt-diagnostics-'));
                return key ? JSON.parse(storage.getItem(key)) : null;
            }})()""")
        def drag_map():
            rectangle = cdp.js(session, f"""(() => {{
                const r = document.querySelector('iframe[title="streamlit_folium.st_folium"]').getBoundingClientRect();
                return {{x:r.x + r.width * .55, y:r.y + r.height * .45}};
            }})()""")
            x, y = rectangle["x"], rectangle["y"]
            cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, session)
            cdp.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                     "button": "left", "buttons": 1, "clickCount": 1}, session)
            for step in range(1, 7):
                cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x + step * 12, "y": y + step * 4,
                         "button": "left", "buttons": 1}, session)
                time.sleep(.06)
            cdp.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x + 72, "y": y + 24,
                     "button": "left", "buttons": 0, "clickCount": 1}, session)
            time.sleep(.8)
        before_drag = saved_view()
        before_drag_client = client_counts()
        drag_map()
        assert saved_view()["center"] != before_drag["center"], "Drag did not pan the map"
        assert state()["runs"] == initial["runs"], "Drag reran Streamlit"
        assert client_counts()["map_mounts"] == before_drag_client["map_mounts"], "Drag remounted the map"
        zoomed_view = saved_view()
        assert zoomed_view, "Viewport persistence was not installed"
        cdp.js(session, """(() => {
            const input = document.querySelector('textarea[data-testid="stChatInputTextArea"]');
            input.focus();
        })()""")
        cdp.call("Input.insertText", {"text": "你好"}, session)
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter",
                 "windowsVirtualKeyCode": 13}, session)
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
                 "windowsVirtualKeyCode": 13}, session)
        wait_for(lambda: cdp.js(session, "document.body.innerText.includes('你好，地图保持当前状态。')"))
        time.sleep(1)
        assert state()["map_key"] == initial["map_key"]
        assert saved_view()["zoom"] == zoomed_view["zoom"], (zoomed_view, saved_view())
        assert all(abs(a-b) < 1e-7 for a,b in zip(saved_view()["center"], zoomed_view["center"]))
        assert state()["runtime"]["counts"]["map_build"] == 1, "Chat rebuilt the map"
        assert cdp.js(session, f"{frame}.__feedback_marker") == "before-delete", "Chat remounted unchanged geometry"
        if args.stress:
            for turn in range(10):
                before_chat = state()
                previous_view = saved_view()
                cdp.js(session, "document.querySelector('textarea[data-testid=\"stChatInputTextArea\"]').focus()")
                cdp.call("Input.insertText", {"text": f"请再解释一下，测试第{turn}轮"}, session)
                for event in ("keyDown", "keyUp"):
                    cdp.call("Input.dispatchKeyEvent", {"type": event, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, session)
                wait_for(lambda: state()["chat_messages"] == before_chat["chat_messages"] + 2)
                time.sleep(.3)
                assert state()["runtime"]["counts"]["map_build"] == 1
                assert cdp.js(session, f"{frame}.__feedback_marker") == "before-delete"
                assert saved_view()["zoom"] == previous_view["zoom"]
                before_pan = state()
                drag_map()
                assert state()["runs"] == before_pan["runs"]
                assert state()["runtime"]["counts"].get("model_call", 0) == before_pan["runtime"]["counts"].get("model_call", 0)
            assert cdp.js(session, "document.querySelectorAll('[data-testid=stChatMessage]').length") == 50
        downloads = output / "downloads"
        downloads.mkdir(exist_ok=True)
        for old_download in downloads.glob("geogpt-diagnostics*.json"):
            old_download.unlink()
        cdp.call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(downloads), "eventsEnabled": True})
        before_diagnosis = state()
        before_diagnostic_client = client_counts()
        cdp.js(session, "Array.from(document.querySelectorAll('summary')).find(e=>e.textContent.includes('Laufzeitdiagnose')).click()")
        cdp.js(session, "Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('Diagnose herunterladen')).click()")
        diagnostic_file = wait_for(lambda: next(downloads.glob("geogpt-diagnostics*.json"), None))
        downloaded_diagnosis = json.loads(diagnostic_file.read_text(encoding="utf-8"))
        time.sleep(.5)
        assert state()["runs"] == before_diagnosis["runs"], "Diagnostic download reran the app"
        assert client_counts()["map_mounts"] == before_diagnostic_client["map_mounts"]
        assert downloaded_diagnosis["runtime"]["counts"]["page_started"] == state()["runs"]
        assert downloaded_diagnosis["session"]["datasets"] == state()["stats"]["datasets"]
        before_delete = state()
        while state()["layers"]:
            count = state()["layers"]
            first_layer_id = state()["layer_summaries"][0]["layer_id"]
            cdp.js(session, f"document.querySelector('.st-key-del_map_{first_layer_id} button').click()")
            wait_for(lambda: state()["layers"] == count - 1)
            time.sleep(.5)
        time.sleep(1.5)
        after_delete = state()
        cdp.js(session, f"{frame}.__feedback_marker='after-delete'")
        for _ in range(3):
            cdp.js(session, f"{frame}.document.querySelector('.leaflet-control-zoom-out').click()")
            time.sleep(.35)
        time.sleep(1.2)
        assert state()["runs"] == after_delete["runs"], (after_delete, state())
        assert cdp.js(session, f"{frame}.__feedback_marker") == "after-delete"
        drag_map()
        assert state()["runs"] == after_delete["runs"], "Drag after deletion reran Streamlit"
        screenshot("after-delete-zoom.png")
        # Open a real downloaded file:// HTML export. No spoofed Referer and
        # no requests to the OSM community tile server are used.
        from agent.graph import create_agent_session
        from core.contracts import Layer
        from core.session import SessionData
        from shapely.geometry import box
        from tests.conftest import collection
        exported_session = SessionData()
        dataset_id = exported_session.add_upload("Dresden test", collection(box(13.70, 51.03, 13.78, 51.07)))
        exported_session.add_layer(Layer(dataset_id, "Export test"))
        exporter = create_agent_session(exported_session, model_factory=lambda: None)
        assert "Erfolg" in exporter.export_current_map.invoke({"format": "html"})
        export_file = output / "downloaded-map.html"
        export_file.write_bytes(exported_session.export_artifact["data"])
        export_target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        export_tab = cdp.call("Target.attachToTarget", {"targetId": export_target, "flatten": True})["sessionId"]
        for method in ("Page.enable", "Runtime.enable", "Network.enable"):
            cdp.call(method, session=export_tab)
        cdp.call("Page.navigate", {"url": export_file.as_uri()}, export_tab)
        wait_for(lambda: cdp.js(export_tab, """(() => {
            const tiles = [...document.querySelectorAll('.leaflet-tile')];
            return tiles.length > 0 && tiles.every(tile => tile.complete && tile.naturalWidth > 0);
        })()"""), timeout=45)
        tile_responses = [event["params"]["response"]["status"] for event in cdp.events
                          if event.get("sessionId") == export_tab and event.get("method") == "Network.responseReceived"
                          and "wmts_topplus_open" in event["params"]["response"]["url"]]
        assert tile_responses and all(code == 200 for code in tile_responses), tile_responses
        data = cdp.call("Page.captureScreenshot", {"format": "png"}, export_tab)["data"]
        (output / "downloaded-map.png").write_bytes(base64.b64decode(data))
        (output / "results.json").write_text(json.dumps({"zoom_no_rerun": True, "chat_keeps_view": True,
            "pan_no_rerun": True, "chat_no_map_rebuild_or_remount": True, "stress": args.stress,
            "diagnostic_download_no_rerun": True,
            "stale_runtime_recovered": args.stale_runtime and initial["legacy_session_preserved"],
            "delete_then_zoom_no_rerun": True, "initial": initial, "before_delete": before_delete,
            "final": state(), "client": client_counts(),
            "file_export_basemap_statuses": tile_responses}, indent=2), encoding="utf-8")
        print("PASS: real Chrome zoom, chat viewport, zoom after deletion, and file:// export basemap", flush=True)
    except Exception:
        if cdp is not None:
            (output / "browser-errors.json").write_text(json.dumps([
                event for event in cdp.events if event.get("method") in ("Runtime.exceptionThrown", "Network.loadingFailed")
            ], ensure_ascii=False, indent=2), encoding="utf-8")
            data = cdp.call("Page.captureScreenshot", {"format": "png"}, session)["data"]
            (output / "failure.png").write_bytes(base64.b64decode(data))
        raise
    finally:
        if cdp is not None:
            cdp.ws.close()
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
