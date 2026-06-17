# ==========================================
# BLOCK 1: ENV CONFIGURATION & INITIALIZATION
# ==========================================
# %% BLOCK 1: ENV CONFIGURATION & INITIALIZATION
import os
import json
import re
from typing import TypedDict, Optional, List
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

import urllib.parse
import requests

current_dir = Path(__file__).parent
load_dotenv(dotenv_path=current_dir / ".env")

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise ValueError("DEEPSEEK_API_KEY wurde nicht gefunden!")

llm = ChatOpenAI(
    model="deepseek-chat",
    openai_api_key=api_key,
    base_url="https://api.deepseek.com",
    timeout=20, 
    max_retries=2
)

DEFAULT_MAP_STYLE = {
    "color": "blue",
    "weight": 2,
    "fillColor": "blue",
    "fillOpacity": 0.3
}

# ==========================================
# BLOCK 2: PYDANTIC SCHEMAS FOR STRUCTURED OUTPUT
# ==========================================
# %% BLOCK 2: PYDANTIC SCHEMAS FOR STRUCTURED OUTPUT
class MapActionParser(BaseModel):
    intent_type: str = Field(
        ..., 
        description="STRENG KLASSIFIZIEREN: 'generate_data' (wenn neue Orte/Städte gesucht werden) ODER 'modify_view' (wenn Farben, Zoom, oder Stil geändert werden)."
    )
    color: Optional[str] = Field(None, description="Die Linienfarbe, z.B. 'red', 'blue', 'green'.")
    weight: Optional[int] = Field(None, description="Die Linienstärke als Ganzzahl zwischen 1 und 10.")
    fillColor: Optional[str] = Field(None, description="Die Füllfarbe für Flächen.")
    fillOpacity: Optional[float] = Field(None, description="Die Transparenz der Füllung zwischen 0.0 und 1.0.")
    focus_city: Optional[str] = Field(None, description="Der Name der Stadt, falls vom Nutzer explizit erwähnt.")
    zoom_level: Optional[int] = Field(None, description="Das gewünschte Zoom-Level als Ganzzahl.")

# ==========================================
# BLOCK 3: LANGGRAPH STATE DEFINITION
# ==========================================
# %% BLOCK 3: LANGGRAPH STATE DEFINITION
class AgentState(TypedDict):
    input_text: str                 
    chat_history: List[dict]        
    intent_type: str                
    geojson_data: Optional[dict]    
    view_updates: dict              
    map_style: dict                 
    error_message: str              

