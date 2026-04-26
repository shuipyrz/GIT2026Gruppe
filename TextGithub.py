import streamlit as st
import folium
from streamlit_folium import st_folium

# 设置页面标题 (Nutzen) [cite: 1459]
st.title("GeoGPT-Mapper - 演示原型")
st.write("这是一个基于自然语言的地图生成工具。")

# 模拟 GI 方面的结合点：显示德累斯顿地图 [cite: 1404]
m = folium.Map(location=[51.05, 13.73], zoom_start=12)
st_folium(m, width=700)