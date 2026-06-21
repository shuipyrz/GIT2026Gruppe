# %%
# ==========================================
# BLOCK 1: FRONTEND INITIALIZATION & SESSION MANAGEMENT
# ==========================================
import streamlit as st
import folium
import json
import copy
from streamlit_folium import st_folium
from backend import agent_app
from backend import parse_uploaded_file
from backend import create_layered_map
from backend import DEFAULT_MAP_STYLE
from backend import is_polygon_geojson
from backend import is_line_geojson
from backend import get_numeric_geojson_fields
from backend import Titel_hinzufügen
from backend import html_to_png
from backend import png_to_pdf
# 🌟 核心修复：把导入语句移到文件顶部！
from backend import smart_analyze_geodata

st.set_page_config(layout="wide", page_title="GeoGPT Intelligent Agent")
st.title("Intelligenter Chatbot für Kartenerstellung")

if "messages" not in st.session_state: st.session_state.messages = []
if "current_geojson" not in st.session_state: st.session_state.current_geojson = None
if "layers" not in st.session_state: st.session_state.layers = []
if "processed_files" not in st.session_state: st.session_state.processed_files = set()
if "map_style" not in st.session_state: st.session_state.map_style = DEFAULT_MAP_STYLE.copy()
if "view_updates" not in st.session_state: st.session_state.view_updates = {}
#  新增地图渲染键，用于物理强制刷新视图组件
if "map_key" not in st.session_state: st.session_state.map_key = 0
if "available_choropleth_fields" not in st.session_state:st.session_state.available_choropleth_fields = []
#  控制导出地图
if "show_export_buttons" not in st.session_state: st.session_state.show_export_buttons = False
if "export_files" not in st.session_state: st.session_state.export_files = {}
#  控制标题
if "Karte_Titel" not in st.session_state: st.session_state.Karte_Titel = ""

#  控制要展示的图层
if "show_layers" not in st.session_state: st.session_state.show_layers = []

# %%
# ==========================================
# BLOCK 2: SIDEBAR CONTROL PANEL
# ==========================================
with st.sidebar:
    st.markdown("### Kontrollzentrum")
    if st.button("Chat & Layer löschen"):
        st.session_state.messages = []
        st.session_state.current_geojson = None
        st.session_state.layers = []
        st.session_state.processed_files = set()
        st.session_state.map_style = DEFAULT_MAP_STYLE.copy()
        st.session_state.view_updates = {}
        st.session_state.available_choropleth_fields = []
        st.session_state.map_key += 1
        st.rerun()

# %%
# ==========================================
# BLOCK 3: LAYOUT SYSTEM (COLUMNS)
# ==========================================
col1, col2 = st.columns([1, 1.5])

