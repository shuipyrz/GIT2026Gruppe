"""Area, point count and density, retaining existing numeric semantics.

All inputs require a CRS. Functions return new frames/series and never submit
application state. Fixed projections, early rounding, invalid-input handling
and within boundary/overlap rules are deliberately unchanged in Phase 3A.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from .data_io import transform_geodata


def calculate_derived_indicator(frame: gpd.GeoDataFrame, *, numerator_field: str,
                                denominator_field: str, output_field: str,
                                scale_factor: float = 1.0, decimals: int = 2) -> gpd.GeoDataFrame:
    """Calculate numerator / denominator * scale_factor without changing input."""
    for field in (numerator_field, denominator_field):
        if field not in frame.columns:
            raise ValueError(f"Das Feld '{field}' ist im Datensatz nicht vorhanden.")
    if not output_field or output_field.strip() != output_field:
        raise ValueError("Der Name des Ergebnisfelds darf nicht leer sein und keine Rand-Leerzeichen enthalten.")
    if output_field == frame.geometry.name:
        raise ValueError("Das Ergebnisfeld darf nicht das Geometriefeld überschreiben.")
    try:
        factor = float(scale_factor)
        digits = int(decimals)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Skalierungsfaktor und Nachkommastellen müssen gültige Zahlen sein.") from exc
    if not np.isfinite(factor):
        raise ValueError("Der Skalierungsfaktor muss endlich sein.")
    if digits < 0 or digits > 12:
        raise ValueError("Die Nachkommastellen müssen zwischen 0 und 12 liegen.")
    numerator = pd.to_numeric(frame[numerator_field], errors="coerce")
    denominator = pd.to_numeric(frame[denominator_field], errors="coerce")
    if not numerator.notna().any() or not denominator.notna().any():
        raise ValueError("Zähler- und Nennerfeld müssen numerische Werte enthalten.")
    valid = numerator.notna() & denominator.notna() & denominator.ne(0)
    result = frame.copy()
    values = pd.Series(np.nan, index=frame.index, dtype="float64")
    values.loc[valid] = (numerator.loc[valid] / denominator.loc[valid] * factor).round(digits)
    result[output_field] = values
    return result


def calculate_intersection_area_by_area(areas: gpd.GeoDataFrame, features: gpd.GeoDataFrame, *,
                                        output_field: str = "Schnittflaeche_km2") -> gpd.GeoDataFrame:
    """Clip polygon features to each area and sum the exact intersection area in km²."""
    if areas.crs is None or features.crs is None:
        raise ValueError("Beide Datensätze benötigen ein bekanntes Koordinatenreferenzsystem.")
    if areas.empty:
        raise ValueError("Der Gebiets-Layer enthält keine Datensätze.")
    polygon_types = {"Polygon", "MultiPolygon"}
    for label, frame in (("Gebiets-Layer", areas), ("Quell-Layer", features)):
        if label == "Quell-Layer" and frame.empty:
            continue
        types = set(frame.geometry.geom_type.dropna())
        if not types or not types <= polygon_types:
            raise ValueError(f"Der {label} muss Polygon- oder MultiPolygon-Geometrien enthalten.")
        if frame.geometry.isna().any() or frame.geometry.is_empty.any():
            raise ValueError(f"Der {label} enthält leere oder fehlende Geometrien.")
        if (~frame.geometry.is_valid).any():
            raise ValueError(f"Der {label} enthält ungültige Geometrien.")
    if not output_field or output_field == areas.geometry.name:
        raise ValueError("Der Name des Ergebnisfelds ist ungültig.")

    combined = gpd.GeoSeries(list(areas.geometry) + list(features.geometry), crs=areas.crs)
    metric_crs = combined.to_crs(4326).estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Für die Lage der Flächen konnte kein lokales UTM-Koordinatensystem bestimmt werden.")
    projected_areas = transform_geodata(areas, metric_crs)[[areas.geometry.name]].copy()
    projected_features = transform_geodata(features, metric_crs)[[features.geometry.name]].copy()
    area_key = "__area_index__"
    projected_areas[area_key] = range(len(projected_areas))
    if projected_features.empty:
        grouped = pd.Series(dtype="float64")
    else:
        intersections = gpd.overlay(projected_areas, projected_features, how="intersection",
                                    keep_geom_type=True, make_valid=False)
        intersections["__intersection_km2__"] = intersections.geometry.area / 1_000_000
        grouped = intersections.groupby(area_key)["__intersection_km2__"].sum()

    result = areas.copy().reset_index(drop=True)
    base_field, suffix = output_field, 2
    while output_field in result.columns:
        output_field = f"{base_field}_{suffix}"
        suffix += 1
    result[output_field] = [round(float(grouped.get(i, 0.0)), 6) for i in range(len(result))]
    result.attrs["calculation_crs"] = metric_crs.to_string()
    return result


def aggregate_by_area(polygons: gpd.GeoDataFrame, features: gpd.GeoDataFrame, *,
                      operation: str = "count", value_field: str | None = None) -> gpd.GeoDataFrame:
    """Count or sum features assigned to each polygon, preserving polygon order.

    Point features use strict ``within`` semantics, matching
    :func:`count_points_in_polygons`; non-point features use ``intersects``.
    A feature intersecting multiple areas contributes to each area.
    """
    if polygons.crs is None or features.crs is None:
        raise ValueError("Beide Datensätze benötigen ein bekanntes Koordinatenreferenzsystem.")
    if not len(polygons):
        raise ValueError("Der Flächenlayer enthält keine Datensätze.")
    if polygons.geometry.isna().any() or polygons.geometry.is_empty.any():
        raise ValueError("Der Flächenlayer enthält leere oder fehlende Geometrien.")
    if not set(polygons.geometry.geom_type.dropna()) <= {"Polygon", "MultiPolygon"}:
        raise ValueError("Der Flächenlayer muss Polygon- oder MultiPolygon-Geometrien enthalten.")
    if operation not in {"count", "sum"}:
        raise ValueError("Die Operation muss 'count' oder 'sum' sein.")
    if operation == "sum":
        if not value_field or value_field not in features.columns:
            raise ValueError(f"Das Summenfeld '{value_field}' ist im Quelldatensatz nicht vorhanden.")
        values = pd.to_numeric(features[value_field], errors="coerce")
        if values.notna().sum() == 0 and len(features):
            raise ValueError(f"Das Feld '{value_field}' enthält keine numerischen Werte.")
    else:
        values = None

    area_key = "__area_aggregate_index__"
    while area_key in polygons.columns or area_key in features.columns:
        area_key += "_"
    areas = polygons[[polygons.geometry.name]].copy()
    areas[area_key] = range(len(areas))
    source = features[[features.geometry.name]].copy()
    source = transform_geodata(source, polygons.crs)
    if operation == "sum":
        source["__area_aggregate_value__"] = values.to_numpy()
    if features.empty:
        grouped = pd.Series(dtype="float64")
    else:
        predicate = "within" if set(features.geometry.geom_type.dropna()) <= {"Point"} else "intersects"
        joined = gpd.sjoin(source, areas, how="inner", predicate=predicate)
        if operation == "count":
            grouped = joined.groupby(area_key).size()
        else:
            grouped = joined.groupby(area_key)["__area_aggregate_value__"].sum(min_count=1)

    result = polygons.copy().reset_index(drop=True)
    output_field = "Anzahl" if operation == "count" else f"Summe_{value_field}"
    base_field = output_field
    suffix = 2
    while output_field in result.columns:
        output_field = f"{base_field}_{suffix}"
        suffix += 1
    result[output_field] = [
        int(grouped.get(i, 0)) if operation == "count" else grouped.get(i, 0)
        for i in range(len(polygons))
    ]
    return result


def calculate_area(polygons: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with area_km2, using EPSG:25832 and four decimal places.

    Geometry and source CRS are retained. Extremely small areas may round to
    zero; rejecting invalid geometries and improving precision are later work.
    """
    result = polygons.copy()
    projected = transform_geodata(polygons, 25832)
    result["area_km2"] = (projected.geometry.area / 1_000_000).round(4)
    return result


