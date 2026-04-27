import streamlit as st

st.title("Kartenerstellung")

# 聊天输入框
prompt = st.chat_input("请输入你的内容")

if prompt:
    # 显示用户输入的内容
    user_message = st.chat_message("human")
    user_message.write(prompt)

    ai_message = st.chat_message("ai")
    ai_message.write("我是AI")
       