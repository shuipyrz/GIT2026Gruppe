"""Session-bound tool adapters; no current-user module global or context variable."""
import json
import io
import re
import urllib.parse
import zipfile
from functools import wraps
from inspect import signature
from typing import List, Optional

import requests
import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from langchain_core.tools import tool, StructuredTool

from core.session import AmbiguousReference, SessionData
from .attributes import is_non_measure_attribute
from gis.data_io import extract_features_to_gdf, transform_geodata
from gis import data_io as gis_data_io
from gis.geometry import buffer_geometries, pairwise_distances
from gis import statistics as gis_statistics
from maps.renderer import create_layered_map, render_map_html, Titel_hinzufügen
from maps.png_export import render_map_pdf, render_map_png

def make_unique_layer_name(base_name: str, existing_layers: List[dict]) -> str:
    existing_names = [l["name"] for l in existing_layers]
    if base_name not in existing_names:
        return base_name
    counter = 2
    new_name = f"{base_name} ({counter})"
    while new_name in existing_names:
        counter += 1
        new_name = f"{base_name} ({counter})"
    return new_name

def clean_gdf_for_geojson(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Compatibility entry point retaining the original in-place behavior."""
    return gis_data_io.clean_gdf_for_geojson(gdf, inplace=True)


def create_tools(session: SessionData):
    """Bind session-aware schemas to one session, publishing only after success.

    The working view exists only during a locked tool call and belongs to this
    factory's session. It is never shared with another session or module global.
    """
    working = None
    used_dataset_ids = set()
    tool_metrics = {}
    pending_map_action = {}
    pending_export = {}

    def get_agent_layers():
        if working is None:
            raise RuntimeError("Tool execution requires its bound session")
        return working

    def resolve(pool, reference, *, require_layer=False):
        match = session.resolve_in(pool, reference, require_layer=require_layer)
        if match:
            used_dataset_ids.add(match["dataset_id"])
        return match

    def input_frame(target):
        return extract_features_to_gdf(target["data"], source_crs=target["crs"])

    def meaningful_numeric_fields(frame):
        fields = []
        for field in frame.columns:
            if field == frame.geometry.name:
                continue
            if is_non_measure_attribute(field):
                continue
            normalized = "".join(character for character in str(field).casefold() if character.isalnum())
            if any(token in normalized for token in ("url", "uri", "timestamp")):
                continue
            source = frame[field]
            if pd.api.types.is_bool_dtype(source.dtype):
                continue
            try:
                values = pd.to_numeric(source, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
            except (TypeError, ValueError):
                continue
            values = values[np.isfinite(values)]
            if len(values) >= 2 and np.unique(values).size > 1:
                fields.append(field)
        return fields

    @tool
    def fetch_city_boundary_osm(city_name: str) -> str:
        """
        Ruft die offiziellen realen administrativen Grenzpolygone einer Stadt über OpenStreetMap (Nominatim) ab
        und fügt sie als neuen Layer hinzu. Geeignet bei Befehlen wie 'Zeige Dresden', 'Lade Umrisse von Berlin'.
        """
        raw_layers, show_layers = get_agent_layers()
        encoded_city = urllib.parse.quote(city_name.strip())
        osm_url = f"https://nominatim.openstreetmap.org/search?q={encoded_city}&format=geojson&polygon_geojson=1&limit=1"
        headers = {"User-Agent": "GeoGPT_Autonomous_GIS_Agent"}

        try:
            resp = requests.get(osm_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return f"Fehler bei OSM-Abfrage: HTTP {resp.status_code}"

            osm_data = resp.json()
            features = osm_data.get("features", [])
            if not features:
                return f"Keine Geometrieumrisse für '{city_name}' gefunden."

            target_feat = next((f for f in features if f.get("geometry", {}).get("type") in ["Polygon", "MultiPolygon"]), features[0])
            geojson_data = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"name": city_name, "source": "OpenStreetMap"},
                    "geometry": target_feat["geometry"]
                }]
            }

            layer_name = make_unique_layer_name(f"Stadtgrenze_{city_name}", show_layers)
            new_layer = {
                "name": layer_name,
                "type": "geojson",
                "data": geojson_data,
                "folium_kwargs": {"color": "red", "weight": 2, "fillColor": "blue", "fillOpacity": 0.2}
            }
            show_layers.append(new_layer)
            return f"Erfolg: Die Stadtgrenze von '{city_name}' wurde geladen und als Layer '{layer_name}' zur Karte hinzugefügt."
        except Exception as e:
            return f"Fehler beim Laden der Stadtgrenze: {str(e)}"

    @tool
    def visualize_layer_on_map(layer_name: str, map_type: str = "geojson", data_field: Optional[str] = None,
                               color: str = "blue", weight: int = 2, fill_opacity: float = 0.4) -> str:
        """
        Bringt einen bereits hochgeladenen Rohdaten-Layer auf die Karte oder ändert dessen Darstellungsart.
        map_type unterstützt: 'points', 'line', 'geojson', 'heatmap', 'choropleth'.
        data_field: Numerisches Attribut für Choropleth, Heatmap, proportionale Punktsymbole oder proportionale Linienstärke.
        Für Choropleth niemals ein Feld aus dem Layernamen ableiten. Für 'points' und 'line' ohne geeignetes
        numerisches Attribut normale Symbole verwenden; bei genau einem geeigneten Feld automatisch wählen.
        Gibt es mehrere geeignete Felder, wähle das inhaltlich passendste und nenne weitere Optionen.
        """
        raw_layers, show_layers = get_agent_layers()
        all_available = show_layers + raw_layers
        target = resolve(all_available, layer_name)
        if not target:
            available_names = [l["name"] for l in all_available]
            return f"Layer '{layer_name}' nicht gefunden. Verfügbar sind: {available_names}"

        frame = input_frame(target)  # Unknown/conflicting CRS must not become a successful layer.

        proportional_field = None
        proportional_alternatives = []
        if map_type in {"points", "line"}:
            available_point_fields = meaningful_numeric_fields(frame)
            if data_field:
                matches = [field for field in available_point_fields
                           if field.casefold() == data_field.casefold()]
                if not matches:
                    choices = ", ".join(available_point_fields) if available_point_fields else "Keine"
                    return (f"Attribut '{data_field}' ist kein geeignetes numerisches Feld für diese Darstellung. "
                            f"Verfügbare Felder: {choices}.")
                proportional_field = matches[0]
                proportional_alternatives = [field for field in available_point_fields
                                             if field != proportional_field]
            elif len(available_point_fields) == 1:
                proportional_field = available_point_fields[0]
            elif len(available_point_fields) > 1:
                choices = ", ".join(available_point_fields)
                display_kind = "Punktekarte" if map_type == "points" else "Linienkarte"
                return (f"Für die proportionale {display_kind} muss ein numerisches Feld ausgewählt werden. "
                        f"Wähle das inhaltlich passendste Feld und rufe das Werkzeug erneut damit auf. "
                        f"Verfügbare Felder: {choices}.")

        if map_type == "choropleth":
            property_fields = [name for name in frame.columns if name != frame.geometry.name]
            numeric_values = {}
            for field in property_fields:
                try:
                    converted = pd.to_numeric(frame[field], errors="coerce")
                    values = converted.to_numpy(dtype=float, na_value=np.nan)
                    values = values[np.isfinite(values)]
                except (TypeError, ValueError):
                    continue
                if len(values):
                    numeric_values[field] = values

            available = list(numeric_values)
            requested_field = data_field
            if requested_field:
                if requested_field in property_fields:
                    selected_field = requested_field
                else:
                    matches = [name for name in property_fields
                               if name.casefold() == requested_field.casefold()]
                    if len(matches) == 1:
                        selected_field = matches[0]
                    elif len(matches) > 1:
                        return (f"Attribut '{requested_field}' ist wegen unterschiedlicher Schreibweisen mehrdeutig. "
                                f"Bitte eines dieser Attribute exakt angeben: {', '.join(matches)}.")
                    else:
                        choices = ", ".join(available) if available else "Keine"
                        return (f"Attribut '{requested_field}' nicht gefunden. Verfügbare numerische Attribute: "
                                f"{choices}.")
                if selected_field not in numeric_values:
                    choices = ", ".join(available) if available else "Keine"
                    return (f"Attribut '{selected_field}' ist nicht numerisch oder enthält keine gültigen Zahlen. "
                            f"Verfügbare numerische Attribute: {choices}.")
            elif len(available) == 1:
                selected_field = available[0]
            elif len(available) > 1:
                return ("Für die Choropleth-Karte gibt es mehrere numerische Attribute. "
                        f"Bitte eines auswählen: {', '.join(available)}.")
            else:
                return "Für diese Daten wurden keine numerischen Attribute gefunden; eine Choropleth-Karte ist nicht möglich."

            values = numeric_values[selected_field]
            if len(values) > 1 and np.unique(values).size == 1:
                return (f"Attribut '{selected_field}' enthält überall denselben Wert; "
                        "damit lässt sich keine sinnvolle Farbskala bilden. Bitte ein anderes Attribut wählen.")
            missing_count = len(frame) - len(values)
            data_field = selected_field

        folium_kwargs = {
            "color": color,
            "weight": weight,
            "fillColor": color,
            "fillOpacity": fill_opacity
        }
        if map_type == "points":
            folium_kwargs["fillOpacity"] = 1.0
        if map_type == "heatmap":
            folium_kwargs = {"radius": 25, "blur": 15, "min_opacity": 0.3}

        new_map_layer_name = make_unique_layer_name(f"{target['name']}_{map_type}", show_layers)
        show_layers.append({
            "name": new_map_layer_name,
            "type": map_type,
            "dataset_id": target["dataset_id"],
            "data": target["data"],
            "data_field": proportional_field if map_type in {"points", "line"} else data_field,
            "folium_kwargs": folium_kwargs,
            "style": folium_kwargs
        })
        if map_type == "choropleth":
            tool_metrics["choropleth_field"] = data_field
            if missing_count:
                return (f"Erfolg: Layer '{target['name']}' wurde als Choropleth unter dem Namen "
                        f"'{new_map_layer_name}' mit Attribut '{data_field}' erstellt. "
                        f"{missing_count} von {len(frame)} Objekten haben keinen gültigen Wert und werden als fehlend markiert.")
            return (f"Erfolg: Layer '{target['name']}' wurde als Choropleth unter dem Namen "
                    f"'{new_map_layer_name}' mit Attribut '{data_field}' erstellt.")
        if map_type in {"points", "line"} and proportional_field:
            measure = "Symbolgröße" if map_type == "points" else "Linienstärke"
            display_kind = "proportionale Punktekarte" if map_type == "points" else "proportionale Linienkarte"
            tool_metrics[f"{map_type}_size_field"] = proportional_field
            if proportional_alternatives:
                return (f"Erfolg: Layer '{target['name']}' wurde als {display_kind} erstellt. "
                        f"Die {measure} basiert auf '{proportional_field}'. Weitere numerische Felder: "
                        f"{', '.join(proportional_alternatives)}.")
            return (f"Erfolg: Layer '{target['name']}' wurde als {display_kind} erstellt. "
                    f"Die {measure} basiert auf '{proportional_field}'.")
        return f"Erfolg: Layer '{target['name']}' wurde als '{map_type}' unter dem Namen '{new_map_layer_name}' zur Karte hinzugefügt."

    @tool
    def calculate_buffer_analysis(layer_name: str, distance_meters: float = 500.0) -> str:
        """
        Berechnet eine Pufferzone (Buffer) um Geometrien eines Layers mit angegebener Distanz in Metern.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers if show_layers else raw_layers
        target = resolve(pool, layer_name)
        if not target:
            return f"Layer '{layer_name}' nicht gefunden."

        try:
            gdf = input_frame(target)
            res_gdf = buffer_geometries(gdf, distance_meters)
            res_gdf = clean_gdf_for_geojson(res_gdf)
            tool_metrics["feature_count"] = len(res_gdf)

            new_layer_name = make_unique_layer_name(f"Puffer_{int(distance_meters)}m_{target['name']}", show_layers)
            show_layers.append({
                "name": new_layer_name,
                "type": "polygon",
                "data": res_gdf,
                "folium_kwargs": {"color": "red", "weight": 2, "fillColor": "orange", "fillOpacity": 0.35}
            })
            return f"Erfolg: Ein {distance_meters}m Puffer für '{target['name']}' wurde erzeugt (neuer Layer: '{new_layer_name}')."
        except Exception as e:
            return f"Fehler bei Pufferberechnung: {str(e)}"

    @tool
    def calculate_area_and_density(polygon_layer_name: str, point_layer_name: Optional[str] = None) -> str:
        """
        Berechnet Flächen (km² / m²) für Polygone. Falls ein zweiter Punkt-Layer angegeben ist,
        wird zusätzlich ein räumlicher Overlay-Join durchgeführt und die Punktdichte pro km² berechnet.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        poly_layer = resolve(pool, polygon_layer_name)
        if not poly_layer:
            return f"Polygon-Layer '{polygon_layer_name}' nicht gefunden."

        try:
            gdf_poly = input_frame(poly_layer)
            gdf_poly = gis_statistics.calculate_area(gdf_poly)

            target_field = "area_km2"
            layer_suffix = "Flaeche"

            if point_layer_name:
                pt_layer = resolve(pool, point_layer_name)
                if pt_layer:
                    gdf_pt = input_frame(pt_layer)
                    gdf_poly = gis_statistics.calculate_density(gdf_poly, gdf_pt)
                    target_field = "dichte_pro_km2"
                    layer_suffix = "Dichte"

            res_gdf = clean_gdf_for_geojson(gdf_poly)
            res_json = json.loads(res_gdf.to_json())
            tool_metrics.update(feature_count=len(res_gdf), computed_field=target_field)
            new_layer_name = make_unique_layer_name(f"{layer_suffix}_{poly_layer['name']}", show_layers)

            show_layers.append({
                "name": new_layer_name,
                "type": "choropleth",
                "data": res_gdf,
                "data_field": target_field,
                "folium_kwargs": {"fillColor": "YlOrRd", "color": "black", "weight": 1, "fillOpacity": 0.7}
            })
            return f"Erfolg: Analyse durchgeführt. Attribut '{target_field}' berechnet und als Choropleth-Layer '{new_layer_name}' visualisiert."
        except Exception as e:
            return f"Fehler bei Flächen-/Dichteberechnung: {str(e)}"

    @tool
    def run_machine_learning_spatial_prediction(layer_name: str, target_field: Optional[str] = None) -> str:
        """
        Trainiert einen Random-Forest-Regressor auf den Raumkoordinaten (lat/lon) des Ziel-Layers,
        interpoliert/prognostiziert Muster und fügt Vorhersagen sowie Residuen als Layer hinzu.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        target = resolve(pool, layer_name)
        if not target:
            return f"Layer '{layer_name}' nicht gefunden."

        try:
            gdf = gis_data_io.transform_geodata(input_frame(target), 4326)
            gdf["lon"] = gdf.geometry.x
            gdf["lat"] = gdf.geometry.y

            if not target_field:
                num_cols = [c for c in gdf.select_dtypes(include=[np.number]).columns if c not in ["lon", "lat"]]
                if not num_cols:
                    return "Keine numerische Variable für ML-Modellierung vorhanden."
                target_field = num_cols[0]

            clean_df = gdf.dropna(subset=[target_field, "lon", "lat"])
            X = clean_df[["lon", "lat"]].values
            y = clean_df[target_field].values

            rf = RandomForestRegressor(n_estimators=100, random_state=42)
            rf.fit(X, y)

            clean_df["ml_predicted"] = np.round(rf.predict(X), 2)
            clean_df["ml_residual"] = np.round(clean_df[target_field] - clean_df["ml_predicted"], 2)
            clean_gdf = clean_gdf_for_geojson(clean_df)
            tool_metrics.update(feature_count=len(clean_gdf), target_field=target_field)

            new_layer_name = make_unique_layer_name(f"ML_Vorhersage_{target['name']}", show_layers)
            show_layers.append({
                "name": new_layer_name,
                "type": "points",
                "data": clean_gdf,
                "_format": "features",
                "folium_kwargs": {"color": "purple", "icon": "eye-open"}
            })
            return f"Erfolg: Random-Forest-Modell für '{target_field}' trainiert. Vorhersagen als Layer '{new_layer_name}' eingefügt."
        except Exception as e:
            return f"Fehler bei ML-Prognose: {str(e)}"

    @tool
    def update_map_style(layer_name: str, color: Optional[str] = None, weight: Optional[int] = None,
                         fill_opacity: Optional[float] = None,
                         fill_color: Optional[str] = None) -> str:
        """
        Ändert den Stil eines sichtbaren Kartenlayers. 'color' steuert bei Choropleth nur die Umrandung;
        'fill_color' steuert die Füllung. Für Choropleth ist fill_color eine der Paletten
        'YlOrRd', 'Blues', 'Greens', 'Purples', 'Reds', 'Oranges', 'YlGn', 'YlGnBu',
        'GnBu', 'PuBu', 'BuPu' oder 'RdYlBu'. Bei anderen Flächenlayern ist es eine CSS-Farbe.

        Args:
            layer_name: Name oder ID des sichtbaren Kartenlayers.
            color: Umrandungsfarbe; bei Choropleth niemals als Füllpalette verwenden.
            weight: Stärke der Umrandung.
            fill_opacity: Transparenz der Flächenfüllung.
            fill_color: Unabhängige Füllfarbe oder Choropleth-Palette.
        """
        _, show_layers = get_agent_layers()
        target = resolve(show_layers, layer_name, require_layer=True)
        if not target:
            return f"Sichtbarer Layer '{layer_name}' nicht gefunden."

        kwargs = target.get("folium_kwargs", {})
        if color is not None:
            kwargs["color"] = color
            if target.get("type") != "choropleth":
                kwargs["fillColor"] = color
        if weight is not None:
            kwargs["weight"] = int(weight)
        if fill_opacity is not None:
            kwargs["fillOpacity"] = float(fill_opacity)
        if fill_color is not None:
            if target.get("type") == "choropleth":
                palettes = {name.casefold(): name for name in (
                    "YlOrRd", "Blues", "Greens", "Purples", "Reds", "Oranges",
                    "YlGn", "YlGnBu", "GnBu", "PuBu", "BuPu", "RdYlBu")}
                normalized = fill_color.casefold()
                if normalized not in palettes:
                    return ("Ungültige Choropleth-Füllpalette. Verfügbar sind: "
                            "YlOrRd, Blues, Greens, Purples, Reds, Oranges, YlGn, YlGnBu, "
                            "GnBu, PuBu, BuPu und RdYlBu.")
                kwargs["fillColor"] = palettes[normalized]
            else:
                kwargs["fillColor"] = fill_color

        target["folium_kwargs"] = kwargs
        changed = []
        if color is not None:
            changed.append(f"Umrandungsfarbe: {color}" if target.get("type") == "choropleth"
                           else f"Farbe: {color}")
        if fill_color is not None:
            changed.append(f"Füllung: {kwargs['fillColor']}")
        if weight is not None:
            changed.append(f"Randstärke: {weight}")
        if fill_opacity is not None:
            changed.append(f"Fülltransparenz: {fill_opacity}")
        return f"Erfolg: Stil für Layer '{layer_name}' aktualisiert. " + ", ".join(changed) + "."

    @tool
    def calculate_feature_distances(layer_name: str, max_pairs: int = 10) -> str:
        """
        Berechnet die paarweisen realen Abstände (in Metern) zwischen allen Objekten/Gebäuden
        innerhalb eines Punkt- oder Polygon-Layers.
        Geeignet bei Befehlen wie 'Berechne den Abstand zwischen den Gebäuden',
        'Wie weit sind die Gebäude voneinander entfernt?'.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        target = resolve(pool, layer_name)
        if not target:
            available = [l["name"] for l in pool]
            return f"Layer '{layer_name}' wurde nicht gefunden. Verfügbar sind: {available}"

        try:
            # 1. In einen GeoDataFrame umwandeln
            gdf = input_frame(target)
            if len(gdf) < 2:
                return f"Der Layer '{layer_name}' enthält weniger als 2 Objekte. Abstandsberechnung nicht möglich."

            # Ein Attribut ermitteln, das den Gebäudenamen enthält
            name_cols = [c for c in ["bezeichnung", "name", "einrichtung", "strasse"] if c in gdf.columns]
            label_col = name_cols[0] if name_cols else None

            results = []
            for pair in pairwise_distances(gdf):
                idx1, idx2 = pair["from_index"], pair["to_index"]
                name1 = str(gdf.iloc[idx1][label_col]) if label_col else f"Objekt {idx1 + 1}"
                name2 = str(gdf.iloc[idx2][label_col]) if label_col else f"Objekt {idx2 + 1}"

                results.append({
                    "from": name1,
                    "to": name2,
                    "distance_m": pair["distance_m"]
                })

            # Nach Entfernung aufsteigend sortieren
            results.sort(key=lambda x: x["distance_m"])

            # 3. Als Markdown-Tabelle formatieren
            summary = f"### 📏 Distanzanalyse für '{target['name']}'\n\n"
            summary += f"Es wurden die Abstände zwischen insgesamt **{len(gdf)} Gebäuden/Objekten** berechnet.\n\n"
            summary += "| Von | Nach | Abstand (Meter) | Abstand (km) |\n"
            summary += "| :--- | :--- | :---: | :---: |\n"

            for row in results[:max_pairs]:
                dist_km = round(row["distance_m"] / 1000, 2)
                summary += f"| {row['from']} | {row['to']} | **{row['distance_m']} m** | {dist_km} km |\n"

            if len(results) > max_pairs:
                summary += f"\n*(Tabelle auf die {max_pairs} kürzesten Distanzen begrenzt)*\n"

            min_pair = results[0]
            tool_metrics.update(feature_count=len(gdf), pair_count=len(results),
                                min_distance_m=min_pair["distance_m"])
            summary += f"\n- **Nächste Nachbarn:** `{min_pair['from']}` und `{min_pair['to']}` liegen mit nur **{min_pair['distance_m']} m** am dichtesten beieinander."

            return summary

        except Exception as e:
            return f"Fehler bei der Distanzberechnung: {str(e)}"

    @tool
    def export_current_map(format: str) -> str:
        """Bereitet einen Download der aktuell sichtbaren Karte vor.

        Args:
            format: 'html' für die interaktive Karte, 'geojson' für sichtbare Vektordaten, 'png' für einen Kartenscreenshot oder 'pdf' für eine druckbare Kartenansicht.
        """
        normalized_format = format.strip().casefold().lstrip(".")
        visible_layers = session.map_view
        if normalized_format == "html":
            map_layers = gis_data_io.prepare_map_layers(visible_layers)
            map_object = create_layered_map(map_layers, show_basemap=session.show_basemap)
            if session.map_title:
                Titel_hinzufügen(map_object, session.map_title)
            artifact = {
                "data": render_map_html(map_object),
                "file_name": "geogpt_map.html",
                "mime_type": "text/html",
            }
        elif normalized_format == "geojson":
            if not visible_layers:
                return "Fehler: Es gibt keine sichtbaren Vektorebenen zum Exportieren."
            files = []
            for index, layer in enumerate(visible_layers, start=1):
                frame = extract_features_to_gdf(layer["data"], source_crs=layer.get("crs"))
                frame = transform_geodata(frame, 4326)
                collection = json.loads(frame.to_json())
                geojson_bytes = json.dumps(collection, ensure_ascii=False, allow_nan=False).encode("utf-8")
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", layer["name"]).strip("._-") or "layer"
                files.append((f"{index:02d}_{safe_name}.geojson", geojson_bytes))

            if len(files) == 1:
                file_name, data = files[0]
                artifact = {"data": data, "file_name": file_name, "mime_type": "application/geo+json"}
            else:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for file_name, data in files:
                        archive.writestr(file_name, data)
                artifact = {
                    "data": buffer.getvalue(),
                    "file_name": "geogpt_layers_geojson.zip",
                    "mime_type": "application/zip",
                }
        elif normalized_format == "png":
            map_layers = gis_data_io.prepare_map_layers(visible_layers)
            map_object = create_layered_map(map_layers, show_basemap=session.show_basemap)
            if session.map_title:
                Titel_hinzufügen(map_object, session.map_title)
            html_document = render_map_html(map_object).decode("utf-8")
            artifact = {
                "data": render_map_png(html_document, show_basemap=session.show_basemap),
                "file_name": "geogpt_map.png",
                "mime_type": "image/png",
            }
        elif normalized_format == "pdf":
            map_layers = gis_data_io.prepare_map_layers(visible_layers)
            map_object = create_layered_map(map_layers, show_basemap=session.show_basemap)
            if session.map_title:
                Titel_hinzufügen(map_object, session.map_title)
            html_document = render_map_html(map_object).decode("utf-8")
            map_png = render_map_png(html_document, show_basemap=session.show_basemap)
            artifact = {
                "data": render_map_pdf(map_png),
                "file_name": "geogpt_map.pdf",
                "mime_type": "application/pdf",
            }
        else:
            return "Fehler: Unterstützte Exportformate sind html, geojson, png und pdf."

        pending_export["artifact"] = artifact
        return f"Erfolg: Der Export '{artifact['file_name']}' wurde vorbereitet und steht zum Herunterladen bereit."

    @tool
    def manage_map_layer(layer_name: str, action: str) -> str:
        """Entfernt einen Kartenlayer oder blendet ihn ein bzw. aus.

        Args:
            layer_name: Exakter Name oder Layer-ID eines Kartenlayers, auch wenn er ausgeblendet ist.
            action: 'remove', 'hide' oder 'show'. Entfernen löscht nur die Kartendarstellung; Upload-Daten bleiben erhalten.
        """
        actions = {"remove", "hide", "show"}
        normalized_action = action.strip().casefold()
        if normalized_action not in actions:
            return "Fehler: Aktion muss remove, hide oder show sein."
        target = session.resolve_in(session.layer_management_view, layer_name, require_layer=True)
        if not target:
            return f"Fehler: Kartenlayer '{layer_name}' wurde nicht gefunden."
        layer_id = target["layer_id"]
        if normalized_action == "remove":
            pending_map_action["action"] = {"type": "remove", "layer_id": layer_id}
            return f"Erfolg: Layer '{target['name']}' wurde von der Karte entfernt. Die hochgeladenen Quelldaten bleiben erhalten."
        visible = normalized_action == "show"
        pending_map_action["action"] = {"type": "visibility", "layer_id": layer_id, "visible": visible}
        verb = "eingeblendet" if visible else "ausgeblendet"
        return f"Erfolg: Layer '{target['name']}' wurde {verb}."

    @tool
    def set_map_title(title: str) -> str:
        """Set the single global title shown above the current map and in visual exports.

        Args:
            title: A concise title supplied by the user or generated from the currently visible map layers.
        """
        normalized_title = title.strip()
        if not normalized_title:
            return "Fehler: Der Kartentitel darf nicht leer sein."
        pending_map_action["action"] = {"type": "title", "title": normalized_title}
        return f"Erfolg: Der Kartentitel wurde auf '{normalized_title}' gesetzt."

    @tool
    def clear_map_title() -> str:
        """Remove the current global map title from the map and visual exports."""
        pending_map_action["action"] = {"type": "clear_title"}
        if not session.map_title:
            return "Erfolg: Die Karte hat bereits keinen Titel."
        return "Erfolg: Der Kartentitel wurde entfernt."

    TOOLS = [
        fetch_city_boundary_osm,
        visualize_layer_on_map,
        calculate_buffer_analysis,
        calculate_area_and_density,
        run_machine_learning_spatial_prediction,
        update_map_style,
        calculate_feature_distances,
        manage_map_layer,
        export_current_map,
        set_map_title,
        clear_map_title
    ]

    def bind_atomic(legacy):
        @wraps(legacy.func)
        def execute(*args, **kwargs):
            nonlocal working
            with session.lock:
                working = session.legacy_layers()
                used_dataset_ids.clear()
                tool_metrics.clear()
                pending_map_action.clear()
                pending_export.clear()
                arguments = signature(legacy.func).bind(*args, **kwargs)
                arguments.apply_defaults()
                try:
                    message = legacy.func(*args, **kwargs)
                    success = message.startswith("Erfolg:") or (
                        legacy.name == "calculate_feature_distances" and message.startswith("###"))
                    if not success:
                        category = "ambiguous_reference" if "mehrdeutig" in message else (
                            "not_found" if "nicht gefunden" in message else "tool_error")
                        return session.record_failure(message, category).message
                    metrics = dict(tool_metrics)
                    for key in ("distance_meters", "max_pairs"):
                        if key in arguments.arguments:
                            metrics[key] = arguments.arguments[key]
                    map_action = pending_map_action.get("action")
                    export_artifact = pending_export.get("artifact")
                    if export_artifact:
                        result = session.commit_export_artifact(export_artifact, message=message,
                                                                tool_name=legacy.name)
                    elif map_action:
                        result = session.commit_map_action(map_action, message=message, tool_name=legacy.name)
                    else:
                        result = session.commit_views(*working, message=message, tool_name=legacy.name,
                                                      parent_ids=tuple(sorted(used_dataset_ids)), metrics=metrics)
                    return result.message
                except AmbiguousReference as exc:
                    return session.record_failure(str(exc), "ambiguous_reference").message
                except Exception as exc:
                    return session.record_failure(f"Fehler bei {legacy.name}: {exc}", "commit_or_tool_error").message
                finally:
                    working = None
                    used_dataset_ids.clear()
                    tool_metrics.clear()
                    pending_map_action.clear()
                    pending_export.clear()
        return StructuredTool.from_function(execute, name=legacy.name, description=legacy.description,
                                            args_schema=legacy.args_schema)

    return [bind_atomic(registered) for registered in TOOLS]