# ==========================================
# BLOCK 4: CORE AGENT NODES
# ==========================================
# %% BLOCK 4: CORE AGENT NODES
def master_intent_router(state: AgentState):
    user_input = state.get("input_text", "")
    current_style = state.get("map_style", DEFAULT_MAP_STYLE.copy())
    history = state.get("chat_history", [])
    
    history_str = ""
    for msg in history[-4:]:
        role_label = "Nutzer" if msg["role"] == "user" else "Assistent"
        history_str += f"{role_label}: {msg['content']}\n"

    intelligent_instruction = f"""
    Du bist das Gehirn eines intelligenten GIS-Systems. Analysiere den aktuellen Nutzerbefehl.

    Bisheriger Chatverlauf für Kontext:
    {history_str}
    Aktueller Kartenstil: {json.dumps(current_style)}

    REGLER FÜR INTENT_TYPE:
    - Wenn der Nutzer neue Gebiete generieren, Orte anzeigen oder neue Daten auf die Karte bringen will, MUSS intent_type = "generate_data" sein.
    - Wenn der Nutzer NUR Farben, Stärken oder das Aussehen bestehender Dinge ändern will (z.B. "Mach es rot", "Farbe blau ändern"), MUSS intent_type = "modify_view" sein.

    GIB AUSSCHLIESSLICH EIN VALIDES JSON-OBJEKT IN FOLGENDEM FORMAT ZURÜCK:
    {{
      "intent_type": "generate_data" oder "modify_view",
      "color": "Farbe oder null",
      "weight": Ganzzahl oder null,
      "fillColor": "Farbe oder null",
      "fillOpacity": Fließkommazahl oder null,
      "focus_city": "Name der erwähnten Stadt oder null",
      "zoom_level": Ganzzahl oder null
    }}
    """
    
    try:
        response = llm.invoke(intelligent_instruction + f"\nNutzerbefehl: {user_input}")
        content = response.content.strip()
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)
        if json_match:
            parsed_json = json.loads(json_match.group(1))
        else:
            raise ValueError("Kein gültiges JSON.")
            
        detected_intent = parsed_json.get("intent_type", "").strip()
        low_input = user_input.lower()
        
        # 口语识别校正
        if any(k in low_input for k in ["zeige", "erstelle", "karte", "berlin", "hamburg", "dresden", "leipzig", "gebiet", "zone"]):
            detected_intent = "generate_data"
        elif not detected_intent:
            detected_intent = "modify_view"
            
        updated_style = current_style.copy()
        for key in ["color", "weight", "fillColor", "fillOpacity"]:
            val = parsed_json.get(key)
            if val is not None and val != "null":
                if key == "weight": updated_style[key] = int(val)
                elif key == "fillOpacity": updated_style[key] = float(val)
                else: updated_style[key] = str(val)
                
        # 🌟 如果用户说“变红”，我们自动同步修改边框线颜色和填充颜色
        if "rot" in low_input or parsed_json.get("color") == "red":
            updated_style["color"] = "red"
            updated_style["fillColor"] = "red"
        elif "blau" in low_input:
            updated_style["color"] = "blue"
            updated_style["fillColor"] = "blue"
        elif "grün" in low_input or "grun" in low_input:
            updated_style["color"] = "green"
            updated_style["fillColor"] = "green"

        view_updates = {}
        focus_city = parsed_json.get("focus_city")
        if not focus_city or focus_city == "null":
            for city in ["Dresden", "Berlin", "Leipzig", "Hamburg", "München", "Köln", "Frankfurt"]:
                if city.lower() in low_input:
                    focus_city = city
                    break
                    
        if focus_city and focus_city != "null":
            view_updates["focus_city"] = focus_city
            
        zoom_level = parsed_json.get("zoom_level")
        if zoom_level is not None and zoom_level != "null":
            view_updates["zoom_level"] = int(zoom_level)
            
        return {
            "intent_type": detected_intent,
            "map_style": updated_style,
            "view_updates": view_updates,
            "error_message": ""
        }
    except Exception as e:
        return {"intent_type": "modify_view", "map_style": current_style, "view_updates": {}, "error_message": ""}



