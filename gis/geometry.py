"""Geometry computations on explicitly georeferenced frames, without sessions.

Distance queries and buffers use a local UTM projection for geographically
compact input datasets. All-pairs calculations remain quadratic in input size.
"""
from itertools import combinations
import math

import geopandas as gpd
import pandas as pd

from .data_io import transform_geodata


def buffer_geometries(gdf: gpd.GeoDataFrame, distance_meters: float = 500.0, *,
                      return_crs: bool = False):
    """Return buffered features in EPSG:4326 without modifying the input.

    A local UTM CRS is estimated from the data extent so the buffer distance is
    expressed in metres with low local projection distortion. This is intended
    for geographically compact datasets, not regions spanning multiple UTM zones.
    """
    try:
        distance = float(distance_meters)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Pufferdistanz muss eine positive Zahl in Metern sein.") from exc
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("Pufferdistanz muss eine positive, endliche Zahl in Metern sein.")
    if gdf.empty:
        raise ValueError("Der Eingabelayer enthält keine Geometrien.")
    if gdf.crs is None:
        raise ValueError("Quell-Koordinatenreferenzsystem unbekannt; Puffer kann nicht berechnet werden.")

    geometry = gdf.geometry
    missing = geometry.isna() | geometry.is_empty
    if missing.any():
        raise ValueError(f"Der Eingabelayer enthält {int(missing.sum())} leere oder fehlende Geometrien.")
    invalid = ~geometry.is_valid
    if invalid.any():
        raise ValueError(f"Der Eingabelayer enthält {int(invalid.sum())} ungültige Geometrien.")

    geographic = transform_geodata(gdf, 4326)
    metric_crs = geographic.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Für die Lage des Eingabelayers konnte kein lokales UTM-Koordinatensystem bestimmt werden.")
    projected = transform_geodata(gdf, metric_crs)
    projected["geometry"] = projected.buffer(distance)
    result = transform_geodata(projected, 4326)
    return (result, metric_crs.to_string()) if return_crs else result


def pairwise_distances(gdf: gpd.GeoDataFrame, *, return_crs: bool = False):
    """Return all feature-pair minimum distances in a local UTM CRS.

    Input requires a CRS. from_index/to_index are zero-based row POSITIONS,
    not index labels or application IDs. Retain rounding to 0.1m and stable
    sorting. No max_pairs truncation or UI formatting occurs.
    Input is not modified. Time and result storage remain quadratic.
    """
    if gdf.empty:
        raise ValueError("Der Eingabelayer enthält keine Geometrien.")
    missing = gdf.geometry.isna() | gdf.geometry.is_empty
    if missing.any():
        raise ValueError(f"Der Eingabelayer enthält {int(missing.sum())} leere oder fehlende Geometrien.")
    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        raise ValueError(f"Der Eingabelayer enthält {int(invalid.sum())} ungültige Geometrien.")
    geographic = transform_geodata(gdf, 4326)
    metric_crs = geographic.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Für den Eingabelayer konnte kein lokales UTM-Koordinatensystem bestimmt werden.")
    projected = transform_geodata(gdf, metric_crs)
    results = []
    for idx1, idx2 in combinations(range(len(projected)), 2):
        distance = projected.iloc[idx1].geometry.distance(projected.iloc[idx2].geometry)
        results.append({
            "from_index": idx1,
            "to_index": idx2,
            "distance_m": round(distance, 1)
        })
    results.sort(key=lambda row: row["distance_m"])
    return (results, metric_crs.to_string()) if return_crs else results