with col1:
    st.subheader("Daten & Chat")
    
    # 🌟 核心改进 1：添加 accept_multiple_files=True，允许用户多选文件
    uploaded_files = st.file_uploader(
        "Bitte laden Sie Ihre Daten hoch",
        type=["json", "csv", "geojson"],
        accept_multiple_files=True  # 允许选择多个文件
    )

    # 🌟 核心改进 2：通过循环逐个处理用户上传的所有文件
    if uploaded_files:  # 如果列表不为空
        for uploaded_file in uploaded_files:
            file_id = uploaded_file.name

            # 检查当前文件是否已经处理过，避免重复触发大模型
            if file_id not in st.session_state.processed_files:
                # 为每个文件单独展示独立的加载状态
                with st.spinner(f" GeoGPT analysiert: {file_id}..."):
                    try:
                        # 1. 基础解析
                        parsed_data = parse_uploaded_file(uploaded_file)
                        raw_data = parsed_data["data"]

                        # 2. 智能化数据切片
                        if isinstance(raw_data, list):
                            sample_str = json.dumps(raw_data[:3], ensure_ascii=False)
                        elif isinstance(raw_data, dict) and "features" in raw_data:
                            sample_features = raw_data.get("features", [])[:2]
                            sample_str = json.dumps({"type": "FeatureCollection", "features": sample_features}, ensure_ascii=False)
                        else:
                            sample_str = json.dumps(raw_data, ensure_ascii=False)[:1000]

                        # 3. 调用 LLM 进行智能化盲审分析
                        llm_analysis = smart_analyze_geodata(uploaded_file.name, sample_str)
                        
                        detected_type = llm_analysis.get("detected_type", parsed_data["type"])
                        detected_fields = llm_analysis.get("data_fields", [])
                        recommended_name = llm_analysis.get("recommended_layer_name", uploaded_file.name)

                        # 4. 组装图层对象
                        current_layer = {
                            "name": recommended_name,
                            "type": detected_type,
                            "data": raw_data,
                            "style": copy.deepcopy(st.session_state.map_style),
                            "data_fields": detected_fields
                        }

                        # 5. 更新全局状态
                        st.session_state.layers.append(current_layer)
                        
                        if detected_type == "polygon" and detected_fields:
                            st.session_state.available_choropleth_fields = detected_fields
                            st.session_state.latest_choropleth_source_layer = len(st.session_state.layers) - 1
                        elif detected_type == "points" and detected_fields:
                            st.session_state.available_heatmap_fields = detected_fields
                            st.session_state.latest_heatmap_source_layer = len(st.session_state.layers) - 1

                        st.session_state.processed_files.add(file_id)
                        st.session_state.map_key += 1  # 物理刷新地图组件
                        
                        st.success(f" Erfolgreich! Layer hinzugefügt: **{recommended_name}** ({detected_type})")

                    except Exception as e:
                        st.error(f"Fehler bei der intelligenten Analyse von {file_id}: {e}")


    st.markdown("### List der hochgeladenen Dateien")

    if st.session_state.layers:
        for i, layer in enumerate(st.session_state.layers):
            col_name, col_delete = st.columns([4, 1])

            with col_name:
                st.write(f"{i + 1}. {layer['name']} ({layer['type']})")

            with col_delete:
                if st.button("✕", key=f"delete_layer_{i}"):
                    deleted_name = st.session_state.layers[i]["name"]
                    st.session_state.layers.pop(i)
                    st.session_state.processed_files.discard(deleted_name)
                    st.session_state.map_key += 1
                    st.rerun()
    else:
        st.write("Noch keine Datei hochgeladen.")

    st.markdown("---")
    st.markdown("### Chatverlauf")

    for msg in st.session_state.messages:
        st.chat_message(msg["role"]).write(msg["content"])
    if st.session_state.get("show_export_buttons", False):
        col_out1, col_out2, col_out3,col_out4 = st.columns([1, 1, 1, 2])  
        with open(st.session_state.export_files["html"], "rb") as f:
            html_bytes = f.read()
        with col_out1:
            st.download_button("als HTML", html_bytes, "map.html", "text/html")

        with open(st.session_state.export_files["png"], "rb") as f:
            png_bytes = f.read()
        with col_out2:
            st.download_button("als PNG", png_bytes, "map.png", "image/png")

        with open(st.session_state.export_files["pdf"], "rb") as f:
            pdf_bytes = f.read()
        with col_out3:
            st.download_button("als PDF", pdf_bytes, "map.pdf", "application/pdf")

with col2:
    st.subheader("Interaktive Karte")
    m = create_layered_map(st.session_state.show_layers, st.session_state.view_updates)
    #  核心修复：通过绑定变化的 map_key，强行摧毁旧 Folium 缓存，迫使地图根据最新样式和完美自适应边界重绘！


    #zheng模拟添加标题
    if st.session_state.Karte_Titel:
        Titel_hinzufügen(m,st.session_state.Karte_Titel)

    st_folium(m, width="100%", height=600, key=f"map_instance_{st.session_state.map_key}")

    # %%
    # ==========================================
    # zheng保存地图
    # ==========================================

    col2_1, col2_2, col2_3= st.columns([1, 1, 3])
    with col2_1:
        if st.button("Karte exportieren", icon="⬇️"):
            with col2_2:
                with st.popover("Dateityp auswählen"):
                    html_path = "map.html"
                    png_path = "map.png"
                    pdf_path = "map.pdf"
                    m.save(html_path)
                    html_to_png(html_path,png_path)
                    png_to_pdf(png_path,pdf_path)
                    with open(html_path, "rb") as f:
                        html_bytes = f.read()
                    st.download_button("als HTML", html_bytes, "map.html", "text/html")
                    with open(png_path, "rb") as f:
                        png_bytes = f.read()
                    st.download_button("als PNG", png_bytes, "map.png", "image/png")
                    with open(pdf_path, "rb") as f:
                        pdf_bytes = f.read()
                    st.download_button("als PDF", pdf_bytes, "map.pdf", "application/pdf")

