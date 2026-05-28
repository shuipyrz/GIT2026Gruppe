import json
import pandas as pd


def parse_uploaded_file(uploaded_file):
    filename = uploaded_file.name.lower()

    if filename.endswith(".geojson"):
        geojson_data = json.load(uploaded_file)
        return {
            "type": "geojson",
            "data": geojson_data
        }

    elif filename.endswith(".json"):
        data = json.load(uploaded_file)

        # 如果是 GeoJSON，也按 GeoJSON 处理
        if isinstance(data, dict) and data.get("type") in ["FeatureCollection", "Feature"]:
            return {
                "type": "geojson",
                "data": data
            }

        # 否则按普通点数据处理
        points = parse_json_points(data)
        return {
            "type": "points",
            "data": points
        }

    elif filename.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
        points = parse_csv_points(df)
        return {
            "type": "points",
            "data": points
        }

    else:
        raise ValueError("Nur JSON, GeoJSON oder CSV wird unterstützt.")


def parse_json_points(data):
    points = []

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError("JSON muss eine Liste von Objekten sein.")

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue

        lat = item.get("lat")
        lon = item.get("lon")
        name = item.get("name", f"Point {i + 1}")

        if lat is not None and lon is not None:
            points.append({
                "name": name,
                "lat": float(lat),
                "lon": float(lon)
            })

    if not points:
        raise ValueError("Keine gültigen Punkte mit lat/lon gefunden.")

    return points


def parse_csv_points(df):
    if "lat" not in df.columns or "lon" not in df.columns:
        raise ValueError("CSV muss die Spalten lat und lon enthalten.")

    points = []

    for i, row in df.iterrows():
        name = row["name"] if "name" in df.columns else f"Point {i + 1}"

        points.append({
            "name": name,
            "lat": float(row["lat"]),
            "lon": float(row["lon"])
        })

    return points