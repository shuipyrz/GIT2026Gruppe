import folium


def create_layered_map(layers):
    m = folium.Map(location=[51.0504, 13.7373], zoom_start=7)

    all_bounds = []

    for index, layer in enumerate(layers):
        layer_name = layer.get("name", f"Layer {index + 1}")

        if layer["type"] == "points":
            bounds = add_points_layer(m, layer["data"], layer_name)
            all_bounds.extend(bounds)

        elif layer["type"] == "geojson":
            bounds = add_geojson_layer(m, layer["data"], layer_name)
            all_bounds.extend(bounds)

    if all_bounds:
        try:
            m.fit_bounds(all_bounds)
        except Exception:
            pass

    folium.LayerControl().add_to(m)

    return m


def add_points_layer(m, points, layer_name):
    fg = folium.FeatureGroup(name=layer_name)
    bounds = []

    for point in points:
        lat = point["lat"]
        lon = point["lon"]
        name = point.get("name", layer_name)

        folium.Marker(
            location=[lat, lon],
            popup=name,
            tooltip=name
        ).add_to(fg)

        bounds.append([lat, lon])

    fg.add_to(m)
    return bounds


def add_geojson_layer(m, geojson_data, layer_name):
    fg = folium.FeatureGroup(name=layer_name)

    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        tooltip=create_geojson_tooltip(geojson_data)
    ).add_to(fg)

    fg.add_to(m)

    try:
        return geo_layer.get_bounds()
    except Exception:
        return []


def create_geojson_tooltip(geojson_data):
    fields = get_tooltip_fields(geojson_data)

    if fields:
        return folium.GeoJsonTooltip(
            fields=fields,
            aliases=fields
        )

    return None


def get_tooltip_fields(geojson_data):
    try:
        if geojson_data["type"] == "FeatureCollection":
            features = geojson_data.get("features", [])
            if features and "properties" in features[0]:
                return list(features[0]["properties"].keys())[:3]

        elif geojson_data["type"] == "Feature":
            return list(geojson_data.get("properties", {}).keys())[:3]

    except Exception:
        return []

    return []