def data_generation_node(state: AgentState):
    """
    单元块：真实城市轮廓生成节点（OSM Boundary Gateway）。
    彻底告别 AI 瞎画的规则图形，100% 实时抓取官方真实的二维城市行政区划边界！
    """
    if state.get("intent_type") == "modify_view":
        return {"geojson_data": state.get("geojson_data")}
        
    user_input = state.get("input_text", "")
    
    # 1. 第一步：让大模型变成纯粹的“实体提取专家”，帮我们抠出准确的德语/英语城市名
    ner_instruction = """
    Du bist ein Geodaten-Extraktor. Erkenne den Namen der SCHLÜSSELSTADT aus dem Nutzerbefehl.
    Antworte AUSSCHLIESSLICH mit dem reinen Stadtnamen im Nominativ (z.B. "Dresden", "Berlin", "München").
    Wenn keine Stadt gefunden wird, antworte mit "null". Gib keinen anderen Text zurück.
    """
    
    try:
        response = llm.invoke(ner_instruction + f"\nNutzerbefehl: {user_input}")
        extracted_city = response.content.strip().replace('"', '').replace("'", "")
        
        # 兜底：如果大模型犯懒漏掉了，我们用之前的本地列表强行匹配
        if not extracted_city or extracted_city.lower() == "null":
            low_input = user_input.lower()
            for city in ["Dresden", "Berlin", "Leipzig", "Hamburg", "München", "Köln", "Frankfurt"]:
                if city.lower() in low_input:
                    extracted_city = city
                    break
                    
        if not extracted_city or extracted_city.lower() == "null":
            return {"error_message": "Keine gültige Stadt im Befehl erkannt."}
            
        # 2. 第二步：高能高阶 GIS 操作！利用 OSM Nominatim API 实时下载真实的城市边界多边形
        # 对城市名进行 URL 编码（防止出现 German Umlauts 如 München 导致请求崩溃）
        encoded_city = urllib.parse.quote(extracted_city)
        osm_url = f"https://nominatim.openstreetmap.org/search?q={encoded_city}&format=geojson&polygon_geojson=1&limit=1"
        
        headers = {
            "User-Agent": "GeoGPT_University_Project_Agent_v4"  # OSM 要求必须提供清晰的 UA
        }
        
        osm_response = requests.get(osm_url, headers=headers, timeout=10)
        
        if osm_response.status_code == 200:
            osm_data = osm_response.json()
            features = osm_data.get("features", [])
            
            if features:
                # 筛选抓取出来的特征，确保它是一个有效的 Polygon 或 MultiPolygon（真实面状轮廓）
                target_feature = None
                for feat in features:
                    geom_type = feat.get("geometry", {}).get("type", "")
                    if geom_type in ["Polygon", "MultiPolygon"]:
                        target_feature = feat
                        break
                
                if not target_feature:
                    target_feature = features[0] # 兜底机制
                
                # 规范化包装为标准的 FeatureCollection，确保 Folium 完美解析
                geojson_contour = {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {
                            "name": extracted_city,
                            "source": "OpenStreetMap Real Boundary"
                        },
                        "geometry": target_feature["geometry"]
                    }]
                }
                
                return {"geojson_data": geojson_contour, "error_message": ""}
            else:
                return {"error_message": f"Die realen Umrisse für '{extracted_city}' wurden bei OSM nicht gefunden."}
        else:
            return {"error_message": f"OSM-Server-Fehler: Statuscode {osm_response.status_code}"}
            
    except Exception as e:
        return {"error_message": f"Fehler bei der Umriss-Generierung: {str(e)}"}
    
# ==========================================
# BLOCK 5: LANGGRAPH WORKFLOW COMPILATION
# ==========================================
# %% BLOCK 5: LANGGRAPH WORKFLOW COMPILATION
workflow = StateGraph(AgentState)
workflow.add_node("intent_router", master_intent_router)
workflow.add_node("data_generator", data_generation_node)
workflow.add_edge(START, "intent_router")
workflow.add_edge("intent_router", "data_generator")
workflow.add_edge("data_generator", END)
agent_app = workflow.compile()

# ==========================================
# BLOCK 6: GIS MAP RENDERING ENGINE
# ==========================================
# %% BLOCK 6: GIS MAP RENDERING ENGINE
import folium
import branca.colormap as cm
from folium.plugins import HeatMap
from geopy.geocoders import Nominatim

geolocator = Nominatim(user_agent="geogpt_dresden_v4")

def create_layered_map(layers, view_updates=None):
    """
    单元块：地图渲染
    """
    if view_updates is None:
        view_updates = {}

    m = folium.Map(location=[51.0504, 13.7373], zoom_start=6)
    latest_layer_bounds = []
    for index, layer in enumerate(layers):
        layer_name = layer.get("name", f"Layer {index + 1}")
        layer_style = layer.get("style", DEFAULT_MAP_STYLE)
        
        if layer["type"] == "points":
            bounds = add_points_layer(m, layer["data"], layer_name)
            if index == len(layers) - 1: latest_layer_bounds = bounds

        elif layer["type"] == "line":
            bounds = add_line_layer( m, layer["data"], layer_name)
            if index == len(layers) - 1: latest_layer_bounds = bounds

        elif layer["type"] == "polygon":
            bounds = add_geojson_layer(m, layer["data"], layer_name, layer_style)
            if index == len(layers) - 1: latest_layer_bounds = bounds

        elif layer["type"] == "heatmap":
            bounds = add_heatmap_layer(m, layer["data"], layer_name, layer["data_field"])
            if index == len(layers) - 1:latest_layer_bounds = bounds

        elif layer["type"] == "choropleth":
            bounds = add_choropleth_layer(m, layer["data"], layer_name, layer["data_field"])
            if index == len(layers) - 1:latest_layer_bounds = bounds

        elif layer["type"] == "geojson":
            bounds = add_geojson_layer(m, layer["data"], layer_name, layer_style)
            if index == len(layers) - 1: latest_layer_bounds = bounds

    # 🌟 核心修复：如果刚刚生成了新图层，无脑强制使用 fit_bounds 自适应包裹并高精度放大！
    if latest_layer_bounds:
        try:
            m.fit_bounds(latest_layer_bounds)
            # 如果大模型指定了更深的缩放，或者用户手动覆盖
            if view_updates.get("zoom_level"):
                m.zoom_start = view_updates["zoom_level"]
        except Exception:
            pass
            
    # 如果有明确的城市地理编码覆盖需求
    if view_updates.get("focus_city"):
        try:
            location = geolocator.geocode(view_updates["focus_city"])
            if location:
                m.location = [location.latitude, location.longitude]
                if not latest_layer_bounds:
                    m.zoom_start = 12
        except Exception:
            pass

    folium.LayerControl().add_to(m)
    return m

