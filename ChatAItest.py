import streamlit as st

st.title("Kartenerstellung")

# 聊天输入框
prompt = st.chat_input("请输入你的内容")

if prompt:
    # 显示用户输入的内容
    with st.chat_message("human"):
        st.write(prompt)
    
    # 显示 AI 的回复内容
    with st.chat_message("ai"):
        st.write(f"你刚才说的是：{prompt}。我正在准备地图生成代码...")