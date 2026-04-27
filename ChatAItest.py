import streamlit as st
from langchain_aws import ChatBedrock

llm = ChatBedrock(
    model_id="us.amazon.nova-micro-v1:0"  # 这里使用最简单的文生文模型
)

st.title("Kartenerstellung")

# 聊天输入框
prompt = st.chat_input("请输入你的内容")

if prompt:
    # 显示用户输入的内容
    user_message = st.chat_message("human")
    user_message.write(prompt)

    ai_message = st.chat_message("ai")
    ai_reply = llm.invoke(prompt)  # AI返回内容，并且保存为变量，再写入到AI对应的消息框内
    # 从 AIMessage 对象中提取文本内容
    if hasattr(ai_reply, 'content'):
        ai_message.write(ai_reply.content)
    else:
        ai_message.write(str(ai_reply))
       