def add_points_layer(m, points, layer_name):
    fg = folium.FeatureGroup(name=layer_name)
    bounds = []
    for point in points:
        lat, lon = point["lat"], point["lon"]
        folium.Marker(location=[lat, lon], popup=point.get("name", layer_name)).add_to(fg)
        bounds.append([lat, lon])
    fg.add_to(m)
    return bounds

def add_heatmap_layer(m, points, layer_name, data_field):

    heat_data = []

    for point in points:

        lat = point["lat"]
        lon = point["lon"]

        properties = point.get("properties", {})

        value = properties.get(data_field)

        if value is not None:

            try:
                heat_data.append([
                    lat,
                    lon,
                    float(value)
                ])
            except:
                pass

    if heat_data:

        HeatMap(
            heat_data,
            name=layer_name,
            radius=25,
            blur=15,
            min_opacity=0.3
        ).add_to(m)

    return [[row[0], row[1]] for row in heat_data]
# 可以根据数值字段自动给 Polygon 上色
def add_choropleth_layer(m, geojson_data, layer_name, data_field):
    features = geojson_data.get("features", [])

    values = []

    for feature in features:
        properties = feature.get("properties", {})
        value = properties.get(data_field)

        if value is not None:
            try:
                values.append(float(value))
            except:
                pass

    if not values:
        return []

    min_value = min(values)
    max_value = max(values)

    if min_value == max_value:
        max_value = min_value + 1

    colormap = cm.linear.YlOrRd_09.scale(min_value, max_value)
    colormap.caption = f"{layer_name} - {data_field}"

    def style_function(feature):
        properties = feature.get("properties", {})
        value = properties.get(data_field)

        try:
            value = float(value)

            fill_color = colormap(value)

        except:
            fill_color = "#cccccc"

        return {
            "fillColor": fill_color,
            "color": "black",
            "weight": 0.5,
            "fillOpacity": 0.7
        }

    fg = folium.FeatureGroup(name=layer_name)

    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        style_function=style_function,
        tooltip=create_geojson_tooltip(geojson_data)
    ).add_to(fg)

    fg.add_to(m)
    colormap.add_to(m)

    try:
        return geo_layer.get_bounds()
    except Exception:
        return []
# Line Renderer
def add_line_layer(m, geojson_data, layer_name, color="blue", weight=3):
    fg = folium.FeatureGroup(name=layer_name)
    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        style_function=lambda feature: {
            "color": color,
            "weight": weight
        }
    ).add_to(fg)
    fg.add_to(m)
    try:
        return geo_layer.get_bounds()
    except:
        return []
# geojson Renderer
def add_geojson_layer(m, geojson_data, layer_name, map_style):
    fg = folium.FeatureGroup(name=layer_name)
    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        style_function=lambda feature: map_style,
        tooltip=create_geojson_tooltip(geojson_data)
    ).add_to(fg)
    fg.add_to(m)
    try:
        return geo_layer.get_bounds()
    except Exception:
        return []

def create_geojson_tooltip(geojson_data):
    fields = get_tooltip_fields(geojson_data)
    if fields: return folium.GeoJsonTooltip(fields=fields, aliases=fields)
    return None

