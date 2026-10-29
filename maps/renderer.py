"""Explicit layer-to-Folium rendering, independent of application state.

Preserves the existing map types, styles, layer order and last-layer bounds.
GeoJSON is copied at the Folium boundary to avoid its identifier injection
mutating caller data. Helpers intentionally add children to the supplied map.
"""
from copy import deepcopy
from html import escape
import json
import math
from typing import List, Optional

import folium
import branca.colormap as cm
from folium.plugins import HeatMap
from branca.element import Element
from pyproj import CRS


def _check_wire_crs(layer):
    """Require WGS84 wire data; callers prepare copies outside the renderer."""
    declarations = []
    if "crs" in layer:
        declarations.append(layer["crs"])
    data = layer.get("data")
    if isinstance(data, dict) and "crs" in data:
        header = data["crs"]
        if not isinstance(header, dict) or header.get("type") != "name":
            raise ValueError("Karten-CRS unbekannt; WGS84-Eingabe vorbereiten.")
        properties = header.get("properties")
        declarations.append(properties.get("name") if isinstance(properties, dict) else None)
    for value in declarations:
        try:
            valid = value is not None and CRS.from_user_input(value).equals(CRS.from_epsg(4326), ignore_axis_order=True)
        except Exception:
            valid = False
        if not valid:
            raise ValueError("Karten benötigen eine tatsächlich nach EPSG:4326 transformierte Kopie.")

DEFAULT_MAP_STYLE = {
    "color": "blue",
    "weight": 2,
    "fillColor": "blue",
    "fillOpacity": 0.4
}

_TOOLTIP_FIELD_PRIORITY = (
    "name", "einrichtung", "title", "bezeichnung", "category", "kategorie", "type",
    "einrichtungsart", "amenity", "address", "adresse", "street", "strasse", "hnr",
    "housenumber", "plz", "postcode", "postalcode", "city", "ort", "gemeinde",
    "description", "beschreibung", "bemerkung", "barrierefreiheit", "accessibility",
    "zugang_oepnv", "operator", "population", "value",
)
_TOOLTIP_FIELD_LIMIT = 8


def _is_technical_tooltip_field(field):
    """Exclude identifiers, links and machine-maintained metadata from map tooltips."""
    normalized = "".join(character for character in str(field).casefold() if character.isalnum())
    if any(token in normalized for token in ("url", "uri", "href", "link", "uuid", "guid", "timestamp")):
        return True
    if normalized in {"id", "fid", "crs", "bbox", "geometry", "metadata", "tsp"}:
        return True
    if normalized.endswith("id") or normalized.startswith("id"):
        return True
    if any(token in normalized for token in ("created", "modified", "updated", "lastchange", "timestamp")):
        return True
    if normalized.endswith(("date", "datetime", "time", "at")) or normalized.startswith("ds"):
        return True
    return False


def _select_tooltip_fields(properties, *, preferred=()):
    """Pick a compact set of meaningful, populated source attributes."""
    if not isinstance(properties, dict):
        return []
    available = {
        str(field): value for field, value in properties.items()
        if not _is_technical_tooltip_field(field)
        and value is not None
        and not (isinstance(value, str) and value.strip().casefold() in {"", "null", "none", "nan"})
        and not (isinstance(value, (dict, list, tuple)) and not value)
    }
    if not available:
        return []
    by_normalized = {"".join(char for char in field.casefold() if char.isalnum()): field
                     for field in available}
    selected = []
    preferred_fields = [*preferred, *_TOOLTIP_FIELD_PRIORITY]
    for candidate in preferred_fields:
        field = by_normalized.get("".join(char for char in str(candidate).casefold() if char.isalnum()))
        if field and field not in selected:
            selected.append(field)
        if len(selected) >= _TOOLTIP_FIELD_LIMIT:
            return selected
    for field in available:
        if field not in selected:
            selected.append(field)
        if len(selected) >= _TOOLTIP_FIELD_LIMIT:
            break
    return selected


def _tooltip_value(value):
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return escape(rendered)


def _tooltip_alias(field):
    normalized = "".join(character for character in field.casefold() if character.isalnum())
    return "Name:" if normalized in {"name", "title", "bezeichnung", "einrichtung"} else f"{field}:"