def cross_layer_distances(source: gpd.GeoDataFrame, target: gpd.GeoDataFrame, *,
                          query_mode: str = "nearest", distance_meters: float | None = None,
                          return_crs: bool = False):
    """Query cross-layer distances using a shared local UTM CRS and target index.

    ``nearest`` returns one nearest target feature per source feature. With a
    distance limit, ``within_distance`` returns every pair within that limit.
    The operation supports mixed point, line and polygon geometries.
    """
    if query_mode not in {"nearest", "within_distance"}:
        raise ValueError("query_mode muss 'nearest' oder 'within_distance' sein.")
    if source.empty or target.empty:
        raise ValueError("Beide Eingabelayer müssen mindestens ein Objekt enthalten.")
    if source.crs is None or target.crs is None:
        raise ValueError("Beide Eingabelayer benötigen ein bekanntes Koordinatenreferenzsystem.")
    if distance_meters is not None:
        try:
            distance_meters = float(distance_meters)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Die Suchdistanz muss eine endliche Zahl in Metern sein.") from exc
        if not math.isfinite(distance_meters) or distance_meters < 0:
            raise ValueError("Die Suchdistanz muss eine nicht-negative, endliche Zahl in Metern sein.")
    if query_mode == "within_distance" and distance_meters is None:
        raise ValueError("Für die Umkreissuche ist eine Distanz in Metern erforderlich.")

    for frame in (source, target):
        geometry = frame.geometry
        missing = geometry.isna() | geometry.is_empty
        if missing.any():
            raise ValueError(f"Eingabelayer enthält {int(missing.sum())} leere oder fehlende Geometrien.")
        invalid = ~geometry.is_valid
        if invalid.any():
            raise ValueError(f"Eingabelayer enthält {int(invalid.sum())} ungültige Geometrien.")

    source_wgs = transform_geodata(source, 4326)
    target_wgs = transform_geodata(target, 4326)
    combined = gpd.GeoSeries(list(source_wgs.geometry) + list(target_wgs.geometry), crs=4326)
    metric_crs = combined.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Für die Eingabelayer konnte kein lokales UTM-Koordinatensystem bestimmt werden.")
    source_metric = transform_geodata(source, metric_crs)
    target_metric = transform_geodata(target, metric_crs)

    if query_mode == "nearest":
        positions, distances = target_metric.sindex.nearest(
            source_metric.geometry, return_all=False, return_distance=True)
    else:
        positions = target_metric.sindex.query(
            source_metric.geometry, predicate="dwithin", distance=distance_meters,
            sort=True, output_format="indices")
        distances = [source_metric.geometry.iloc[i].distance(target_metric.geometry.iloc[j])
                     for i, j in zip(positions[0], positions[1])]

    results = [{"from_index": int(i), "to_index": int(j), "distance_m": float(distance)}
               for i, j, distance in zip(positions[0], positions[1], distances)]
    results.sort(key=lambda row: (row["distance_m"], row["from_index"], row["to_index"]))
    return (results, metric_crs.to_string()) if return_crs else results


def spatial_relationship_join(source: gpd.GeoDataFrame, target: gpd.GeoDataFrame, *,
                              relationship: str = "intersects"):
    """Return source features paired with spatially related target attributes.

    ``point_in_polygon`` uses ``covered_by`` so points on polygon boundaries
    are included and can be reported as multiple matches. ``intersects`` keeps
    only source features with at least one target match. The source geometry
    and CRS are retained; target attributes receive a ``related_`` prefix.
    """
    predicates = {"point_in_polygon": "covered_by", "intersects": "intersects"}
    if relationship not in predicates:
        raise ValueError("Unterstützte Beziehungen sind point_in_polygon und intersects.")
    if source.empty or target.empty:
        raise ValueError("Beide Eingabelayer müssen mindestens ein Objekt enthalten.")
    if source.crs is None or target.crs is None:
        raise ValueError("Beide Eingabelayer benötigen ein bekanntes Koordinatenreferenzsystem.")

    source_types = set(source.geometry.geom_type.dropna())
    target_types = set(target.geometry.geom_type.dropna())
    polygon_types = {"Polygon", "MultiPolygon"}
    point_types = {"Point", "MultiPoint"}
    if relationship == "point_in_polygon" and (not source_types <= point_types or not target_types <= polygon_types):
        raise ValueError("point_in_polygon benötigt Punkte im Quell-Layer und Polygone im Ziel-Layer.")

    for frame in (source, target):
        geometry = frame.geometry
        missing = geometry.isna() | geometry.is_empty
        if missing.any():
            raise ValueError(f"Eingabelayer enthält {int(missing.sum())} leere oder fehlende Geometrien.")
        invalid = ~geometry.is_valid
        if invalid.any():
            raise ValueError(f"Eingabelayer enthält {int(invalid.sum())} ungültige Geometrien.")

    source_wgs, target_wgs = transform_geodata(source, 4326), transform_geodata(target, 4326)
    combined = gpd.GeoSeries(list(source_wgs.geometry) + list(target_wgs.geometry), crs=4326)
    metric_crs = combined.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Für die Eingabelayer konnte kein lokales UTM-Koordinatensystem bestimmt werden.")
    source_metric, target_metric = transform_geodata(source, metric_crs), transform_geodata(target, metric_crs)
    pairs = target_metric.sindex.query(
        source_metric.geometry, predicate=predicates[relationship], sort=True, output_format="indices")

    matches = [[] for _ in range(len(source))]
    for source_index, target_index in zip(pairs[0], pairs[1]):
        matches[int(source_index)].append(int(target_index))

    source_geometry_name = source.geometry.name
    source_columns = [column for column in source.columns if column != source_geometry_name]
    target_columns = [column for column in target.columns if column != target.geometry.name]
    used_names = set(source_columns) | {source_geometry_name}
    related_names = {}
    for column in target_columns:
        name = f"related_{column}"
        base, suffix = name, 2
        while name in used_names:
            name = f"{base}_{suffix}"
            suffix += 1
        related_names[column] = name
        used_names.add(name)
    status_column = "spatial_match_status"
    while status_column in used_names:
        status_column = f"_{status_column}"

    rows, geometries = [], []
    unmatched_count = multiple_count = matched_source_count = 0
    for source_index, target_indices in enumerate(matches):
        if not target_indices:
            unmatched_count += 1
            if relationship == "point_in_polygon":
                record = source.iloc[source_index][source_columns].to_dict()
                record.update({name: None for name in related_names.values()})
                record[status_column] = "unmatched"
                rows.append(record)
                geometries.append(source.geometry.iloc[source_index])
            continue
        matched_source_count += 1
        status = "multiple" if len(target_indices) > 1 else "matched"
        if status == "multiple":
            multiple_count += 1
        for target_index in target_indices:
            record = source.iloc[source_index][source_columns].to_dict()
            record.update({related_names[column]: target.iloc[target_index][column]
                           for column in target_columns})
            record[status_column] = status
            rows.append(record)
            geometries.append(source.geometry.iloc[source_index])

    output_columns = source_columns + list(related_names.values()) + [status_column]
    output = gpd.GeoDataFrame(pd.DataFrame(rows, columns=output_columns),
                              geometry=gpd.GeoSeries(geometries, name=source_geometry_name, crs=source.crs),
                              crs=source.crs)
    metrics = {
        "source_features": len(source), "target_features": len(target),
        "matched_source_features": matched_source_count, "unmatched_source_features": unmatched_count,
        "multiple_match_source_features": multiple_count, "relationship_pairs": int(pairs.shape[1]),
        "predicate_crs": metric_crs.to_string(), "status_field": status_column,
        "related_field_mapping": related_names,
    }
    return output, metrics


