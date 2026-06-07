import os
import json
import re
from typing import TypedDict, Optional
from pathlib import Path
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END


#————————————————————————————AI——————————————————————————————————————————————

# --- 1. 环境配置 ---
current_dir = Path(__file__).parent
load_dotenv(dotenv_path=current_dir / ".env")

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise ValueError("DEEPSEEK_API_KEY wurde nicht gefunden!")

# --- 2. 初始化 DeepSeek ---
llm = ChatOpenAI(
    model="deepseek-chat", # 或者根据你的测试结果使用 deepseek-v4-flash
    openai_api_key=api_key,
    base_url="https://api.deepseek.com",
    # 增加超时限制，防止连接瞬间中断
    timeout=20, 
    max_retries=2
)

#  3. 默认地图样式
DEFAULT_MAP_STYLE = {
    "color": "blue",
    "weight": 2,
    "fillColor": "blue",
    "fillOpacity": 0.3
}

# --- 4. 定义Agent状态 ---
class AgentState(TypedDict):
    input_text: str           
    geojson_data: Optional[dict] 
    error_message: str        

# --- 5. 定义节点函数 ---
# 自然语言 → GeoJSON
def geo_generator(state: AgentState):
    user_input = state.get("input_text", "")
    
    # 强化指令：确保输出多个城市且无 Markdown 代码块
    instruction = f"""
    Du bist ein GIS-Experte. 
    Aufgabe: Erstelle ein valides GeoJSON (FeatureCollection) für ALLE genannten Orte: {user_input}
    Regeln:
    1. Erstelle für JEDEN genannten Ort ein separates Feature.
    2. Properties: Muss 'city' und 'distance_km' enthalten.
    3. Gib NUR das pure JSON zurück, KEIN Markdown (```json).
    """
    
    try:
        response = llm.invoke(instruction)
        content = response.content
        
        # 提取 JSON 的正则表达式
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(1))
            return {"geojson_data": data, "error_message": ""}
        else:
            return {"error_message": f"Kein JSON gefunden. AI-Output war: {content[:100]}..."}
    except Exception as e:
        return {"error_message": str(e)}

# 6.     style_modifier
# 自然语言 → 样式参数
def style_modifier(user_input, current_style):
    instruction = f"""
    Du bist ein Assistent für Kartenstil-Bearbeitung.

    Der Nutzer möchte den Stil einer Karte ändern.

    Aktueller Stil:
    {json.dumps(current_style, ensure_ascii=False)}

    Nutzeranweisung:
    {user_input}

    Erlaubte Parameter:
    - color: Linienfarbe, z.B. red, blue, green, black
    - weight: Linienstärke, Zahl zwischen 1 und 10
    - fillColor: Füllfarbe
    - fillOpacity: Transparenz der Füllung, Zahl zwischen 0 und 1

    Gib NUR ein valides JSON zurück.
    Kein Markdown.
    Beispiel:
    {{
        "color": "red",
        "weight": 5,
        "fillColor": "red",
        "fillOpacity": 0.4
    }}
    """

    try:
        response = llm.invoke(instruction)
        content = response.content

        json_match = re.search(r'(\{.*\})', content, re.DOTALL)

        if json_match:
            new_style = json.loads(json_match.group(1))

            allowed_keys = ["color", "weight", "fillColor", "fillOpacity"]

            updated_style = current_style.copy()

            for key in allowed_keys:
                if key in new_style:
                    updated_style[key] = new_style[key]

            return updated_style, ""

        else:
            return current_style, f"Kein JSON gefunden. AI-Output war: {content[:100]}..."

    except Exception as e:
        return current_style, str(e)


# --- 7. 构建图 ---
workflow = StateGraph(AgentState)
workflow.add_node("generator", geo_generator)
workflow.add_edge(START, "generator")
workflow.add_edge("generator", END)

# 8. agent_app
agent_app = workflow.compile()


#—————————————————————————————————————————————— GIS——————————————————————————————————————————————

import folium

# 1. 图层创建
def create_layered_map(layers, map_style=None):
    if map_style is None:
        map_style = DEFAULT_MAP_STYLE

    m = folium.Map(location=[51.0504, 13.7373], zoom_start=7)

    all_bounds = []

    for index, layer in enumerate(layers):
        layer_name = layer.get("name", f"Layer {index + 1}")

        if layer["type"] == "points":
            bounds = add_points_layer(m, layer["data"], layer_name)
            all_bounds.extend(bounds)

        elif layer["type"] == "geojson":
            bounds = add_geojson_layer(m, layer["data"], layer_name, map_style)
            all_bounds.extend(bounds)

    if all_bounds:
        try:
            m.fit_bounds(all_bounds)
        except Exception:
            pass

    folium.LayerControl().add_to(m)

    return m

# 2. points_layer
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


# 3. geojson_layer
def add_geojson_layer(m, geojson_data, layer_name, map_style):
    fg = folium.FeatureGroup(name=layer_name)

    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        style_function=lambda feature: map_style, #Folium 会用这个样式画 GeoJSON
        tooltip=create_geojson_tooltip(geojson_data)
    ).add_to(fg)

    fg.add_to(m)

    try:
        return geo_layer.get_bounds()
    except Exception:
        return []


# 4. geojson_tooltip
def create_geojson_tooltip(geojson_data):
    fields = get_tooltip_fields(geojson_data)

    if fields:
        return folium.GeoJsonTooltip(
            fields=fields,
            aliases=fields
        )

    return None


# 5. get_tooltip_fields
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


# ————————————————————————————————————————————————————Data————————————————————————————————————————————————————

import pandas as pd

# 1. parse_uploaded
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

# 2. parse_json_points
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


# 3. parse_csv_points
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