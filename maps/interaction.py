"""Map identity depends on visual state, not conversation length."""
from dataclasses import asdict
from copy import deepcopy
import hashlib
import json
from branca.element import MacroElement
from jinja2 import Template


def map_signature(session):
    value = {"layers": [asdict(layer) for layer in session.layers.values()],
             "title": session.map_title, "basemap": session.show_basemap}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class MapDisplayCache:
    """One pristine figure per UI session; component rendering mutates copies."""
    def __init__(self):
        self._key = None
        self._figure = None

    def get(self, session, build):
        key = (session.session_id, map_signature(session))
        if key != self._key:
            with session.diagnostics.measure("map_build"):
                figure = build()
            self._key, self._figure = key, figure
        else:
            session.diagnostics.record("map_cache_hit")
        # st_folium renders and alters the supplied tree. Reusing that same
        # rendered object accumulates scripts; a pristine copy keeps IDs stable.
        return deepcopy(self._figure)


def remember_view(figure, session):
    """Restore the viewport if Streamlit remounts unchanged map geometry."""
    geometry = sorted({layer.dataset_id for layer in session.layers.values() if layer.visible})
    content_key = hashlib.sha256(json.dumps(geometry).encode()).hexdigest()
    control = MacroElement()
    control._name = "RememberViewport"
    control.storage_key = "geogpt-view-" + session.session_id
    control.content_key = content_key
    control._template_str = """
        {% macro script(this, kwargs) %}
        (function() {
            const map = {{this._parent.get_name()}};
            if (map.__geogptViewportInstalled) return;
            map.__geogptViewportInstalled = true;
            const key = {{this.storage_key|tojson}};
            const signature = {{this.content_key|tojson}};
            const diagnosticKey = key.replace("geogpt-view-", "geogpt-diagnostics-");
            function count(event) {
                try {
                    const counters = JSON.parse(sessionStorage.getItem(diagnosticKey)) || {};
                    counters[event] = (counters[event] || 0) + 1;
                    sessionStorage.setItem(diagnosticKey, JSON.stringify(counters));
                } catch (_) {}
            }
            count("map_mounts");
            map.whenReady(function() {
                try {
                    const saved = JSON.parse(sessionStorage.getItem(key));
                    if (saved && saved.signature === signature && Array.isArray(saved.center)
                        && saved.center.length === 2 && saved.center.every(Number.isFinite)
                        && Number.isFinite(saved.zoom)) {
                        map.setView(saved.center, saved.zoom, {animate: false});
                        count("view_restores");
                    }
                } catch (_) {}
                function saveView() {
                    try {
                        const center = map.getCenter();
                        sessionStorage.setItem(key, JSON.stringify({
                            signature: signature, center: [center.lat, center.lng], zoom: map.getZoom()
                        }));
                    } catch (_) {}
                }
                map.on("moveend", function() { count("move_events"); saveView(); });
                map.on("zoomend", function() { count("zoom_events"); saveView(); });
                saveView();
            });
        })();
        {% endmacro %}
    """
    control._template = Template(control._template_str)
    figure.add_child(control)