def get_tooltip_fields(geojson_data):
    try:
        if geojson_data["type"] == "FeatureCollection":
            features = geojson_data.get("features", [])
            if features and "properties" in features[0]:
                return list(features[0]["properties"].keys())[:3]
    except Exception: pass
    return []
# 判断上传的 GeoJSON 是否适合做 Choropleth
def is_polygon_geojson(geojson_data):
    try:
        features = geojson_data.get("features", [])

        for feature in features:
            geometry_type = feature.get("geometry", {}).get("type")
            if geometry_type in ["Polygon", "MultiPolygon"]:
                return True

    except Exception:
        pass

    return False
# 判断线数据
def is_line_geojson(geojson_data):
    try:
        features = geojson_data.get("features", [])

        for feature in features:
            geometry_type = feature.get(
                "geometry",
                {}
            ).get("type")
            if geometry_type in [
                "LineString",
                "MultiLineString"
            ]:
                return True
    except:
        pass

    return False

# 自动找出可以做 Choropleth 的字段
def get_numeric_geojson_fields(geojson_data):
    numeric_fields = set()

    try:
        features = geojson_data.get("features", [])

        for feature in features:
            properties = feature.get("properties", {})

            for key, value in properties.items():
                if isinstance(value, (int, float)):
                    numeric_fields.add(key)

                else:
                    try:
                        float(value)
                        numeric_fields.add(key)
                    except:
                        pass

    except Exception:
        pass

    # 过滤掉明显不适合作为制图字段的 ID 类字段
    excluded_keywords = ["id", "objectid", "fid", "katnam"]

    filtered_fields = []

    for field in numeric_fields:
        lower_field = field.lower()

        if not any(keyword in lower_field for keyword in excluded_keywords):
            filtered_fields.append(field)

    return sorted(filtered_fields)

# ==========================================
# BLOCK 7: DATA PARSING FACTORY
# ==========================================
# %% BLOCK 7: DATA PARSING FACTORY
import pandas as pd

def parse_uploaded_file(uploaded_file):
    filename = uploaded_file.name.lower()
    if filename.endswith(".geojson"):
        geojson_data = json.load(uploaded_file)

        if is_polygon_geojson(geojson_data):
            return {
                "type": "polygon",
                "data": geojson_data
            }

        if is_line_geojson(geojson_data):
            return {
                "type": "line",
                "data": geojson_data
            }

        points = convert_geojson_points_to_points(geojson_data)
        if points:
            return {
                "type": "points",
                "data": points,
                "original_geojson": geojson_data
            }

        return {
            "type": "geojson",
            "data": geojson_data
        }
    
    elif filename.endswith(".json"):
        data = json.load(uploaded_file)

        if isinstance(data, dict) and data.get("type") in ["FeatureCollection", "Feature"]:

            if is_polygon_geojson(data):
                return {
                    "type": "polygon",
                    "data": data
                }

            if is_line_geojson(data):
                return {
                    "type": "line",
                    "data": data
                }

            points = convert_geojson_points_to_points(data)
            if points:
                return {
                    "type": "points",
                    "data": points,
                    "original_geojson": data
                }

            return {
                "type": "geojson",
                "data": data
            }
        return {"type": "points", "data": parse_json_points(data)}
    elif filename.endswith(".csv"): return {"type": "points", "data": parse_csv_points(pd.read_csv(uploaded_file))}
    raise ValueError("Format nicht unterstützt.")

def parse_json_points(data):
    points = []

    if isinstance(data, dict):
        data = [data]

    for i, item in enumerate(data):
        lat = item.get("lat")
        lon = item.get("lon")

        if lat is not None and lon is not None:
            properties = {}

            for key, value in item.items():
                if key not in ["lat", "lon", "name"]:
                    try:
                        properties[key] = float(value)
                    except:
                        properties[key] = value

            points.append({
                "name": item.get("name", f"Point {i+1}"),
                "lat": float(lat),
                "lon": float(lon),
                "properties": properties
            })

    return points

