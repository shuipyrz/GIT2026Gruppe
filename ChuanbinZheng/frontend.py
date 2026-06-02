import streamlit as st
import folium
from streamlit_folium import st_folium
from backend import agent_app
from backend import parse_uploaded_file
from backend import create_layered_map

st.set_page_config(layout="wide", page_title="GeoGPT DeepSeek")
st.title("Chatbot für Kartenerstellung")

# 初始化 Session
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_geojson" not in st.session_state:
    st.session_state.current_geojson = None
if "layers" not in st.session_state:
    st.session_state.layers = []
if "processed_files" not in st.session_state:
    st.session_state.processed_files = set()

# 侧边栏
with st.sidebar:
    if st.button("Chat löschen"):
        st.session_state.messages = []
        st.session_state.current_geojson = None
        st.session_state.layers = []
        st.session_state.processed_files = set()
        st.rerun()

# 布局
col1, col2 = st.columns([1, 1.5])

with col1:
    st.subheader("Daten & Chat")

    uploaded_file = st.file_uploader(
        "Bitte laden Sie Ihre Daten hoch",
        type=["json", "csv", "geojson"]
    )

    if uploaded_file is not None:
        file_id = uploaded_file.name

        if file_id not in st.session_state.processed_files:
            try:
                parsed_data = parse_uploaded_file(uploaded_file)

                layer = {
                    "name": uploaded_file.name,
                    "type": parsed_data["type"],
                    "data": parsed_data["data"]
                }

                st.session_state.layers.append(layer)
                st.session_state.processed_files.add(file_id)
                st.session_state.current_geojson = None

                st.success(f"Layer hinzugefügt: {uploaded_file.name}")

            except Exception as e:
                st.error(f"Fehler beim Lesen der Datei: {e}")

    st.markdown("### Hochgeladene Layer")

    if st.session_state.layers:
        for i, layer in enumerate(st.session_state.layers):
            col_name, col_delete = st.columns([4, 1])

            with col_name:
                st.write(f"{i + 1}. {layer['name']}")

            with col_delete:
                if st.button("✕", key=f"delete_layer_{i}"):
                    deleted_name = st.session_state.layers[i]["name"]
                    st.session_state.layers.pop(i)
                    st.session_state.processed_files.discard(deleted_name)
                    st.rerun()
    else:
        st.write("Noch keine Datei hochgeladen.")

    st.markdown("### Chat")

    for msg in st.session_state.messages:
        st.chat_message(msg["role"]).write(msg["content"])


with col2:
    st.subheader("Karte")

    if st.session_state.layers:
        m = create_layered_map(st.session_state.layers)

    else:
        m = folium.Map(location=[51.0504, 13.7373], zoom_start=7)

        if st.session_state.current_geojson:
            try:
                features = st.session_state.current_geojson.get("features", [])

                if features:
                    fg = folium.FeatureGroup(name="AI_Results")

                    geo_obj = folium.GeoJson(
                        st.session_state.current_geojson,
                        tooltip=folium.GeoJsonTooltip(
                            fields=list(features[0]["properties"].keys())[:3]
                        )
                    ).add_to(fg)

                    fg.add_to(m)
                    m.fit_bounds(geo_obj.get_bounds())

            except Exception as e:
                st.error(f"Mapping Fehler: {e}")

        folium.LayerControl().add_to(m)

    st_folium(m, width="100%", height=600, key="map")

# 输入框处理
user_query = st.chat_input("Wohin möchtest du reisen?")

if user_query:
    # 1. 记录用户输入
    st.session_state.messages.append({
        "role": "user",
        "content": user_query
    })

    with st.spinner("DeepSeek generiert Daten..."):
        result = agent_app.invoke({
            "input_text": user_query,
            "geojson_data": None,
            "error_message": ""
        })

        if result.get("geojson_data") and not result.get("error_message"):
            # 保存 AI 生成的 GeoJSON
            st.session_state.current_geojson = result["geojson_data"]

            # 清空已上传的图层
            st.session_state.layers = []

            st.session_state.messages.append({
                "role": "assistant",
                "content": "Karte wurde aktualisiert!"
            })

        else:
            err = result.get("error_message", "Unbekannter Fehler")
            st.error(f"Fehler: {err}")

            st.session_state.messages.append({
                "role": "assistant",
                "content": f"Fehler: {err}"
            })

    # 运行完毕后刷新页面显示新结果
    st.rerun()
    
