import streamlit as st

st.title ("Kartenerstellung")

prompt = st.chat_input("Bitte geben Sie Ihren Inhalt ein.")

if prompt:
    user_message = st.chat_message("user")
    user_message.write(prompt)

    ai_message = st.chat_message("ai")
    ai_message.write("Hallo")
