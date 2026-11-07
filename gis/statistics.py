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