def count_points_in_polygons(polygons: gpd.GeoDataFrame, points: gpd.GeoDataFrame) -> pd.Series:
    """Return integer counts indexed like polygons, without modifying inputs.

    Join uses EPSG:3857 and strict within: boundary points excluded; a point in
    overlapping polygons counts in each. Retain the legacy index_right logic.
    """
    projected_polygons = transform_geodata(polygons, 3857)
    projected_points = transform_geodata(points, 3857)
    joined = gpd.sjoin(projected_points, projected_polygons, predicate="within", how="inner")
    counts = joined.groupby("index_right").size()
    values = polygons.index.map(counts).fillna(0).astype(int)
    return pd.Series(values, index=polygons.index, name="objekt_anzahl")


def calculate_density(polygons_with_area: gpd.GeoDataFrame, points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with objekt_anzahl and dichte_pro_km2 (points per km²).

    Requires area_km2, normally supplied by calculate_area. Intentionally uses
    this already-rounded area, rounds density to two places and fills undefined
    zero-area density with 0, preserving the known precision defect.
    """
    result = polygons_with_area.copy()
    result["objekt_anzahl"] = count_points_in_polygons(result, points)
    result["dichte_pro_km2"] = (
        result["objekt_anzahl"] / result["area_km2"].replace(0, np.nan)
    ).round(2).fillna(0)
    return result


def identify_point_density_hotspots(points: gpd.GeoDataFrame, *, cell_size_meters: float = 500.0,
                                    hotspot_percentile: float = 90.0):
    """Count point features in a metric grid and mark high-count occupied cells.

    Grid assignment uses integer cell coordinates, so each point is assigned
    once in O(n) without a point-to-every-cell spatial join. The percentile is
    computed over occupied cells; ties may produce more than the nominal top
    percentile share.
    """
    try:
        cell_size = float(cell_size_meters)
        percentile = float(hotspot_percentile)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Rasterzellengröße und Hotspot-Perzentil müssen Zahlen sein.") from exc
    if not np.isfinite(cell_size) or cell_size <= 0:
        raise ValueError("Die Rasterzellengröße muss eine positive, endliche Distanz in Metern sein.")
    if not np.isfinite(percentile) or not 0 < percentile <= 100:
        raise ValueError("Das Hotspot-Perzentil muss größer als 0 und höchstens 100 sein.")
    if points.empty:
        raise ValueError("Der Eingabelayer enthält keine Ereignispunkte.")
    if points.crs is None:
        raise ValueError("Der Ereignislayer benötigt ein bekanntes Koordinatenreferenzsystem.")
    types = set(points.geometry.geom_type.dropna())
    if not types <= {"Point"}:
        raise ValueError("Die Dichteanalyse erwartet einen Punktlayer.")
    missing = points.geometry.isna() | points.geometry.is_empty
    if missing.any():
        raise ValueError(f"Der Eingabelayer enthält {int(missing.sum())} leere oder fehlende Punkte.")

    geographic = transform_geodata(points, 4326)
    metric_crs = geographic.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Für die Lage der Ereignispunkte konnte kein lokales UTM-Koordinatensystem bestimmt werden.")
    projected = transform_geodata(points, metric_crs)
    x = projected.geometry.x.to_numpy()
    y = projected.geometry.y.to_numpy()
    cell_x = np.floor(x / cell_size).astype(np.int64)
    cell_y = np.floor(y / cell_size).astype(np.int64)
    counts = pd.DataFrame({"cell_x": cell_x, "cell_y": cell_y}).groupby(
        ["cell_x", "cell_y"], sort=True).size().rename("accident_count").reset_index()
    ordered_counts = np.sort(counts["accident_count"].to_numpy())
    threshold_index = max(0, int(np.ceil(percentile / 100.0 * len(ordered_counts))) - 1)
    threshold = int(ordered_counts[threshold_index])
    counts["accident_density_per_km2"] = counts["accident_count"] * (1_000_000.0 / cell_size ** 2)
    counts["is_hotspot"] = counts["accident_count"] >= threshold
    counts["hotspot_threshold_count"] = threshold
    counts["grid_id"] = [f"{int(x_index)}:{int(y_index)}"
                         for x_index, y_index in zip(counts["cell_x"], counts["cell_y"])]
    geometries = [box(row.cell_x * cell_size, row.cell_y * cell_size,
                      (row.cell_x + 1) * cell_size, (row.cell_y + 1) * cell_size)
                  for row in counts.itertuples(index=False)]
    grid = gpd.GeoDataFrame(counts, geometry=geometries, crs=metric_crs)
    result = transform_geodata(grid, points.crs)
    metrics = {
        "input_point_count": len(points), "occupied_cell_count": len(result),
        "hotspot_cell_count": int(result["is_hotspot"].sum()),
        "hotspot_threshold_count": threshold, "cell_size_meters": cell_size,
        "hotspot_percentile": percentile, "metric_crs": metric_crs.to_string(),
    }
    return result, metrics
