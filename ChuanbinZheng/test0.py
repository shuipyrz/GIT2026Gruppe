from dotenv import load_dotenv
from pathlib import Path

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
import os

import folium
from streamlit_folium import st_folium
import streamlit as st

import time
import json

# setup environment variables
# load_dotenv()
try:
    secrets = st.secrets
    if secrets:
        for key in secrets:
            os.environ[key] = str(secrets[key])
except Exception:
    env_path = r"C:\Users\28472\Desktop\ney way\Neues Leben\TU Dresden\Kurse\KI-basierte Geoinformationsdienste\Projekt\env1.env"
    load_dotenv(Path(env_path)) 
# if hasattr(st,"secrets") and dict(st.secrets):
#     for key in st.secrets:
#         os.environ[key] = str(st.secrets[key])
# else:
#     env_path = r"C:\Users\28472\Desktop\ney way\Neues Leben\TU Dresden\Kurse\KI-basierte Geoinformationsdienste\Projekt\env1.env"
#     load_dotenv(Path(env_path))  

# definieren einen llm-Model
llm = ChatOpenAI(
    model="deepseek-chat",
    # try:
    # api_key=st.secrets["DEEPSEEK_API_KEY"],
    # except Exception:
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

def parse_request(text: str):
    """
    Wandelt Nutzertext in GIS-JSON um.
    """

    system_prompt = """
    Du bist ein GIS-Assistent.

    Gib IMMER nur gültiges JSON zurück.

    Erlaubtes Format:

    1. POI (Punkte):
    {
    "type": "poi",
    "data": [
        {"name": "string", "lat": float, "lon": float}
    ]
    }

    2. ROUTE (Linien):
    {
    "type": "route",
    "data": {
        "points": [
        {"lat": float, "lon": float}
        ]
    }
    }

    3. AREA (Flächen):
    {
    "type": "area",
    "data": {
        "polygon": [
        [lat, lon]
        ]
    }
    }

    Regeln:
    - KEINE anderen Felder erlauben
    - KEIN Text außerhalb JSON
    """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text}
    ])

    content = response.content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"error": "invalid JSON", "raw": content}

# mit der erstellung anfangen
st.title("Karten erstellen")

user_input=st.text_area("Bitte beschreiben einfach Ihre Anforderungen:",height=200)

hochladen_file=st.file_uploader("Bitte laden Ihre Daten hoch (optional):"
                                ,type=["csv"])

if "map_created" not in st.session_state:
    st.session_state.map_created=False
    st.session_state.map=None

# if st.button("Karten erstellen"):
#     st.session_state.map_generated=True
if st.button("Karten erstellen"):
    placeholder1 = st.empty()
    if not user_input:
        placeholder2 = st.empty()
        placeholder2.write("Bitte importieren unbedingt etwas!")
        time.sleep(1)
        placeholder2.empty()

    with st.spinner("In Bearbeitung..."):    
        st.session_state.data = parse_request(user_input)
        Daten=st.session_state.data
        points=[[point["lat"],point["lon"]] for point in Daten["data"]] 
        m = folium.Map()
        for point in Daten["data"]:
            folium.CircleMarker(
                location=[point["lat"], point["lon"]],
                radius=12,
                color="red",
                fill=True,
                fill_color="orange",
                fill_opacity=0.7,
                tooltip=folium.Tooltip(point["name"], permanent=False),
                popup=point["name"]
            ).add_to(m)
        m.fit_bounds(points) 

        st.session_state.map_created=True
        st.session_state.map=m

        time.sleep(1)
        # st_folium(m, width=700, height=600)

    placeholder1.success("Die Karte wurde erfolgreich erstellt!")
    time.sleep(1)
    placeholder1.empty()

if st.session_state.map_created:
    st_folium(st.session_state.map, width=700, height=600)
