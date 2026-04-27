import streamlit as st

st.title("Kartenerstellung")

# 初始化聊天历史
if "messages" not in st.session_state:
    st.session_state.messages = []

# 显示聊天历史
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 聊天输入
if prompt := st.chat_input("请输入内容"):
    # 添加用户消息到历史
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # 显示用户消息
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # 模拟 AI 响应（这里可以替换为实际的 AI 调用）
    response = f"你说了: {prompt}"
    
    # 添加 AI 响应到历史
    st.session_state.messages.append({"role": "assistant", "content": response})
    
    # 显示 AI 响应
    with st.chat_message("assistant"):
        st.markdown(response)
