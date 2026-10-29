"""Browser-based PNG rendering for Folium map documents."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread


def render_map_png(map_html, *, show_basemap=True, width=1600, height=1000):
    """Render HTML text or UTF-8 bytes for one Folium map viewport to PNG."""
    if isinstance(map_html, bytes):
        map_html = map_html.decode("utf-8")
    elif not isinstance(map_html, str):
        raise TypeError("Map HTML must be text or UTF-8 bytes.")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "PNG export requires Playwright. Install the project requirements and run "
            "'python -m playwright install chromium'."
        ) from exc

    html = map_html.replace(
        "</head>",
        f"<style>html,body{{width:{width}px;height:{height}px;margin:0;overflow:hidden}} "
        f".folium-map{{width:{width}px!important;height:{height}px!important}}</style></head>",
        1,
    )
    body = html.encode("utf-8")

    class MapPageHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), MapPageHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
            except Exception:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Exception as exc:
                    raise RuntimeError(
                        "No headless browser is available. Install Chromium with "
                        "'python -m playwright install chromium'."
                    ) from exc
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{server.server_port}/map.html", wait_until="domcontentloaded",
                      timeout=45000)
            map_element = page.locator(".folium-map")
            map_element.wait_for(state="visible", timeout=15000)
            page.wait_for_timeout(1200)
            if show_basemap:
                page.wait_for_function(
                    """() => {
                        const tiles = Array.from(document.querySelectorAll('.leaflet-tile'));
                        return tiles.length > 0 && tiles.every(tile => tile.complete && tile.naturalWidth > 0);
                    }""",
                    timeout=25000,
                )
            page.wait_for_timeout(250)
            return page.screenshot(type="png", timeout=30000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "The map did not finish rendering. Check access to the Folium assets and, when enabled, "
            "the OpenStreetMap tile service."
        ) from exc
    finally:
        server.shutdown()
        server.server_close()


def render_map_pdf(map_png):
    """Place a rendered map image on a white, landscape A4 PDF page."""
    from PIL import Image

    with Image.open(BytesIO(map_png)) as source:
        image = source.convert("RGB")

    page_size = (1754, 1240)
    margin = 48
    image.thumbnail((page_size[0] - 2 * margin, page_size[1] - 2 * margin), Image.Resampling.LANCZOS)
    page = Image.new("RGB", page_size, "white")
    page.paste(image, ((page_size[0] - image.width) // 2, (page_size[1] - image.height) // 2))
    output = BytesIO()
    page.save(output, format="PDF", resolution=150.0)
    return output.getvalue()