# %%
# ==========================================
# BLOCK 4: UNIFIED INTELLIGENT INPUT GATEWAY
# ==========================================
from backend import parse_intent
from backend import get_selected_layer_name
from backend import bestimmen_Kartentyp
from backend import set_selected_layer_name
from backend import get_map_style
from backend import verwalten_layers
from backend import verstehen_Geodatei
from backend import smart_analyze_geodata

user_query = st.chat_input("Was möchtest du mit der Karte machen?")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})

    with st.spinner("GeoGPT analysiert und führt Befehl aus..."):
        #initiieren
        st.session_state.show_export_buttons = False
        st.session_state.export_files = {}
        
        result = parse_intent(user_query)
        layers=st.session_state.layers
        show_layers=st.session_state.show_layers
        if result["tool"]=="create_map":
            selected_layer_name = get_selected_layer_name(user_query,layers)
            layer = next((l for l in layers if l["name"] == selected_layer_name), layers[-1])
            kartentype=bestimmen_Kartentyp(user_query,layer)
            st.session_state.show_layers.append({
                        "name": f"{layer["name"]}_{kartentype["type"]}_{kartentype["data_field"]}" ,
                        "type": kartentype["type"],
                        "data": layer["data"],
                        "style": layer["style"],
                        "data_fields": layer["data_fields"],
                        "data_field": kartentype["data_field"]
                    })
            output="Erfolgreich erstellen die Karte."
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })

        elif result["tool"]=="export_map":
            html_path = "map.html"
            png_path = "map.png"
            pdf_path = "map.pdf"
            st.session_state.show_export_buttons = True
            st.session_state.export_files = {
                "html": html_path,
                "png": png_path,
                "pdf": pdf_path
            }

            m.save(html_path)
            html_to_png(html_path,png_path)
            png_to_pdf(png_path,pdf_path)
            output="Die Karte wurde erfolgreich exportiert. Bitte laden Sie sie über die Buttons oben herunter."
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })
            
        elif result["tool"]=="set_title":
            selected_layer_name = get_selected_layer_name(user_query,show_layers)
            layer = next((l for l in show_layers if l["name"] == selected_layer_name), show_layers[-1])
            Karte_Titel=set_selected_layer_name(layer)
            st.session_state.Karte_Titel=Karte_Titel
            output=f"""Der Titel "{Karte_Titel}" wird gegeben."""
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })   

        elif result["tool"]=="Kartenstil_anpassen": 
            selected_layer_name = get_selected_layer_name(user_query,show_layers)
            layer = next((l for l in show_layers if l["name"] == selected_layer_name), show_layers[-1])
            layer["style"]=get_map_style(user_query,layer)

            output="Der Kartenstil wurde erfolgreich angepasst."
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })

        elif result["tool"]=="Layer_verwalten": 
            neue_layers_list = verwalten_layers(user_query,show_layers)
            new_layers = []
            layer_dict = {l["name"]: l for l in show_layers}
            for layer in neue_layers_list:
                original_layer = copy.deepcopy(layer_dict[layer["source"]])
                original_layer["name"] = layer["name"]
                new_layers.append(original_layer)
  
            st.session_state.show_layers=new_layers
            output="Die Layers wurden erneut."
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })
        
        elif result["tool"]=="Geodatei_analysieren": 
            selected_layer_name = get_selected_layer_name(user_query,layers)
            layer = next((l for l in layers if l["name"] == selected_layer_name), layers[-1])
            output=verstehen_Geodatei(user_query,layer)
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })


        elif result["tool"]=="error":
            output="unknown intent!"
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })
            
        else:
            output="error!"
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })            
        st.session_state.map_key += 1
    st.rerun()
