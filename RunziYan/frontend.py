import streamlit as st
import folium
from streamlit_folium import st_folium
from backend import agent_app

st.set_page_config(layout="wide", page_title="GeoGPT DeepSeek")
st.title("🗺️ GeoGPT Kartenerstellung")

# 初始化 Session
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_geojson" not in st.session_state:
    st.session_state.current_geojson = None

# 侧边栏
with st.sidebar:
    if st.button("Chat löschen"):
        st.session_state.messages = []
        st.session_state.current_geojson = None
        st.rerun()

# 布局
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Chat")
    for msg in st.session_state.messages:
        st.chat_message(msg["role"]).write(msg["content"])

with col2:
    st.subheader("Karte")
    # 初始地图
    m = folium.Map(location=[51.0504, 13.7373], zoom_start=7)
    
    if st.session_state.current_geojson:
        try:
            features = st.session_state.current_geojson.get("features", [])
            if features:
                # 动态获取字段名（支持多语言）
                available_fields = list(features[0]["properties"].keys())
                display_field = "city" if "city" in available_fields else available_fields[0]
                
                # 添加数据
                fg = folium.FeatureGroup(name="AI_Results")
                geo_obj = folium.GeoJson(
                    st.session_state.current_geojson,
                    tooltip=folium.GeoJsonTooltip(fields=[display_field])
                ).add_to(fg)
                fg.add_to(m)
                
                # --- 核心改进：自动调整缩放以包含所有点 ---
                m.fit_bounds(geo_obj.get_bounds())
                
        except Exception as e:
            st.error(f"Mapping Fehler: {e}")
    
    st_folium(m, width="100%", height=600, key="map")

# 输入框处理
user_query = st.chat_input("Wohin möchtest du reisen?")

if user_query:
    # 1. 记录用户输入
    st.session_state.messages.append({"role": "user", "content": user_query})
    
    with st.spinner("DeepSeek generiert Daten..."):
        # --- 修复点：确保传入的是 user_query 而不是未定义的 prompt ---
        result = agent_app.invoke({
            "input_text": user_query,
            "geojson_data": None,
            "error_message": ""
        })
        
        if result.get("geojson_data") and not result.get("error_message"):
            st.session_state.current_geojson = result["geojson_data"]
            st.session_state.messages.append({"role": "assistant", "content": "Karte wurde aktualisiert!"})
        else:
            err = result.get("error_message", "Unbekannter Fehler")
            st.error(f"Fehler: {err}")
            st.session_state.messages.append({"role": "assistant", "content": f"Fehler: {err}"})
    
    # 运行完毕后刷新页面显示新结果
    st.rerun()