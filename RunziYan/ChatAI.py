import os

import openai
import streamlit as st

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    st.error("请先设置环境变量 OPENAI_API_KEY，然后重新运行应用。")
    st.stop()

openai.api_key = openai_api_key

st.title("📍 Kartenerstellung - GeoGPT")
st.write("Szenario 4: KI-basierte Geoinformationsdienste")

# 聊天输入框
prompt = st.chat_input("请输入地图描述")

if prompt:
    with st.chat_message("human"):
        st.write(prompt)

    with st.chat_message("ai"):
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant for geographic information and map creation."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
            )
            ai_content = response.choices[0].message.content.strip()
            st.write(ai_content)
        except Exception as e:
            st.error(f"调用 OpenAI 出错: {e}")