def convert_geojson_points_to_points(geojson_data):
    points = []

    features = geojson_data.get("features", [])

    for i, feature in enumerate(features):
        geometry = feature.get("geometry", {})
        properties = feature.get("properties", {})

        if geometry.get("type") == "Point":
            coordinates = geometry.get("coordinates", [])

            if len(coordinates) >= 2:
                lon = coordinates[0]
                lat = coordinates[1]

                points.append({
                    "name": properties.get("standbesch", f"Point {i+1}"),
                    "lat": float(lat),
                    "lon": float(lon),
                    "properties": properties
                })

    return points

def parse_csv_points(df):
    if "lat" not in df.columns or "lon" not in df.columns:
        raise ValueError("CSV muss die Spalten lat und lon enthalten.")

    points = []

    for i, row in df.iterrows():
        properties = {}

        for col in df.columns:
            if col not in ["lat", "lon", "name"]:
                value = row[col]

                try:
                    properties[col] = float(value)
                except:
                    properties[col] = value

        points.append({
            "name": row["name"] if "name" in df.columns else f"Point {i+1}",
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "properties": properties
        })

    return points

# ==========================================
# zheng BLOCK 8: Karte exportieren
# ==========================================
from branca.element import Element
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader

# 添加标题
def Titel_hinzufügen(m:folium.Map,titel:str) -> None:
    title_html = f'''
    <div style="
        position: fixed;
        top: 10px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 9999;
        background-color: white;
        padding: 8px 15px;
        border-radius: 6px;
        font-size: 18px;
        font-weight: bold;
        box-shadow: 0 0 6px rgba(0,0,0,0.3);
    ">
        {titel}
    </div>
    '''
    m.get_root().html.add_child(Element(title_html))

#根据HTML创建图片
def html_to_png(html:str,png:str) -> None:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1200,800")
    driver = webdriver.Chrome(options=options)
    driver.get("file://" + os.path.abspath(html))
    time.sleep(0.1)  # wait...
    driver.save_screenshot(png)
    driver.quit()

#根据图片创建PDF
def png_to_pdf(png:str,pdf:str) -> None:
    c = canvas.Canvas(pdf, pagesize=landscape(A4))
    page_width, page_height = landscape(A4)
    img = ImageReader(png)
    img_width, img_height = img.getSize()
    ratio = min(page_width / img_width, page_height / img_height)
    new_width = img_width * ratio
    new_height = img_height * ratio
    x = (page_width - new_width) / 2
    y = (page_height - new_height) / 2
    c.drawImage(png, x, y, width=new_width, height=new_height)
    c.save()

TOOLS = [
    {
        "name": "create_map",
        "description": "Karte erstellen (Heatmap oder Choropleth)"
    },
    {
        "name": "set_title",
        "description": "Titel zurückgeben"
    },
    {
        "name": "export_map",
        "description": "Karte exportieren"
    },
    {
        "name": "Kartenstil_anpassen",
        "description": "Ändern den Kartenstil in einem bestimmten Layer"
    },
    {
        "name": "Layer_verwalten",
        "description": "Füge neue Layer hinzu, entferne oder kopiere bestehende Layer."
    },
    {
        "name": "Geodatei_analysieren",
        "description": "Analysiert die Struktur und den Inhalt einer Geodatendatei und erklärt die enthaltenen Informationen."
    }
]


def parse_intent(text: str):
    """
    Analysieren Anforderungen der Nutzer:innen
    """
    
    tool_text = "\n".join(
    f"- {t['name']}: {t['description']}"
    for t in TOOLS
)
    system_prompt = f"""
        Du bist ein GIS-Assistent.

        Folgende Tools stehen zur Verfügung:

        {tool_text}

        Gib nur JSON zurück:

        {{
        "tool": "<tool_name>"
        }}

        Wenn kein Tool passt:
        {{
        "tool": "error"
        }}

        Nur JSON ausgeben.
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text}
    ])

    result = response.content.strip()
    return json.loads(result)

def get_selected_layer_name(user_text: str,layers):
    """
    Bestimmen den Ziellayer
    """

    layer_names = "\n".join([l["name"] for l in layers])
    system_prompt = f"""
        Du bist ein GIS-Assistent.

        Der Benutzer kann Kartenlayer verwalten oder auswählen.

        Layer werden vom Nutzer bereitgestellt.

        Deine Aufgabe ist es, den am besten passenden Layer anhand der Benutzeranfrage auszuwählen.

        Regeln:
        - Gib nur den Namen eines Layers zurück
        - Antworte ausschließlich im JSON-Format
        - Kein zusätzlicher Text

        Format:

        {{
        "layer_name": "<name>"
        }}

        WICHTIG:
        - Wenn kein passender Layer gefunden wird, wähle den letzten Layer aus der Liste.
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_text}\n\n{layer_names}"}
    ])

    result = json.loads(response.content.strip())
    return result["layer_name"]

