import streamlit as st

st.title("我的第一个 Streamlit 应用")
st.write("你好，这个页面已经运行成功了！")

name = st.text_input("请输入你的名字")
st.write("欢迎你：", name)