def overlay_polygon_layers(layer_a: gpd.GeoDataFrame, layer_b: gpd.GeoDataFrame, *,
                           operation: str = "intersection"):
    """Run a polygon overlay and return output in layer A's source CRS.

    GeoPandas performs the overlay geometry operation and uses spatial indexing
    internally. Area is measured before reprojecting the result back to source CRS.
    """
    supported = {"intersection", "difference", "union", "symmetric_difference"}
    if operation not in supported:
        raise ValueError(f"operation muss eines von {', '.join(sorted(supported))} sein.")
    if layer_a.empty or layer_b.empty:
        raise ValueError("Beide Eingabelayer müssen mindestens ein Polygon enthalten.")
    if layer_a.crs is None or layer_b.crs is None:
        raise ValueError("Beide Eingabelayer benötigen ein bekanntes Koordinatenreferenzsystem.")
    polygon_types = {"Polygon", "MultiPolygon"}
    for frame in (layer_a, layer_b):
        types = set(frame.geometry.geom_type.dropna())
        if not types <= polygon_types:
            raise ValueError("Die erste Version der Overlay-Analyse unterstützt nur Polygon- und MultiPolygon-Layer.")
        geometry = frame.geometry
        missing = geometry.isna() | geometry.is_empty
        if missing.any():
            raise ValueError(f"Eingabelayer enthält {int(missing.sum())} leere oder fehlende Geometrien.")
        invalid = ~geometry.is_valid
        if invalid.any():
            raise ValueError(f"Eingabelayer enthält {int(invalid.sum())} ungültige Geometrien.")

    a_wgs, b_wgs = transform_geodata(layer_a, 4326), transform_geodata(layer_b, 4326)
    combined = gpd.GeoSeries(list(a_wgs.geometry) + list(b_wgs.geometry), crs=4326)
    metric_crs = combined.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Für die Eingabelayer konnte kein lokales UTM-Koordinatensystem bestimmt werden.")
    a_metric, b_metric = transform_geodata(layer_a, metric_crs), transform_geodata(layer_b, metric_crs)
    result_metric = gpd.overlay(a_metric, b_metric, how=operation, keep_geom_type=True, make_valid=False)
    total_area = float(result_metric.geometry.area.sum()) if len(result_metric) else 0.0
    result = transform_geodata(result_metric, layer_a.crs)
    metrics = {
        "operation": operation, "layer_a_features": len(layer_a), "layer_b_features": len(layer_b),
        "result_features": len(result), "total_area_m2": total_area, "metric_crs": metric_crs.to_string(),
    }
    return result, metrics