def set_selected_layer_name(layer):
    """
    Geben dem Layer einen geeigneten Name
    """

    system_prompt = """
        Du bist ein kreativer GIS-Assistent.

        Du erhältst Informationen über einen geographischen Layer.

        Deine Aufgabe:
        Erzeuge einen passenden, anschaulichen und gut klingenden MAP TITEL.

        Der Titel soll:
        - beschreibend sein
        - nicht nur den Dateinamen wiederholen
        - etwas menschlicher und natürlicher klingen
        - optional den Kontext oder die Bedeutung des Layers ausdrücken

        Antworte nur im JSON-Format:

        {
        "title": "<creative title>"
        }

        Keine Erklärungen.
        """
    if "data_field" in layer:
        minimal_layer = {
        "name": layer["name"],
        "data_field": layer["data_field"],
        "type": layer["type"],
    }
    else:
        minimal_layer = {
        "name": layer["name"],
        "type": layer["type"],
    }
    content = json.dumps(minimal_layer, ensure_ascii=False, indent=2)

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content}
    ])

    result = json.loads(response.content.strip())
    return result["title"]

SUPPORTED_MAP_TYPES = [
    "points",
    "line",
    "polygon",
    "heatmap",
    "choropleth"
]

def bestimmen_Kartentyp(user_text: str,layer):
    """
    bestimmen den Typ der erstllten Karte
    """

    system_prompt = f"""
        Du bist ein GIS-Assistent.

        Bitte analysiere die Benutzeranfrage sowie die Informationen der Geo-Datenstruktur und bestimme den gewünschten Kartentyp.

        Unterstützte Kartentypen:
        {SUPPORTED_MAP_TYPES}

        Ausgabeformat (immer gleich!):

        {{
            "type": "<type>",
            "data_field": "<field or null>"
        }}

        Regeln:

        1. Gib ausschließlich ein gültiges JSON-Objekt zurück.
        Keine Erklärungen, kein Markdown, kein zusätzlicher Text.
        2. Die Ausgabe MUSS immer sowohl "type" als auch "data_field" enthalten.
        3. Für Basiskartentypen (point, line, polygon) setze "data_field" auf null.
        4. Heatmap ist nur erlaubt, wenn layer.type == "point".
        5. Choropleth ist nur erlaubt, wenn layer.type == "polygon".
        6. Für Heatmap und Choropleth darf "data_field" niemals null sein.
        7. "data_field" muss aus den verfügbaren Feldern (data_fields) gewählt werden.
        Eigene oder erfundene Feldnamen sind nicht erlaubt.
        8. Wenn der Benutzer ein bestimmtes Feld angibt, soll dieses bevorzugt verwendet werden.
        Andernfalls wähle das geeignetste Feld aus den verfügbaren Daten.
        """

    minimal_layer = {
    "name": layer["name"],
    "type": layer["type"],
    "data_fields": layer["data_fields"],
}
    content = json.dumps(minimal_layer, ensure_ascii=False, indent=2)

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_text}\n\n{content}"}
    ])

    result = json.loads(response.content.strip())
    return result

