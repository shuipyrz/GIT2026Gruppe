import streamlit as st
import folium
from streamlit_folium import st_folium

st.title("Chat für Kartenerstellung")

# 1. 创建 Folium 地图对象 (以德累斯顿为例)
Location_DD = folium.Map(location=[51.0504, 13.7373], zoom_start=12)

# 2. 添加一个标记点
folium.Marker(
    [51.0504, 13.7373], 
    popup="Dresden City Center", 
    tooltip="点击查看详情"
).add_to(Location_DD)

# 3. 在 Streamlit 中显示地图
st_folium(Location_DD, width=700, height=500)
