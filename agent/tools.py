"""Session-bound tool adapters; no current-user module global or context variable."""
import json
import io
import re
import urllib.parse
import zipfile
import unicodedata
from functools import wraps
from inspect import signature
from typing import List, Optional, Literal
from datetime import datetime, timezone

import requests
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely.geometry import shape
from shapely import get_coordinates
from sklearn.ensemble import RandomForestRegressor
from langchain_core.tools import tool, StructuredTool

from core.session import AmbiguousReference, SessionData
from core.contracts import normalize_crs
from .attributes import is_non_measure_attribute
from gis.data_io import extract_features_to_gdf, transform_geodata
from gis import data_io as gis_data_io
from gis.geometry import (buffer_geometries, pairwise_distances, cross_layer_distances,
                          spatial_relationship_join, overlay_polygon_layers)
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
    pending_attribute_relation = {}

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

    @tool
    def check_dataset_crs_consistency() -> str:
        """Prüft die Koordinatenreferenzsysteme aller aktuell hochgeladenen Datensätze und meldet, ob sie übereinstimmen. Verändert keine Daten."""
        raw_layers, _ = get_agent_layers()
        if not raw_layers:
            return "Fehler: Es sind keine hochgeladenen Datensätze zum Prüfen vorhanden."

        known = []
        unknown = []
        for dataset in raw_layers:
            value = dataset.get("crs")
            if value is None:
                unknown.append(dataset["name"])
            else:
                known.append((dataset["name"], CRS.from_user_input(value)))

        if not known:
            return (f"Erfolg: {len(raw_layers)} Datensätze geprüft. Für keinen Datensatz ist ein "
                    "Koordinatenreferenzsystem bekannt: " + ", ".join(unknown) + ".")

        reference = known[0][1]
        consistent = all(reference.equals(crs, ignore_axis_order=True) for _, crs in known[1:])
        details = "; ".join(f"{name}: {crs.to_string()}" for name, crs in known)
        if unknown:
            details += "; Unbekannt: " + ", ".join(unknown)
            return (f"Erfolg: {len(raw_layers)} Datensätze geprüft. Die bekannten Koordinatenreferenzsysteme "
                    f"{'stimmen überein' if consistent else 'sind nicht einheitlich'} ({details}); "
                    "wegen unbekannter CRS ist kein vollständiger Vergleich möglich.")
        return (f"Erfolg: {len(raw_layers)} Datensätze geprüft. Die Koordinatenreferenzsysteme "
                f"{'stimmen überein' if consistent else 'sind nicht einheitlich'} ({details}).")

    @tool
    def transform_dataset_crs(dataset_name: str, target_crs: str) -> str:
        """Transformiert einen hochgeladenen Geodatensatz in ein vom Nutzer angegebenes CRS und speichert das Ergebnis als neue Sitzungsebene. Erfordert ein bekanntes Quell-CRS; die Quelldaten bleiben unverändert."""
        raw_layers, show_layers = get_agent_layers()
        try:
            target = resolve(raw_layers + show_layers, dataset_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if target is None:
            return f"Fehler: Datensatz oder Layer '{dataset_name}' wurde nicht gefunden."
        try:
            normalized_target = normalize_crs(target_crs)
            if normalized_target is None:
                return "Fehler: Ein Ziel-Koordinatenreferenzsystem muss angegeben werden."
            frame = input_frame(target)
            source_crs = CRS.from_user_input(frame.crs)
            destination_crs = CRS.from_user_input(normalized_target)
            if source_crs.equals(destination_crs, ignore_axis_order=True):
                return (f"Erfolg: '{target['name']}' verwendet bereits {destination_crs.to_string()}; "
                        "es wurde keine zusätzliche Ebene erzeugt.")

            converted = transform_geodata(frame, destination_crs)
            converted_name = make_unique_layer_name(
                f"{target['name']}_{destination_crs.to_string().replace(':', '_')}", show_layers)
            renderer = target.get("type", "geojson")
            style = target.get("folium_kwargs", target.get("style", {}))
            if not isinstance(style, dict):
                style = {}
            show_layers.append({
                "name": converted_name,
                "type": renderer,
                "data": clean_gdf_for_geojson(converted),
                "data_field": target.get("data_field"),
                "_format": "collection",
                "crs": destination_crs.to_string(),
                "folium_kwargs": style,
            })
            tool_metrics.update(feature_count=len(converted), source_crs=source_crs.to_string(),
                                target_crs=destination_crs.to_string())
            return (f"Erfolg: '{target['name']}' wurde von {source_crs.to_string()} nach "
                    f"{destination_crs.to_string()} transformiert und als neue Ebene "
                    f"'{converted_name}' gespeichert. Die Quelldaten bleiben unverändert.")
        except Exception as exc:
            return f"Fehler bei der CRS-Transformation: {exc}"

    @tool
    def check_uploaded_data_quality() -> str:
        """Prüft hochgeladene Datensätze auf fehlende oder auffällige Koordinaten und ungültige Geometrien. Meldet nur Befunde und verändert keine Daten."""
        raw_layers, _ = get_agent_layers()
        if not raw_layers:
            return "Fehler: Es sind keine hochgeladenen Datensätze zum Prüfen vorhanden."

        def records(data):
            if hasattr(data, "geometry") and hasattr(data, "columns"):
                return [(geometry, None, None) for geometry in data.geometry]
            if isinstance(data, dict):
                if data.get("type") == "FeatureCollection":
                    values = data.get("features", [])
                elif data.get("type") == "Feature":
                    values = [data]
                elif "features" in data:
                    values = data.get("features", [])
                elif data.get("type") in ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"):
                    values = [{"geometry": data}]
                else:
                    values = [data]
            elif isinstance(data, list):
                values = data
            else:
                values = []
            result = []
            for item in values:
                if not isinstance(item, dict):
                    result.append((None, None, None))
                elif "geometry" in item:
                    result.append((item.get("geometry"), None, None))
                elif "lat" in item or "lon" in item:
                    result.append((None, item.get("lon"), item.get("lat")))
                else:
                    result.append((None, None, None))
            return result

        def missing(value):
            if value is None:
                return True
            try:
                result = pd.isna(value)
                return isinstance(result, (bool, np.bool_)) and bool(result)
            except (TypeError, ValueError):
                return False

        summaries = []
        for dataset in raw_layers:
            data = dataset["data"]
            items = records(data)
            if not items:
                summaries.append(f"{dataset['name']}: keine prüfbaren Geometrie-/Koordinatendatensätze (nicht anwendbar)")
                continue

            missing_count = anomalous_count = invalid_count = 0
            try:
                crs = CRS.from_user_input(dataset.get("crs")) if dataset.get("crs") else None
            except Exception:
                crs = None
            geographic = crs is not None and crs.is_geographic

            for geometry, lon, lat in items:
                geom = None
                if isinstance(data, list) and (lon is not None or lat is not None):
                    if missing(lon) or missing(lat):
                        missing_count += 1
                        continue
                    try:
                        x, y = float(lon), float(lat)
                        if not np.isfinite([x, y]).all() or (geographic and not (-180 <= x <= 180 and -90 <= y <= 90)):
                            anomalous_count += 1
                    except (TypeError, ValueError):
                        anomalous_count += 1
                    continue

                if geometry is None:
                    missing_count += 1
                    continue
                try:
                    if isinstance(geometry, dict):
                        geom = shape(geometry)
                    else:
                        geom = geometry
                    if geom is None or geom.is_empty:
                        missing_count += 1
                        continue
                    coords = get_coordinates([geom])
                    if not np.isfinite(coords).all() or (geographic and len(coords) and (
                            (coords[:, 0] < -180).any() or (coords[:, 0] > 180).any() or
                            (coords[:, 1] < -90).any() or (coords[:, 1] > 90).any())):
                        anomalous_count += 1
                    if not geom.is_valid:
                        invalid_count += 1
                except Exception:
                    anomalous_count += 1
                    invalid_count += 1

            summaries.append(
                f"{dataset['name']}: {len(items)} Datensätze, {missing_count} mit fehlenden Koordinaten, "
                f"{anomalous_count} mit auffälligen Koordinaten, {invalid_count} mit ungültiger Geometrie"
            )

        return "Erfolg: Datenqualitätsprüfung abgeschlossen. " + "; ".join(summaries) + "."

    @tool
    def associate_dataset_attributes(source_dataset: str, target_dataset: str,
                                     source_field: Optional[str] = None,
                                     target_field: Optional[str] = None) -> str:
        """Vergleicht zwei hochgeladene Datensätze über Attributwerte, speichert die Feldzuordnung temporär in der Sitzung und berichtet Treffer. Keine Geometrie-/Raumverknüpfung und kein Zusammenführen der Daten."""
        raw_layers, _ = get_agent_layers()
        try:
            source = resolve(raw_layers, source_dataset)
            target = resolve(raw_layers, target_dataset)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if source is None:
            return f"Fehler: Datensatz '{source_dataset}' nicht gefunden."
        if target is None:
            return f"Fehler: Datensatz '{target_dataset}' nicht gefunden."
        if source["dataset_id"] == target["dataset_id"]:
            return "Fehler: Bitte zwei unterschiedliche Datensätze angeben."

        def rows(data):
            if hasattr(data, "columns") and hasattr(data, "geometry"):
                geometry_name = data.geometry.name
                return [{str(key): value for key, value in row.items() if key != geometry_name}
                        for row in data.drop(columns=[geometry_name]).to_dict(orient="records")]
            if isinstance(data, dict):
                if data.get("type") == "FeatureCollection":
                    values = data.get("features", [])
                elif data.get("type") == "Feature":
                    values = [data]
                elif isinstance(data.get("features"), list):
                    values = data["features"]
                else:
                    values = [data]
            elif isinstance(data, list):
                values = data
            else:
                values = []
            result = []
            for item in values:
                if not isinstance(item, dict):
                    continue
                properties = item.get("properties")
                record = {key: value for key, value in item.items()
                          if key not in ("geometry", "properties", "type", "bbox")}
                if isinstance(properties, dict):
                    record.update(properties)
                result.append({str(key): value for key, value in record.items()})
            return result

        source_rows, target_rows = rows(source["data"]), rows(target["data"])
        if not source_rows or not target_rows:
            return "Fehler: Mindestens ein Datensatz enthält keine auswertbaren Attributzeilen."
        source_fields = list(dict.fromkeys(key for row in source_rows for key in row))
        target_fields = list(dict.fromkeys(key for row in target_rows for key in row))

        def field_key(field):
            return "".join(ch for ch in unicodedata.normalize("NFKC", str(field)).casefold() if ch.isalnum())

        def identifier_field(field):
            normalized = field_key(field)
            return any(token in normalized for token in (
                "code", "kenn", "schluessel", "schlüssel", "identifier", "region", "district",
                "bezirk", "gemeinde", "areaid", "ags", "objectid", "featureid", "recordid",
                "代码", "编码", "编号", "区划", "行政区", "区域", "标识"
            ))

        def value_key(value):
            if value is None:
                return None
            try:
                missing_value = pd.isna(value)
                if isinstance(missing_value, (bool, np.bool_)) and missing_value:
                    return None
            except (TypeError, ValueError):
                pass
            if isinstance(value, (float, np.floating)) and np.isfinite(value) and float(value).is_integer():
                value = int(value)
            normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
            return normalized or None

        source_values = {field: [value_key(row.get(field)) for row in source_rows] for field in source_fields}
        target_values = {field: [value_key(row.get(field)) for row in target_rows] for field in target_fields}

        def overlap_score(left, right):
            left_set, right_set = set(filter(None, left)), set(filter(None, right))
            denominator = min(len(left_set), len(right_set))
            return len(left_set & right_set) / denominator if denominator else 0.0

        if source_field and source_field not in source_fields:
            return f"Fehler: Feld '{source_field}' ist in '{source['name']}' nicht vorhanden. Verfügbar: {', '.join(source_fields)}."
        if target_field and target_field not in target_fields:
            return f"Fehler: Feld '{target_field}' ist in '{target['name']}' nicht vorhanden. Verfügbar: {', '.join(target_fields)}."

        if source_field and target_field:
            pass
        else:
            pairs = [(left, right) for left in source_fields for right in target_fields
                     if (not source_field or left == source_field) and (not target_field or right == target_field)]
            exact = [(left, right) for left, right in pairs if field_key(left) == field_key(right)]
            exact_identifiers = [(left, right) for left, right in exact
                                 if identifier_field(left) or identifier_field(right)]
            if exact_identifiers:
                exact = exact_identifiers
            if not source_field and not target_field and len(exact) == 1:
                source_field, target_field = exact[0]
            else:
                inferred = [pair for pair in pairs
                            if overlap_score(source_values[pair[0]], target_values[pair[1]]) >= 0.8]
                identifier_candidates = [pair for pair in inferred
                                         if identifier_field(pair[0]) or identifier_field(pair[1])]
                if identifier_candidates:
                    inferred = identifier_candidates
                candidates = exact if exact else inferred
                if candidates:
                    scores = [(overlap_score(source_values[left], target_values[right]), left, right)
                              for left, right in candidates]
                    scores.sort(reverse=True)
                    if len(scores) == 1 or scores[0][0] > scores[1][0]:
                        _, source_field, target_field = scores[0]
                    else:
                        options = ", ".join(f"{left} ↔ {right}" for _, left, right in scores[:5])
                        return f"Fehler: Die passende Feldzuordnung ist mehrdeutig. Bitte gib die Felder an. Kandidaten: {options}."
                else:
                    return ("Fehler: Es konnte keine eindeutige Feldzuordnung erkannt werden. "
                            f"Felder in '{source['name']}': {', '.join(source_fields)}; "
                            f"Felder in '{target['name']}': {', '.join(target_fields)}. "
                            "Bitte gib source_field und target_field an.")

        source_column = source_values[source_field]
        target_column = target_values[target_field]
        source_keys = set(filter(None, source_column))
        target_keys = set(filter(None, target_column))
        matched_source = sum(value is not None and value in target_keys for value in source_column)
        matched_target = sum(value is not None and value in source_keys for value in target_column)
        unmatched_source = sum(value is None or value not in target_keys for value in source_column)
        unmatched_target = sum(value is None or value not in source_keys for value in target_column)
        duplicate_source = sum(count - 1 for count in pd.Series([v for v in source_column if v is not None]).value_counts()
                               if count > 1)
        duplicate_target = sum(count - 1 for count in pd.Series([v for v in target_column if v is not None]).value_counts()
                               if count > 1)
        sample_matched = list(dict.fromkeys(v for v in target_column if v is not None and v in source_keys))[:5]
        sample_source_unmatched = list(dict.fromkeys(v for v in source_column if v is None or v not in target_keys))[:5]
        sample_target_unmatched = list(dict.fromkeys(v for v in target_column if v is None or v not in source_keys))[:5]

        message = (f"Erfolg: Temporäre Attributzuordnung gespeichert: '{source['name']}.{source_field}' → "
                   f"'{target['name']}.{target_field}'. Auf Basis der Feldwerte sind {matched_target} von "
                   f"{len(target_rows)} Zielzeilen und {matched_source} von {len(source_rows)} Quellzeilen zuordenbar; "
                   f"nicht zuordenbar: {unmatched_target} Zielzeilen und {unmatched_source} Quellzeilen. "
                   f"Doppelte Schlüssel: {duplicate_source} in der Quelle, {duplicate_target} im Ziel.")
        if sample_matched:
            message += f" Beispiele zugeordneter Werte: {sample_matched}."
        if sample_target_unmatched:
            message += f" Beispiele nicht zugeordneter Zielwerte: {sample_target_unmatched}."
        if sample_source_unmatched:
            message += f" Beispiele nicht zugeordneter Quellwerte: {sample_source_unmatched}."
        relation = {
            "source_dataset_id": source["dataset_id"], "source_dataset": source["name"],
            "source_field": source_field, "target_dataset_id": target["dataset_id"],
            "target_dataset": target["name"], "target_field": target_field,
            "metrics": {"source_rows": len(source_rows), "target_rows": len(target_rows),
                        "matched_source_rows": matched_source, "matched_target_rows": matched_target,
                        "unmatched_source_rows": unmatched_source, "unmatched_target_rows": unmatched_target,
                        "duplicate_source_keys": duplicate_source, "duplicate_target_keys": duplicate_target}
        }
        pending_attribute_relation["relation"] = relation
        return message

    @tool
    def filter_dataset_by_attribute(dataset_name: str, field: str,
                                    operator: Literal["equals", "contains", "greater_than", "less_than", "between", "date_between", "within_last_years"],
                                    value: str, end_value: Optional[str] = None) -> str:
        """Filter an uploaded spatial dataset by one attribute and add the selected features as a new map layer. Supports equals/contains, numeric comparisons and ranges, date ranges, and dates within the last N years. Does not modify the source dataset."""
        raw_layers, show_layers = get_agent_layers()
        try:
            target = resolve(raw_layers, dataset_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if target is None:
            return f"Fehler: Datensatz '{dataset_name}' nicht gefunden."

        data = target["data"]
        if hasattr(data, "columns") and hasattr(data, "geometry"):
            if field not in data.columns:
                return f"Fehler: Feld '{field}' nicht gefunden. Verfügbar: {', '.join(map(str, data.columns))}."
            if field == data.geometry.name:
                return "Fehler: Die Geometrie selbst kann nicht als Attribut gefiltert werden."
            values = data[field]
        else:
            if isinstance(data, dict):
                if data.get("type") == "FeatureCollection":
                    records = data.get("features", [])
                elif data.get("type") == "Feature":
                    records = [data]
                elif isinstance(data.get("features"), list):
                    records = data["features"]
                else:
                    records = [data]
            elif isinstance(data, list):
                records = data
            else:
                records = []
            values = []
            for record in records:
                if not isinstance(record, dict):
                    values.append(None)
                else:
                    properties = record.get("properties")
                    values.append(properties.get(field) if isinstance(properties, dict) and field in properties
                                  else record.get(field))
            if not any(isinstance(record, dict) and (
                    field in record or (isinstance(record.get("properties"), dict) and field in record["properties"]))
                       for record in records):
                available = list(dict.fromkeys(
                    key for record in records if isinstance(record, dict)
                    for key in (list(record.get("properties", {}).keys()) if isinstance(record.get("properties"), dict) else []) +
                                [key for key in record if key not in ("geometry", "properties", "type", "bbox")]
                ))
                return f"Fehler: Feld '{field}' nicht gefunden. Verfügbar: {', '.join(map(str, available))}."

        def missing_value(item):
            if item is None:
                return True
            try:
                result = pd.isna(item)
                return isinstance(result, (bool, np.bool_)) and bool(result)
            except (TypeError, ValueError):
                return False

        def match(item):
            if missing_value(item):
                return False
            if operator == "equals":
                if isinstance(item, (int, float, np.number)) and not isinstance(item, (bool, np.bool_)):
                    try:
                        return float(item) == float(value)
                    except (TypeError, ValueError):
                        return False
                return str(item).strip().casefold() == value.strip().casefold()
            if operator == "contains":
                return value.casefold() in str(item).casefold()
            if operator in ("greater_than", "less_than", "between"):
                try:
                    number = float(item)
                    lower = float(value)
                    if operator == "greater_than":
                        return number > lower
                    if operator == "less_than":
                        return number < lower
                    if end_value is None:
                        raise ValueError("between requires end_value")
                    return lower <= number <= float(end_value)
                except (TypeError, ValueError, OverflowError):
                    return False
            if operator in ("within_last_years", "date_between"):
                parsed = pd.to_datetime(item, errors="coerce", utc=True)
                if pd.isna(parsed):
                    return False
                if operator == "within_last_years":
                    try:
                        years = float(value)
                        if years <= 0:
                            return False
                        cutoff = pd.Timestamp(datetime.now(timezone.utc)) - pd.DateOffset(days=round(years * 365.2425))
                        return cutoff <= parsed <= pd.Timestamp(datetime.now(timezone.utc))
                    except (TypeError, ValueError, OverflowError):
                        return False
                if end_value is None:
                    return False
                start = pd.to_datetime(value, errors="coerce", utc=True)
                end = pd.to_datetime(end_value, errors="coerce", utc=True)
                return not pd.isna(start) and not pd.isna(end) and start <= parsed <= end
            return False

        if hasattr(data, "columns") and hasattr(data, "geometry"):
            mask = values.map(match)
            result_data = data.loc[mask].copy()
            feature_count = len(result_data)
        else:
            selected = [record for record, item in zip(records, values) if match(item)]
            feature_count = len(selected)
            if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                result_data = dict(data)
                result_data["features"] = selected
            elif isinstance(data, dict) and data.get("type") == "Feature":
                result_data = {"type": "FeatureCollection", "features": selected}
                if "crs" in data:
                    result_data["crs"] = data["crs"]
            elif isinstance(data, dict) and isinstance(data.get("features"), list):
                result_data = dict(data)
                result_data["features"] = selected
            else:
                result_data = selected

        layer_name = make_unique_layer_name(f"Filter_{target['name']}", show_layers)
        renderer = target.get("type", "geojson")
        show_layers.append({"name": layer_name, "type": renderer, "data": result_data,
                            "_format": "collection", "crs": target.get("crs")})
        tool_metrics.update(feature_count=feature_count, filter_field=field,
                            filter_operator=operator, filter_value=value,
                            filter_end_value=end_value)
        return (f"Erfolg: '{target['name']}' anhand des Feldes '{field}' gefiltert "
                f"({operator}: {value}{f' bis {end_value}' if end_value is not None else ''}). "
                f"{feature_count} von {len(values)} Datensätzen ausgewählt und als neuer Layer '{layer_name}' hinzugefügt.")

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
            res_gdf, metric_crs = buffer_geometries(gdf, distance_meters, return_crs=True)
            res_gdf = clean_gdf_for_geojson(res_gdf)
            tool_metrics["feature_count"] = len(res_gdf)
            tool_metrics["buffer_crs"] = metric_crs

            new_layer_name = make_unique_layer_name(f"Puffer_{int(distance_meters)}m_{target['name']}", show_layers)
            show_layers.append({
                "name": new_layer_name,
                "type": "polygon",
                "data": res_gdf,
                "folium_kwargs": {"color": "red", "weight": 2, "fillColor": "orange", "fillOpacity": 0.35}
            })
            return (f"Erfolg: Ein {distance_meters}m Puffer für '{target['name']}' wurde im lokalen "
                    f"UTM-Koordinatensystem {metric_crs} berechnet und als neuer Layer '{new_layer_name}' hinzugefügt.")
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
    def calculate_derived_indicator(layer_name: str, numerator_field: str,
                                    denominator_field: str, output_field: str,
                                    scale_factor: float = 1.0, decimals: int = 2) -> str:
        """Berechnet je Datensatz eine abgeleitete Kennzahl: Zählerfeld geteilt durch Nennerfeld mal Skalierungsfaktor. Erzeugt einen neuen Layer und verändert den Quelldatensatz nicht. Nenner 0 und fehlende/nichtnumerische Werte werden als fehlend markiert."""
        raw_layers, show_layers = get_agent_layers()
        try:
            target = resolve(show_layers + raw_layers, layer_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if target is None:
            return f"Fehler: Datensatz '{layer_name}' wurde nicht gefunden."
        try:
            source_frame = input_frame(target)
            result = gis_statistics.calculate_derived_indicator(
                source_frame, numerator_field=numerator_field,
                denominator_field=denominator_field, output_field=output_field,
                scale_factor=scale_factor, decimals=decimals)
            missing_count = int(result[output_field].isna().sum())
            result = clean_gdf_for_geojson(result)
            geometry_types = set(result.geometry.geom_type.dropna())
            map_type = ("choropleth" if geometry_types <= {"Polygon", "MultiPolygon"} else
                        "points" if geometry_types <= {"Point", "MultiPoint"} else
                        "line" if geometry_types <= {"LineString", "MultiLineString"} else "geojson")
            new_name = make_unique_layer_name(f"{output_field}_{target['name']}", show_layers)
            show_layers.append({
                "name": new_name, "type": map_type, "data": result,
                "data_field": output_field if map_type == "choropleth" else None,
                "_format": "collection", "crs": target.get("crs"),
                "folium_kwargs": {"fillColor": "YlOrRd", "color": "black",
                                  "weight": 1, "fillOpacity": 0.7}
            })
            tool_metrics.update(feature_count=len(result), output_field=output_field,
                                missing_indicator_count=missing_count,
                                numerator_field=numerator_field,
                                denominator_field=denominator_field,
                                scale_factor=scale_factor)
            message = (f"Erfolg: '{output_field}' = '{numerator_field}' / '{denominator_field}' × "
                       f"{scale_factor:g} wurde für '{target['name']}' berechnet. Der neue Layer "
                       f"'{new_name}' enthält {len(result)} Datensätze.")
            if missing_count:
                message += (f" Für {missing_count} Datensätze ist der Wert fehlend, weil Zähler oder "
                            "Nenner fehlen/ungültig sind oder der Nenner 0 ist.")
            return message
        except Exception as exc:
            return f"Fehler bei der Kennzahlenberechnung: {exc}"

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
    def calculate_feature_distances(layer_name: str, max_pairs: int = 10,
                                    target_layer_name: Optional[str] = None,
                                    query_mode: Literal["all_pairs", "nearest", "within_distance"] = "all_pairs",
                                    distance_threshold_meters: Optional[float] = None) -> str:
        """
        Berechnet Distanzen zwischen Geometrien. 'all_pairs' berechnet alle Paare
        innerhalb eines Layers. 'nearest' findet pro Quellobjekt den nächsten
        Eintrag in einem Ziellayer. 'within_distance' findet alle layerübergreifenden
        Paare innerhalb distance_threshold_meters. Unterstützt Punkte, Linien und Flächen.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        target = resolve(pool, layer_name)
        if not target:
            available = [l["name"] for l in pool]
            return f"Layer '{layer_name}' wurde nicht gefunden. Verfügbar sind: {available}"
        if query_mode not in {"all_pairs", "nearest", "within_distance"}:
            return "Fehler: query_mode muss all_pairs, nearest oder within_distance sein."

        try:
            gdf = input_frame(target)
            if query_mode != "all_pairs":
                if not target_layer_name:
                    return "Fehler: Für nearest oder within_distance muss ein Ziel-Layer angegeben werden."
                other = resolve(pool, target_layer_name)
                if not other:
                    return f"Ziel-Layer '{target_layer_name}' wurde nicht gefunden."
                if other["dataset_id"] == target["dataset_id"]:
                    return ("Fehler: Layerübergreifende Modi benötigen zwei unterschiedliche Datensätze. "
                            "Für Abstände innerhalb eines Layers nutze query_mode='all_pairs'.")
                other_gdf = input_frame(other)
                if query_mode == "within_distance" and distance_threshold_meters is None:
                    return "Fehler: Für within_distance muss distance_threshold_meters angegeben werden."
                pairs, metric_crs = cross_layer_distances(
                    gdf, other_gdf,
                    query_mode="nearest" if query_mode == "nearest" else "within_distance",
                    distance_meters=distance_threshold_meters,
                    return_crs=True
                )

                def label(frame, position):
                    columns = [column for column in ("bezeichnung", "name", "einrichtung", "strasse")
                               if column in frame.columns]
                    return str(frame.iloc[position][columns[0]]) if columns else f"Objekt {position + 1}"

                metric_values = [pair["distance_m"] for pair in pairs]
                tool_metrics.update(source_feature_count=len(gdf), target_feature_count=len(other_gdf),
                                    pair_count=len(pairs), distance_crs=metric_crs,
                                    query_mode=query_mode)
                if distance_threshold_meters is not None:
                    tool_metrics["distance_threshold_meters"] = distance_threshold_meters

                if query_mode == "nearest":
                    if distance_threshold_meters is not None:
                        within = [pair for pair in pairs if pair["distance_m"] <= distance_threshold_meters]
                        outside = [pair for pair in pairs if pair["distance_m"] > distance_threshold_meters]
                        summary = (f"### Distanzabfrage: '{target['name']}' → '{other['name']}'\n\n"
                                   f"Für {len(pairs)} Quellobjekte wurde jeweils der nächste Ziellayer-Eintrag gesucht. "
                                   f"Innerhalb von {distance_threshold_meters:g} m: {len(within)}; außerhalb: {len(outside)}.\n\n")
                        display = sorted(outside, key=lambda pair: pair["distance_m"])
                        heading = "Objekte außerhalb des Grenzwerts"
                    else:
                        summary = (f"### Nächste Nachbarn: '{target['name']}' → '{other['name']}'\n\n"
                                   f"Für {len(pairs)} Quellobjekte wurde jeweils der nächste Eintrag im Ziellayer gesucht.\n\n")
                        display = pairs
                        heading = "Kürzeste Abstände"
                else:
                    summary = (f"### Distanzabfrage: '{target['name']}' zu '{other['name']}'\n\n"
                               f"{len(pairs)} Objektpaare liegen innerhalb von {distance_threshold_meters:g} m.\n\n")
                    display = pairs
                    heading = "Nächste Paare innerhalb des Grenzwerts"

                summary += f"**{heading}** (Meter; Projektionssystem {metric_crs})\n\n"
                summary += "| Von | Nach | Abstand (m) |\n| :--- | :--- | ---: |\n"
                for pair in display[:max_pairs]:
                    summary += (f"| {label(gdf, pair['from_index'])} | "
                                f"{label(other_gdf, pair['to_index'])} | {pair['distance_m']:.1f} |\n")
                if len(display) > max_pairs:
                    summary += f"\n*(Anzeige auf {max_pairs} Paare begrenzt.)*\n"
                if metric_values:
                    tool_metrics["min_distance_m"] = round(min(metric_values), 1)
                return summary

            if target_layer_name:
                return "Fehler: Für einen Ziel-Layer muss query_mode='nearest' oder 'within_distance' angegeben werden."
            if distance_threshold_meters is not None:
                return "Fehler: distance_threshold_meters wird nur mit query_mode='nearest' oder 'within_distance' verwendet."
            if len(gdf) < 2:
                return f"Der Layer '{layer_name}' enthält weniger als 2 Objekte. Abstandsberechnung nicht möglich."

            # Ein Attribut ermitteln, das den Gebäudenamen enthält
            name_cols = [c for c in ["bezeichnung", "name", "einrichtung", "strasse"] if c in gdf.columns]
            label_col = name_cols[0] if name_cols else None

            pair_results, metric_crs = pairwise_distances(gdf, return_crs=True)
            results = []
            for pair in pair_results:
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
            summary += (f"Es wurden die Abstände zwischen insgesamt **{len(gdf)} Objekten** im "
                        f"lokalen UTM-Koordinatensystem {metric_crs} berechnet.\n\n")
            summary += "| Von | Nach | Abstand (Meter) | Abstand (km) |\n"
            summary += "| :--- | :--- | :---: | :---: |\n"

            for row in results[:max_pairs]:
                dist_km = round(row["distance_m"] / 1000, 2)
                summary += f"| {row['from']} | {row['to']} | **{row['distance_m']} m** | {dist_km} km |\n"

            if len(results) > max_pairs:
                summary += f"\n*(Tabelle auf die {max_pairs} kürzesten Distanzen begrenzt)*\n"

            min_pair = results[0]
            tool_metrics.update(feature_count=len(gdf), pair_count=len(results),
                                min_distance_m=min_pair["distance_m"], distance_crs=metric_crs)
            summary += f"\n- **Nächste Nachbarn:** `{min_pair['from']}` und `{min_pair['to']}` liegen mit nur **{min_pair['distance_m']} m** am dichtesten beieinander."

            return summary

        except Exception as e:
            return f"Fehler bei der Distanzberechnung: {str(e)}"

    @tool
    def aggregate_features_by_area(area_layer_name: str, source_layer_name: str,
                                   operation: Literal["count", "sum"],
                                   value_field: Optional[str] = None) -> str:
        """Zählt Quellobjekte je Polygon oder summiert ein numerisches Quellfeld je Polygon und gibt eine Statistiktabelle zurück. Punkte am Polygonrand gelten nicht als innerhalb; bei anderen Geometrien zählt jede Überschneidung. Bei überlappenden Flächen kann ein Quellobjekt mehrfach eingehen."""
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        try:
            areas = resolve(pool, area_layer_name)
            source = resolve(pool, source_layer_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if areas is None:
            return f"Fehler: Flächen-Layer '{area_layer_name}' wurde nicht gefunden."
        if source is None:
            return f"Fehler: Quell-Layer '{source_layer_name}' wurde nicht gefunden."
        if areas["dataset_id"] == source["dataset_id"]:
            return "Fehler: Bitte unterschiedliche Flächen- und Quelldatensätze angeben."
        try:
            result = gis_statistics.aggregate_by_area(
                input_frame(areas), input_frame(source), operation=operation, value_field=value_field)
            aggregate_field = result.columns[-1]
            preferred = [c for c in ("name", "NAME", "Name", "stadtteil", "Stadtteil",
                                      "bezirk", "Bezirk", "district", "id", "ID") if c in result.columns]
            columns = list(dict.fromkeys(preferred + [c for c in result.columns
                                                       if c not in preferred and c != result.geometry.name]))
            columns = columns[:8]
            if aggregate_field not in columns:
                columns[-1] = aggregate_field
            table = result[columns].copy()
            table.insert(0, "Gebiet", [str(v) for v in range(1, len(table) + 1)])
            # Prefer a meaningful area label where available.
            if preferred:
                table["Gebiet"] = result[preferred[0]].fillna("").astype(str).replace("", "Unbenannt")
                table = table.drop(columns=[preferred[0]])
            table = table.rename(columns={c: str(c) for c in table.columns})
            tool_metrics.update(area_count=len(result), operation=operation,
                                source_feature_count=len(input_frame(source)))
            heading = "Anzahl je Gebiet" if operation == "count" else f"Summe von '{value_field}' je Gebiet"
            result = clean_gdf_for_geojson(result)
            result_layer_name = make_unique_layer_name(
                f"{aggregate_field}_{source['name']}_nach_{areas['name']}", show_layers)
            show_layers.append({
                "name": result_layer_name, "type": "choropleth", "data": result,
                "data_field": aggregate_field, "_format": "collection",
                "crs": areas.get("crs"),
                "folium_kwargs": {"fillColor": "Blues", "color": "black",
                                  "weight": 1, "fillOpacity": 0.7}
            })
            headers = [str(column) for column in table.columns]
            rows = [[str(value).replace("|", "\\|").replace("\n", " ") for value in row]
                    for row in table.itertuples(index=False, name=None)]
            markdown_table = ("| " + " | ".join(headers) + " |\n"
                              + "| " + " | ".join("---" for _ in headers) + " |\n"
                              + "\n".join("| " + " | ".join(row) + " |" for row in rows))
            return (f"Erfolg: **{heading}** für '{source['name']}' nach den Gebieten aus "
                    f"'{areas['name']}' berechnet. Alle {len(result)} Gebiete sind enthalten; "
                    f"Gebiete ohne Treffer haben den Wert 0. Das Ergebnisfeld '{aggregate_field}' "
                    f"steht im neuen Layer '{result_layer_name}'.\n\n" + markdown_table)
        except Exception as exc:
            return f"Fehler bei der Flächenaggregation: {exc}"

    @tool
    def calculate_feature_area_by_area(area_layer_name: str, source_layer_name: str,
                                       output_field: str = "Schnittflaeche_km2") -> str:
        """Schneidet Polygonflächen exakt an Gebietsgrenzen und summiert die Schnittfläche je Gebiet in km². Gibt eine Ergebnistabelle und einen wiederverwendbaren Choropleth-Layer zurück."""
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        try:
            areas = resolve(pool, area_layer_name)
            source = resolve(pool, source_layer_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if areas is None:
            return f"Fehler: Flächen-Layer '{area_layer_name}' wurde nicht gefunden."
        if source is None:
            return f"Fehler: Quell-Layer '{source_layer_name}' wurde nicht gefunden."
        if areas["dataset_id"] == source["dataset_id"]:
            return "Fehler: Bitte unterschiedliche Gebiets- und Quell-Layer angeben."
        try:
            result = gis_statistics.calculate_intersection_area_by_area(
                input_frame(areas), input_frame(source), output_field=output_field)
            result_field = result.columns[-1]
            label_fields = [c for c in ("name", "NAME", "Name", "stadtteil", "Stadtteil",
                                        "bezirk", "Bezirk", "district", "id", "ID")
                            if c in result.columns]
            labels = (result[label_fields[0]].fillna("").astype(str).replace("", "Unbenannt").tolist()
                      if label_fields else [f"Gebiet {i + 1}" for i in range(len(result))])
            values = result[result_field].tolist()
            escaped_labels = [str(label).replace("|", "\\|") for label in labels]
            markdown_table = ("| Gebiet | Fläche (km²) |\n| --- | ---: |\n" +
                              "\n".join(f"| {label} | {value:.6f} |"
                                        for label, value in zip(escaped_labels, values)))
            result = clean_gdf_for_geojson(result)
            result_layer_name = make_unique_layer_name(
                f"{result_field}_{source['name']}_nach_{areas['name']}", show_layers)
            show_layers.append({
                "name": result_layer_name, "type": "choropleth", "data": result,
                "data_field": result_field, "_format": "collection",
                "crs": areas.get("crs"),
                "folium_kwargs": {"fillColor": "Greens", "color": "black",
                                  "weight": 1, "fillOpacity": 0.7}
            })
            tool_metrics.update(area_count=len(result), source_feature_count=len(input_frame(source)),
                                output_field=result_field)
            return (f"Erfolg: Die Flächen von '{source['name']}' wurden an den Gebietsgrenzen aus "
                    f"'{areas['name']}' geschnitten und als km² je Gebiet summiert. Ergebnislayer "
                    f"'{result_layer_name}' mit Feld '{result_field}'.\n\n" + markdown_table)
        except Exception as exc:
            return f"Fehler bei der flächenbezogenen Aggregation: {exc}"

    @tool
    def rank_dataset_by_field(dataset_name: str, field_name: str, limit: int = 5,
                              order: Literal["descending", "ascending"] = "descending") -> str:
        """Sortiert einen Datensatz nach einem numerischen Attribut und gibt die Top-N-Werte als Berichtstabelle aus. Erstellt keinen neuen Layer. Gleichstände behalten die ursprüngliche Datensatzreihenfolge; fehlende/nichtnumerische Werte werden ausgelassen."""
        raw_layers, show_layers = get_agent_layers()
        try:
            target = resolve(show_layers + raw_layers, dataset_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if target is None:
            return f"Fehler: Datensatz '{dataset_name}' wurde nicht gefunden."
        try:
            n = int(limit)
            if n < 1 or n > 100:
                return "Fehler: Die Anzahl N muss zwischen 1 und 100 liegen."
            if order not in {"ascending", "descending"}:
                return "Fehler: order muss 'ascending' oder 'descending' sein."
            frame = input_frame(target).reset_index(drop=True)
            if field_name not in frame.columns or field_name == frame.geometry.name:
                return f"Fehler: Attribut '{field_name}' ist in '{target['name']}' nicht vorhanden."
            numeric = pd.to_numeric(frame[field_name], errors="coerce")
            valid = numeric.notna()
            if not valid.any():
                return f"Fehler: Attribut '{field_name}' enthält keine gültigen numerischen Werte."
            ascending = order == "ascending"
            ranked_indices = numeric.loc[valid].sort_values(ascending=ascending, kind="stable").index[:n]
            label_candidates = ["name", "NAME", "Name", "stadtteil", "Stadtteil", "bezirk",
                                "Bezirk", "district", "District", "id", "ID"]
            label_field = next((field for field in label_candidates
                                if field in frame.columns and field != field_name), None)
            rows = []
            for rank, index in enumerate(ranked_indices, start=1):
                label = str(frame.loc[index, label_field]) if label_field else f"Objekt {index + 1}"
                label = label.replace("|", "\\|").replace("\n", " ")
                value = float(numeric.loc[index])
                rows.append((rank, label, value))
            direction = "absteigend" if not ascending else "aufsteigend"
            tool_metrics.update(result_count=len(rows), missing_value_count=int((~valid).sum()),
                                ranked_field=field_name, ranking_order=order, limit=n)
            table = ("| Rang | Gebiet/Objekt | " + field_name.replace("|", "\\|") + " |\n"
                     "| ---: | --- | ---: |\n" +
                     "\n".join(f"| {rank} | {label} | {value:.6g} |" for rank, label, value in rows))
            message = (f"Erfolg: Top {len(rows)} in '{target['name']}' nach '{field_name}' "
                       f"({direction}) sortiert.\n\n" + table)
            missing = int((~valid).sum())
            if missing:
                message += f"\n\n{missing} Datensätze ohne gültigen Zahlenwert wurden ausgelassen."
            return message
        except Exception as exc:
            return f"Fehler beim Sortieren nach '{field_name}': {exc}"

    @tool
    def query_spatial_relationship(source_layer_name: str, target_layer_name: str,
                                   relationship: Literal["point_in_polygon", "intersects"] = "intersects") -> str:
        """Prüft räumliche Beziehungen zwischen zwei Layern und erzeugt einen Ergebnislayer. point_in_polygon ordnet Quellpunkte Zielpolygonen zu und behält nicht zugeordnete Punkte mit leeren Zielattributen; intersects liefert Quellobjekte mit Schnitt mit mindestens einem Zielobjekt."""
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        try:
            source = resolve(pool, source_layer_name)
            target = resolve(pool, target_layer_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if source is None:
            return f"Fehler: Quell-Layer '{source_layer_name}' wurde nicht gefunden."
        if target is None:
            return f"Fehler: Ziel-Layer '{target_layer_name}' wurde nicht gefunden."
        if source["dataset_id"] == target["dataset_id"]:
            return "Fehler: Bitte zwei unterschiedliche Quell- und Ziel-Datensätze angeben."

        try:
            result, metrics = spatial_relationship_join(
                input_frame(source), input_frame(target), relationship=relationship)
            layer_name = make_unique_layer_name(f"{relationship}_{source['name']}", show_layers)
            tool_metrics.update(metrics)
            tool_metrics["relationship"] = relationship
            show_layers.append({
                "name": layer_name,
                "type": source.get("type", "geojson"),
                "data": clean_gdf_for_geojson(result),
                "_format": "collection",
                "crs": source.get("crs"),
                "folium_kwargs": {"color": "red", "weight": 2, "fillColor": "orange", "fillOpacity": 0.5}
            })
            return (f"Erfolg: Räumliche Beziehung '{relationship}' zwischen '{source['name']}' und "
                    f"'{target['name']}' geprüft. {metrics['matched_source_features']} von "
                    f"{metrics['source_features']} Quellobjekten haben mindestens einen Treffer; "
                    f"{metrics['unmatched_source_features']} ohne Treffer, "
                    f"{metrics['multiple_match_source_features']} mit mehreren Treffern. "
                    f"Der Ergebnislayer '{layer_name}' enthält {len(result)} Datensätze; "
                    "nicht zugeordnete Quellobjekte sind bei point_in_polygon enthalten und ihre related_-Attribute bleiben leer.")
        except Exception as exc:
            return f"Fehler bei der räumlichen Beziehungsabfrage: {exc}"

    @tool
    def run_spatial_overlay(layer_a_name: str, layer_b_name: str,
                            operation: Literal["intersection", "difference", "union", "symmetric_difference"]) -> str:
        """Führt eine Polygon-Overlay-Operation aus und erzeugt einen Ergebnislayer. Unterstützt intersection, difference (A minus B), union und symmetric_difference. Layer müssen Polygon- oder MultiPolygon-Geometrien enthalten."""
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        try:
            layer_a = resolve(pool, layer_a_name)
            layer_b = resolve(pool, layer_b_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if layer_a is None:
            return f"Fehler: Layer A '{layer_a_name}' wurde nicht gefunden."
        if layer_b is None:
            return f"Fehler: Layer B '{layer_b_name}' wurde nicht gefunden."
        if layer_a["dataset_id"] == layer_b["dataset_id"]:
            return "Fehler: Bitte zwei unterschiedliche Polygon-Datensätze angeben."

        try:
            result, metrics = overlay_polygon_layers(
                input_frame(layer_a), input_frame(layer_b), operation=operation)
            output_name = make_unique_layer_name(
                f"{operation}_{layer_a['name']}_{layer_b['name']}", show_layers)
            tool_metrics.update(metrics)
            show_layers.append({
                "name": output_name, "type": "geojson",
                "data": clean_gdf_for_geojson(result), "_format": "collection",
                "crs": layer_a.get("crs"),
                "folium_kwargs": {"color": "darkgreen", "weight": 2,
                                  "fillColor": "lightgreen", "fillOpacity": 0.5}
            })
            area = metrics["total_area_m2"]
            return (f"Erfolg: Overlay '{operation}' für '{layer_a['name']}' und '{layer_b['name']}' "
                    f"im lokalen UTM-Koordinatensystem {metrics['metric_crs']} abgeschlossen. "
                    f"Ergebnis: {metrics['result_features']} Flächen, Gesamtfläche ca. {area:,.1f} m². "
                    f"Der Ergebnislayer heißt '{output_name}'."
                    + (" Beim Unterschied gilt die Richtung Layer A minus Layer B." if operation == "difference" else ""))
        except Exception as exc:
            return f"Fehler bei der Overlay-Analyse: {exc}"

    @tool
    def identify_spatial_hotspots(dataset_name: str, cell_size_meters: float = 500.0,
                                  hotspot_percentile: float = 90.0) -> str:
        """Identifiziert hohe Ereignisdichten in einem Punktlayer durch feste Rasterzellen und erzeugt einen Choropleth-Ergebnislayer. Hotspots sind Zellen ab dem angegebenen Perzentil der belegten Zellen, kein statistischer Signifikanztest."""
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        try:
            target = resolve(pool, dataset_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if target is None:
            return f"Fehler: Datensatz '{dataset_name}' wurde nicht gefunden."

        try:
            grid, metrics = gis_statistics.identify_point_density_hotspots(
                input_frame(target), cell_size_meters=cell_size_meters,
                hotspot_percentile=hotspot_percentile)
            layer_name = make_unique_layer_name(f"Hotspots_{target['name']}", show_layers)
            has_density_variation = grid["accident_count"].nunique(dropna=True) > 1
            tool_metrics.update(metrics)
            show_layers.append({
                "name": layer_name,
                "type": "choropleth" if has_density_variation else "geojson",
                "data": clean_gdf_for_geojson(grid),
                "data_field": "accident_count" if has_density_variation else None,
                "_format": "collection", "crs": target.get("crs"),
                "folium_kwargs": {"fillColor": "YlOrRd" if has_density_variation else "red",
                                  "color": "black", "weight": 1, "fillOpacity": 0.7}
            })
            threshold = metrics["hotspot_threshold_count"]
            return (f"Erfolg: {metrics['input_point_count']} Ereignispunkte wurden in "
                    f"{metrics['occupied_cell_count']} belegte Rasterzellen der Größe "
                    f"{metrics['cell_size_meters']:g} m eingeteilt. Ab {threshold:g} Ereignissen pro Zelle "
                    f"(Perzentil {metrics['hotspot_percentile']:g}) gelten Zellen als Hotspots; "
                    f"gefunden: {metrics['hotspot_cell_count']}. Die Karte zeigt Ereigniszahlen je Zelle "
                    f"im Ergebnislayer '{layer_name}'. Verwendetes CRS für die Berechnung: {metrics['metric_crs']}. "
                    + ("Alle Zellen haben dieselbe Ereigniszahl und werden deshalb einheitlich eingefärbt. "
                       if not has_density_variation else "")
                    + "Die Einstufung ist eine Dichte-Regel, kein statistischer Signifikanztest.")
        except Exception as exc:
            return f"Fehler bei der Dichte-/Hotspotanalyse: {exc}"

    @tool
    def export_statistical_table(dataset_name: str, fields: Optional[List[str]] = None,
                                 file_name: Optional[str] = None) -> str:
        """Export attribute columns of a dataset or statistical result layer as a downloadable UTF-8 CSV attachment. Geometry is omitted; if fields is not provided, every non-geometry column is exported."""
        raw_layers, show_layers = get_agent_layers()
        try:
            target = resolve(show_layers + raw_layers, dataset_name)
        except AmbiguousReference as exc:
            return f"Fehler: {exc}"
        if target is None:
            return f"Fehler: Datensatz oder Ergebnisebene '{dataset_name}' wurde nicht gefunden."
        try:
            frame = input_frame(target)
            available_fields = [column for column in frame.columns if column != frame.geometry.name]
            if fields is None:
                selected_fields = available_fields
            else:
                if not fields:
                    return "Fehler: Bitte mindestens ein Attributfeld zum Exportieren auswählen."
                missing_fields = [field for field in fields if field not in available_fields]
                if missing_fields:
                    return ("Fehler: Diese Felder sind nicht exportierbar oder nicht vorhanden: "
                            + ", ".join(missing_fields) + ".")
                selected_fields = list(dict.fromkeys(fields))
            if not selected_fields:
                return f"Fehler: '{target['name']}' enthält keine exportierbaren Attributfelder."
            table = frame[selected_fields].copy()
            csv_bytes = table.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")
            base_name = file_name or f"{target['name']}_statistik.csv"
            base_name = re.sub(r"[\\/:*?\"<>|]+", "_", base_name.strip()).strip(" .")
            if not base_name:
                base_name = "statistik.csv"
            if not base_name.casefold().endswith(".csv"):
                base_name += ".csv"
            artifact = {"data": csv_bytes, "file_name": base_name, "mime_type": "text/csv; charset=utf-8"}
            pending_export["artifact"] = artifact
            tool_metrics.update(row_count=len(table), column_count=len(selected_fields),
                                exported_fields=[str(field) for field in selected_fields])
            return (f"Erfolg: '{target['name']}' wurde als CSV mit {len(table)} Datensätzen und "
                    f"{len(selected_fields)} Spalten exportiert. Geometrien sind nicht enthalten; "
                    f"die Datei '{base_name}' steht als Download-Anhang bereit.")
        except Exception as exc:
            return f"Fehler beim CSV-Export: {exc}"

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
        check_dataset_crs_consistency,
        transform_dataset_crs,
        check_uploaded_data_quality,
        associate_dataset_attributes,
        filter_dataset_by_attribute,
        aggregate_features_by_area,
        calculate_feature_area_by_area,
        rank_dataset_by_field,
        query_spatial_relationship,
        run_spatial_overlay,
        identify_spatial_hotspots,
        fetch_city_boundary_osm,
        visualize_layer_on_map,
        calculate_buffer_analysis,
        calculate_area_and_density,
        calculate_derived_indicator,
        run_machine_learning_spatial_prediction,
        update_map_style,
        calculate_feature_distances,
        manage_map_layer,
        export_statistical_table,
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
                pending_attribute_relation.clear()
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
                    for key in ("distance_meters", "max_pairs", "distance_threshold_meters"):
                        if key in arguments.arguments:
                            metrics[key] = arguments.arguments[key]
                    map_action = pending_map_action.get("action")
                    export_artifact = pending_export.get("artifact")
                    attribute_relation = pending_attribute_relation.get("relation")
                    if export_artifact:
                        result = session.commit_export_artifact(export_artifact, message=message,
                                                                tool_name=legacy.name)
                    elif attribute_relation:
                        result = session.commit_attribute_relation(attribute_relation, message=message,
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
                    pending_attribute_relation.clear()
        return StructuredTool.from_function(execute, name=legacy.name, description=legacy.description,
                                            args_schema=legacy.args_schema)

    return [bind_atomic(registered) for registered in TOOLS]
