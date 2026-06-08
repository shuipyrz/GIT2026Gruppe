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

st.set_page_config(layout="wide", page_title="GeoGPT Intelligent Agent")
st.title("Intelligenter Chatbot für Kartenerstellung")

if "messages" not in st.session_state: st.session_state.messages = []
if "current_geojson" not in st.session_state: st.session_state.current_geojson = None
if "layers" not in st.session_state: st.session_state.layers = []
if "processed_files" not in st.session_state: st.session_state.processed_files = set()
if "map_style" not in st.session_state: st.session_state.map_style = DEFAULT_MAP_STYLE.copy()
if "view_updates" not in st.session_state: st.session_state.view_updates = {}
# 🌟 新增地图渲染键，用于物理强制刷新视图组件
if "map_key" not in st.session_state: st.session_state.map_key = 0

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
        st.session_state.map_key += 1
        st.rerun()

# %%
# ==========================================
# BLOCK 3: LAYOUT SYSTEM (COLUMNS)
# ==========================================
col1, col2 = st.columns([1, 1.5])

with col1:
    st.subheader("Daten & Chat")
    uploaded_file = st.file_uploader("Bitte laden Sie Ihre Daten hoch", type=["json", "csv", "geojson"])

    if uploaded_file is not None:
        file_id = uploaded_file.name
        if file_id not in st.session_state.processed_files:
            try:
                parsed_data = parse_uploaded_file(uploaded_file)
                st.session_state.layers.append({
                    "name": uploaded_file.name,
                    "type": parsed_data["type"],
                    "data": parsed_data["data"],
                    "style": copy.deepcopy(st.session_state.map_style)
                })
                st.session_state.processed_files.add(file_id)
                st.session_state.map_key += 1  # 触发刷新
                st.success(f"Layer hinzugefügt: {uploaded_file.name}")
            except Exception as e: st.error(f"Fehler: {e}")

    st.markdown("### Aktive Kartenebenen (Layers)")
    if st.session_state.layers:
        for i, layer in enumerate(st.session_state.layers):
            col_name, col_delete = st.columns([4, 1])
            with col_name: st.write(f"{i + 1}. {layer['name']} ({layer['type']})")
            with col_delete:
                if st.button("✕", key=f"delete_layer_{i}"):
                    deleted_name = st.session_state.layers[i]["name"]
                    st.session_state.layers.pop(i)
                    st.session_state.processed_files.discard(deleted_name)
                    st.session_state.map_key += 1
                    st.rerun()
    else: st.write("Noch keine Datei hochgeladen.")

    st.markdown("---")
    st.markdown("### Chatverlauf")
    for msg in st.session_state.messages: st.chat_message(msg["role"]).write(msg["content"])

with col2:
    st.subheader("Interaktive Karte")
    m = create_layered_map(st.session_state.layers, st.session_state.view_updates)
    # 🌟 核心修复：通过绑定变化的 map_key，强行摧毁旧 Folium 缓存，迫使地图根据最新样式和完美自适应边界重绘！
    st_folium(m, width="100%", height=600, key=f"map_instance_{st.session_state.map_key}")

# %%
# ==========================================
# BLOCK 4: UNIFIED INTELLIGENT INPUT GATEWAY
# ==========================================
user_query = st.chat_input("Was möchtest du mit der Karte machen?")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})

    with st.spinner("GeoGPT analysiert und führt Befehl aus..."):
        result = agent_app.invoke({
            "input_text": user_query,
            "chat_history": st.session_state.messages,
            "intent_type": "",
            "geojson_data": st.session_state.current_geojson,
            "view_updates": {},
            "map_style": st.session_state.map_style,
            "error_message": ""
        })

        if not result.get("error_message"):
            st.session_state.map_style = result.get("map_style", DEFAULT_MAP_STYLE)
            st.session_state.view_updates = result.get("view_updates", {})
            
            if result.get("intent_type") == "generate_data" and result.get("geojson_data"):
                st.session_state.current_geojson = result["geojson_data"]
                st.session_state.layers.append({
                    "name": f"AI_Generated_{len(st.session_state.layers)+1}",
                    "type": "geojson",
                    "data": result["geojson_data"],
                    "style": copy.deepcopy(st.session_state.map_style)
                })
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "Neue Geodaten wurden generiert und die Ansicht optimal angepasst!"
                })
            else:
                # 🌟 核心修复：样式更新精准指向列表末尾最新的独立图层快照
                if st.session_state.layers:
                    st.session_state.layers[-1]["style"] = copy.deepcopy(st.session_state.map_style)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "Kartenansicht aktualisiert und Stil angewendet!"
                })
            
            # 🌟 强制递增渲染键，告诉 Streamlit 这一轮数据流变了，必须重新绘制图层颜色！
            st.session_state.map_key += 1
        else:
            st.session_state.messages.append({"role": "assistant", "content": f"Fehler: {result.get('error_message')}"})

    st.rerun()