def create_layered_map(layers: List[dict], view_updates: Optional[dict] = None, show_basemap: bool = True) -> folium.Map:
    m = folium.Map(location=[51.1657, 10.4515], zoom_start=6, tiles=None)
    m.get_root().header.add_child(Element("""
        <style>
        .leaflet-control-container .leaflet-top.leaflet-right {
            top: auto !important;
            right: auto !important;
            bottom: 0 !important;
            left: 0 !important;
        }
        </style>
    """))
    if show_basemap:
        folium.TileLayer(tiles="OpenStreetMap", name="OpenStreetMap", control=False).add_to(m)

    if not layers:
        return m

    latest_bounds = None
    for layer in layers:
        _check_wire_crs(layer)
        l_type = layer.get("type", "geojson")
        l_data = layer.get("data")
        l_name = layer.get("name", "Layer")
        kwargs = layer.get("folium_kwargs", DEFAULT_MAP_STYLE.copy())

        if l_type == "points":
            bounds = add_points_layer(m, l_data, l_name, folium_kwargs=kwargs,
                                      data_field=layer.get("data_field"))
            if bounds: latest_bounds = bounds
        elif l_type == "line":
            bounds = add_line_layer(m, l_data, l_name, data_field=layer.get("data_field"),
                                    folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds
        elif l_type == "heatmap":
            bounds = add_heatmap_layer(m, l_data, l_name, layer.get("data_field"), folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds
        elif l_type == "choropleth":
            bounds = add_choropleth_layer(m, l_data, l_name, layer.get("data_field"), folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds
        else:
            bounds = add_geojson_layer(m, l_data, l_name, folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds

    if getattr(m, "_has_proportional_symbols", False):
        map_name = m.get_name()
        m.get_root().script.add_child(Element(f"""
        (function() {{
            const map = {map_name};
            const symbols = [];
            function collect(layer) {{
                if ((layer instanceof L.CircleMarker && layer.options.className === "codex-proportional-symbol") ||
                    (layer instanceof L.Path && layer.options.className === "codex-proportional-line")) {{
                    if (!symbols.some(entry => entry.layer === layer)) {{
                        symbols.push({{layer: layer,
                            kind: layer instanceof L.CircleMarker ? "circle" : "line",
                            size: layer instanceof L.CircleMarker ? layer.getRadius() : layer.options.weight}});
                    }}
                }} else if (layer.eachLayer) {{
                    layer.eachLayer(collect);
                }}
            }}
            function collectAndResize() {{
                map.eachLayer(collect);
                const scale = Math.max(0.15, Math.min(1.5, Math.pow(1.6, map.getZoom() - 12)));
                symbols.forEach(entry => {{
                    const size = entry.size * scale;
                    if (entry.kind === "circle") {{
                        entry.layer.setRadius(Math.max(2, Math.min(36, size)));
                    }} else {{
                        entry.layer.setStyle({{weight: Math.max(1, Math.min(15, size))}});
                    }}
                }});
            }}
            map.whenReady(function() {{
                collectAndResize();
                map.on("zoomend", collectAndResize);
            }});
        }})();
        """))

    if view_updates and "min_lat" in view_updates:
        m.fit_bounds([[view_updates["min_lat"], view_updates["min_lon"]],
                      [view_updates["max_lat"], view_updates["max_lon"]]])
    elif latest_bounds:
        m.fit_bounds(latest_bounds)
    else:
        m.fit_bounds([[50.5, 9.5], [52.5, 11.5]])

    return m

def add_points_layer(m, points, layer_name, folium_kwargs=None, data_field=None):
    if folium_kwargs is None: folium_kwargs = {"color": "blue", "icon": "info-sign"}
    fg = folium.FeatureGroup(name=layer_name)
    bounds = []

    # Unterstützt GeoJSON-Features und Listenformate.
    pts_to_iterate = points.get("features", []) if isinstance(points, dict) else points
    point_values = []
    for point in pts_to_iterate:
        properties = point.get("properties") or {} if isinstance(point, dict) and "geometry" in point else (
            {**(point.get("properties") or {}), **{key: value for key, value in point.items()
             if key not in {"lat", "lon", "properties"}}} if isinstance(point, dict) else {})
        try:
            value = float(properties[data_field]) if data_field and properties.get(data_field) is not None else None
            point_values.append(value if value is not None and math.isfinite(value) else None)
        except (TypeError, ValueError, OverflowError):
            point_values.append(None)
    valid_values = [value for value in point_values if value is not None]
    min_value = min(valid_values) if valid_values else 0.0
    max_value = max(valid_values) if valid_values else 0.0

    for index, pt in enumerate(pts_to_iterate):
        if isinstance(pt, dict) and "geometry" in pt:
            coords = pt["geometry"].get("coordinates", [0, 0])
            lat, lon = coords[1], coords[0]
            properties = pt.get("properties") or {}
            name = properties.get("name", "Punkt")
        else:
            lat, lon = pt.get("lat", 0), pt.get("lon", 0)
            name = pt.get("name", "Punkt")
            properties = dict(pt.get("properties") or {})
            properties.update({key: value for key, value in pt.items()
                               if key not in {"lat", "lon", "properties"}})
        bounds.append([lat, lon])
        tooltip = None
        tooltip_fields = _select_tooltip_fields(properties, preferred=(data_field,) if data_field else ())
        if tooltip_fields:
            rows = "<br>".join(
                f"<strong>{escape(str(key))}</strong>: "
                f"{_tooltip_value(properties[key])}"
                for key in tooltip_fields
            )
            tooltip = folium.Tooltip(rows, sticky=True, direction="top")
        value = point_values[index]
        if data_field and max_value > min_value:
            normalized = ((value - min_value) / (max_value - min_value)) if value is not None else 0.0
            radius = 5 + 13 * math.sqrt(max(0.0, min(1.0, normalized)))
            marker = folium.CircleMarker(
                location=[lat, lon], radius=radius, color=folium_kwargs.get("color", "blue"),
                fill=True, fill_color=folium_kwargs.get("fillColor", folium_kwargs.get("color", "blue")),
                fill_opacity=folium_kwargs.get("fillOpacity", 1.0), weight=folium_kwargs.get("weight", 1),
                popup=str(name), tooltip=tooltip
            )
            marker.options["className"] = "codex-proportional-symbol"
            marker.add_to(fg)
            m._has_proportional_symbols = True
        else:
            folium.Marker(
                location=[lat, lon],
                popup=str(name),
                icon=folium.Icon(color=folium_kwargs.get("color", "blue"), icon=folium_kwargs.get("icon", "info-sign")),
                tooltip=tooltip
            ).add_to(fg)
    fg.add_to(m)
    return bounds


def add_line_layer(m, geojson_data, layer_name, data_field=None, folium_kwargs=None):
    """Render line features with optional value-scaled stroke width and filtered tooltips."""
    if folium_kwargs is None:
        folium_kwargs = {"color": "blue", "weight": 3, "fillOpacity": 1.0}
    if isinstance(geojson_data, dict):
        if geojson_data.get("type") == "Feature":
            features = [geojson_data]
        else:
            features = geojson_data.get("features", [])
    elif isinstance(geojson_data, list):
        features = geojson_data
    else:
        features = []
    if not isinstance(features, list):
        features = []

    values = []
    for feature in features:
        properties = feature.get("properties") or {} if isinstance(feature, dict) else {}
        try:
            value = float(properties[data_field]) if data_field and properties.get(data_field) is not None else None
            values.append(value if value is not None and math.isfinite(value) else None)
        except (TypeError, ValueError, OverflowError):
            values.append(None)
    valid_values = [value for value in values if value is not None]
    min_value = min(valid_values) if valid_values else 0.0
    max_value = max(valid_values) if valid_values else 0.0
    has_scale = bool(data_field and max_value > min_value)

    tooltip_fields = list(dict.fromkeys(
        field
        for feature in features if isinstance(feature, dict)
        for field in _select_tooltip_fields(feature.get("properties") or {},
                                            preferred=(data_field,) if data_field else ())
    ))[:_TOOLTIP_FIELD_LIMIT]

    def style_function(feature):
        properties = feature.get("properties") or {}
        weight = folium_kwargs.get("weight", 3)
        if has_scale:
            try:
                value = float(properties[data_field])
                normalized = (value - min_value) / (max_value - min_value)
                weight = 2 + 8 * math.sqrt(max(0.0, min(1.0, normalized)))
            except (KeyError, TypeError, ValueError, OverflowError):
                weight = 2
        style = {
            "color": folium_kwargs.get("color", "blue"),
            "weight": weight,
            "fillOpacity": folium_kwargs.get("fillOpacity", 1.0),
        }
        if has_scale:
            style["className"] = "codex-proportional-line"
        return style

    fg = folium.FeatureGroup(name=layer_name)
    geo_layer = folium.GeoJson(deepcopy(geojson_data), name=layer_name,
                               style_function=style_function).add_to(fg)
    if tooltip_fields:
        geo_layer.add_child(folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=[_tooltip_alias(field) for field in tooltip_fields],
            localize=True,
            sticky=True,
        ))
    fg.add_to(m)
    if has_scale:
        m._has_proportional_symbols = True
    try:
        return geo_layer.get_bounds()
    except Exception:
        return []

def add_heatmap_layer(m, points, layer_name, data_field=None, folium_kwargs=None):
    if folium_kwargs is None: folium_kwargs = {"radius": 25, "blur": 15, "min_opacity": 0.3}
    heat_data = []
    pts_to_iterate = points.get("features", []) if isinstance(points, dict) else points
    for pt in pts_to_iterate:
        if isinstance(pt, dict) and "geometry" in pt:
            coords = pt["geometry"].get("coordinates", [0, 0])
            val = pt.get("properties", {}).get(data_field, 1.0) if data_field else 1.0
            heat_data.append([coords[1], coords[0], float(val)])
        else:
            val = pt.get("properties", {}).get(data_field, 1.0) if data_field else 1.0
            heat_data.append([pt["lat"], pt["lon"], float(val)])

    if heat_data:
        HeatMap(heat_data, name=layer_name, **folium_kwargs).add_to(m)
    return [[r[0], r[1]] for r in heat_data] if heat_data else []

def add_choropleth_layer(m, geojson_data, layer_name, data_field=None, folium_kwargs=None):
    if folium_kwargs is None:
        folium_kwargs = {"fillColor": "YlOrRd", "color": "black", "weight": 0.5, "fillOpacity": 0.7}

    COLORMAPS = {
        "YlOrRd": cm.linear.YlOrRd_09,
        "Blues": cm.linear.Blues_09,
        "Greens": cm.linear.Greens_09,
        "Purples": cm.linear.Purples_09,
        "Reds": cm.linear.Reds_09,
        "Oranges": cm.linear.Oranges_09,
        "YlGn": cm.linear.YlGn_09,
        "YlGnBu": cm.linear.YlGnBu_09,
        "GnBu": cm.linear.GnBu_09,
        "PuBu": cm.linear.PuBu_09,
        "BuPu": cm.linear.BuPu_09,
        "RdYlBu": cm.linear.RdYlBu_11,
    }
    # GeoJSON uploads may be either a FeatureCollection or one Feature. Keep
    # both forms intact for Folium, but inspect their properties consistently
    # when deriving the color scale.
    if isinstance(geojson_data, dict):
        if geojson_data.get("type") == "Feature":
            features = [geojson_data]
        elif geojson_data.get("type") == "FeatureCollection":
            features = geojson_data.get("features", [])
        else:
            features = []
    elif isinstance(geojson_data, list):
        features = geojson_data
    else:
        features = []
    if not isinstance(features, list):
        features = []

    property_fields = list(dict.fromkeys(
        key
        for feat in features if isinstance(feat, dict)
        for key in (feat.get("properties") or {})
    ))
    if data_field not in property_fields:
        matches = [field for field in property_fields
                   if data_field and field.casefold() == data_field.casefold()]
        if len(matches) == 1:
            data_field = matches[0]
        else:
            available = ", ".join(property_fields) if property_fields else "Keine"
            raise ValueError(f"Choropleth-Attribut '{data_field}' nicht gefunden. Verfügbare Attribute: {available}.")

    values = []
    for feat in features:
        properties = feat.get("properties") or {}
        val = properties.get(data_field)
        if val is not None:
            try:
                numeric_value = float(val)
                if math.isfinite(numeric_value):
                    values.append(numeric_value)
            except: pass
    if not values:
        raise ValueError(f"Choropleth-Attribut '{data_field}' enthält keine gültigen numerischen Werte.")
    if len(values) > 1 and min(values) == max(values):
        raise ValueError(f"Choropleth-Attribut '{data_field}' enthält überall denselben Wert; keine Farbskala möglich.")
    selected_fields = list(dict.fromkeys(
        field
        for feature in features if isinstance(feature, dict)
        for field in _select_tooltip_fields(feature.get("properties") or {}, preferred=(data_field,))
    ))[:_TOOLTIP_FIELD_LIMIT]
    tooltip_aliases = [_tooltip_alias(field) for field in selected_fields]

    min_val, max_val = min(values), max(values)
    if min_val == max_val: max_val += 1

    scale_gen = COLORMAPS.get(folium_kwargs.get("fillColor", "YlOrRd"), cm.linear.YlOrRd_09)
    colormap = scale_gen.scale(min_val, max_val)
    colormap.caption = f"{layer_name} - {data_field}"

    def style_fn(feature):
        val = feature.get("properties", {}).get(data_field)
        try: fill_col = colormap(float(val))
        except: fill_col = "#cccccc"
        return {
            "fillColor": fill_col,
            "color": folium_kwargs.get("color", "black"),
            "weight": folium_kwargs.get("weight", 0.5),
            "fillOpacity": folium_kwargs.get("fillOpacity", 0.7)
        }

    fg = folium.FeatureGroup(name=layer_name)
    geo_l = folium.GeoJson(deepcopy(geojson_data), name=layer_name, style_function=style_fn).add_to(fg)
    if selected_fields:
        geo_l.add_child(folium.GeoJsonTooltip(
            fields=selected_fields,
            aliases=tooltip_aliases,
            localize=True,
            sticky=True,
        ))
    fg.add_to(m)
    colormap.add_to(m)
    try: return geo_l.get_bounds()
    except: return []

def add_geojson_layer(m, geojson_data, layer_name, folium_kwargs=None):
    if folium_kwargs is None:
        folium_kwargs = {"fillColor": "blue", "color": "blue", "weight": 2, "fillOpacity": 0.4}

    if isinstance(geojson_data, dict):
        if geojson_data.get("type") == "Feature":
            features = [geojson_data]
        elif geojson_data.get("type") == "FeatureCollection":
            features = geojson_data.get("features", [])
        else:
            features = []
    elif isinstance(geojson_data, list):
        features = geojson_data
    else:
        features = []
    property_fields = list(dict.fromkeys(
        field
        for feature in features if isinstance(feature, dict)
        for field in _select_tooltip_fields(feature.get("properties") or {})
    ))[:_TOOLTIP_FIELD_LIMIT]

    fg = folium.FeatureGroup(name=layer_name)
    geo_l = folium.GeoJson(
        deepcopy(geojson_data),
        name=layer_name,
        style_function=lambda feat: {
            "fillColor": folium_kwargs.get("fillColor", "blue"),
            "color": folium_kwargs.get("color", "blue"),
            "weight": folium_kwargs.get("weight", 2),
            "fillOpacity": folium_kwargs.get("fillOpacity", 0.4)
        }
    ).add_to(fg)
    if property_fields:
        geo_l.add_child(folium.GeoJsonTooltip(
            fields=property_fields,
            aliases=[_tooltip_alias(field) for field in property_fields],
            localize=True,
            sticky=True,
        ))
    fg.add_to(m)
    try: return geo_l.get_bounds()
    except: return []

def Titel_hinzufügen(m: folium.Map, titel: str) -> None:
    safe_title = escape(str(titel))
    title_html = f'''
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 9999;
                background-color: white; padding: 8px 15px; border-radius: 6px; font-size: 18px;
                font-weight: bold; box-shadow: 0 0 6px rgba(0,0,0,0.3);">
        {safe_title}
    </div>'''
    m.get_root().html.add_child(Element(title_html))


def render_map_html(m: folium.Map) -> bytes:
    """Render UTF-8 HTML bytes, equivalent to Folium save's default encoding.

    Does not write a shared file or download browser assets. As with save,
    rendering may populate Folium's generated map/figure children.
    """
    return m.get_root().render().encode("utf-8")