def get_map_style(user_text: str,layer):

    aktuell_style=json.dumps(layer["style"], ensure_ascii=False, indent=2)
    system_prompt = f"""
        Du bist ein GIS-Styling-Assistent für Folium (Leaflet).

        Du erhältst den aktuellen Kartenstil :

        DEFAULT_MAP_STYLE:
        {aktuell_style}

        Deine Aufgabe:
        Passe diesen Style an, basierend auf der Benutzeranfrage.

        REGELN (SEHR WICHTIG):
        1. Ändere nur die Werte, nicht die Keys
        2. KEINE neuen Felder hinzufügen
        3. KEINE Felder entfernen
        4. Alle Werte müssen aus den erlaubten Bereichen stammen
        5. Wenn die Anfrage unklar ist → behalte den alten Wert

        ERLAUBTE WERTE:

        color / fillColor:
        - "blue"
        - "red"
        - "green"
        - "black"
        - "orange"
        - "purple"
        - "gray"
        - "yellow"

        weight:
        - ganze Zahlen von 1 bis 10

        fillOpacity:
        - Werte zwischen 0.0 und 1.0

        OUTPUT FORMAT (streng):
        {{
        "color": "...",
        "weight": ...,
        "fillColor": "...",
        "fillOpacity": ...
        }}

        Keine Erklärungen, nur JSON.
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ])

    result = json.loads(response.content.strip())
    return result

def verwalten_layers(user_text: str,layers):

    layer_names = "\n".join([l["name"] for l in layers])
    system_prompt = """
        Du bist ein GIS Layer Management Assistent.

        Du erhältst:
        - eine Liste aktueller Layer
        - eine Benutzeranweisung

        Deine Aufgabe:
        Erzeuge die neue finale Layer-Liste nach der Benutzeranweisung.

        REGELN:
        1. Antworte nur im JSON-Format
        2. Gib eine vollständige Liste der finalen Layer zurück
        3. Jeder Layer muss eindeutig benannt sein
        4. Keine Duplikate in "name"
        5. Wenn ein Layer gelöscht werden soll → einfach NICHT in der Liste enthalten
        6. Wenn ein Layer kopiert wird → füge einen neuen Layer mit neuem Namen hinzu
        7. Wenn alle Layer gelöscht werden sollen:
           → gib eine leere Liste zurück UND setze "state": "empty"
        8. Reihenfolge beibehalten, außer Benutzer sagt etwas anderes
        9. Keine zusätzlichen Felder oder Kommentare

        OUTPUT FORMAT:

        {
        "state": "normal | empty",
        "layers": [
            {
            "name": "<layer_name>",
            "source": "<original_name>"
            }
        ]
        }
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_text}, die Liste aktueller Layer:\n\n{layer_names}"}
    ])

    result = json.loads(response.content.strip())
    
    if result["state"] == "empty":
        new_layers = []
    else:
        new_layers = result["layers"]
    return new_layers

def verstehen_Geodatei(user_text: str,layer):

    data = layer["data"]
    limit=10
    if isinstance(data, list):
        layer_data_sample = data[:limit]
    elif isinstance(data, dict):
        layer_data_sample = {}
        for i, (key, value) in enumerate(data.items()):
            if i >= limit:
                break
            layer_data_sample[key] = value
    else:
        layer_data_sample = data
    sample=json.dumps(layer_data_sample, ensure_ascii=False, indent=2)
    system_prompt = f"""
        Du bist ein GIS-Datenanalyse-Assistent.

        Du erhältst einen Ausschnitt einer Geodatendatei.

        Deine Aufgaben:

        1. Analysiere die Datenstruktur.
        2. Erkläre die wichtigsten Attribute verständlich.
        3. Fasse zusammen, welche Informationen der Datensatz enthält.
        4. Empfiehl eine oder mehrere geeignete Kartentypen.

        Die Anwendung unterstützt ausschließlich folgende Kartentypen:

        {SUPPORTED_MAP_TYPES}

        Für jede Empfehlung erkläre kurz, warum dieser Kartentyp geeignet ist.

        Falls mehrere Kartentypen sinnvoll sind, sortiere sie nach ihrer Eignung.

        Falls ein Kartentyp nicht geeignet ist, empfehle ihn nicht."""
    
        # REGELN:
        # - Keine Programmierdetails
        # - Keine unnötigen technischen Erklärungen
        # - Fokus auf verständliche Erklärung
        # - Antworte nur im JSON-Format

        # FORMAT:

        # {
        # "type": "...",
        # "feature_count": ...,
        # "geometry_types": [...],
        # "fields": {
        #     "field_name": "meaning"
        # },
        # "summary": "..."
        # }

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_text}, die Beispieldatei:\n\n{sample}"}
    ])
    
    return response.content.strip()
# %%
