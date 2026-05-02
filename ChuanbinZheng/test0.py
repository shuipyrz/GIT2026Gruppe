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
    Analysieren die Anforderungen der Nutzer:innen und bestimmen, welche Art von Karten Nutzer:innen erstellen möchten
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
    "data": [
    {
        "name": "string",
        "points": [
        {"lat": float, "lon": float}
        ]
    }
    ]
    }

    3. AREA (Flächen):
    {
    "type": "area",
    "data": [
    {
        "name": "string",
        "points": [
        {"lat": float, "lon": float}
        ]
    }
    ]
    }

    Regeln:
    - KEINE anderen Felder erlauben
    - KEIN Text außerhalb JSON
    - Die Ausgabe muss ein einzelnes JSON-Objekt sein (beginnend mit { und endend mit }).
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


# erstellen Punktekarte
def erstellen_karten_Punkte(Daten:dict, m:folium.Map) -> None:
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
    # zoom   
    points=[[point["lat"],point["lon"]] for point in Daten["data"]] 
    m.fit_bounds(points) 

# erstellen Routenkarte
def erstellen_karten_Routen(Daten:dict, m:folium.Map) -> None:
    Routen=Daten["data"]
    alle_Punkte=[]
    for Route in Routen:
        Points_der_Route=[[p["lat"], p["lon"]] for p in Route["points"]]
        # Points_der_Route=[Route["points"]["lat"],Route["points"]["lon"]]
        folium.PolyLine(
            Points_der_Route,
            color="blue", 
            weight=5, 
            opacity=0.8,
            tooltip=folium.Tooltip(Route["name"], permanent=False),
            popup=Route["name"]
        ).add_to(m)
        alle_Punkte.extend(Points_der_Route)
    # zoom    
    if alle_Punkte:
        m.fit_bounds(alle_Punkte) 



# erstellen Regionskarte
def erstellen_karten_Region(Daten:dict, m:folium.Map) -> None:
    Regionen=Daten["data"]
    alle_Punkte=[]
    for Region in Regionen:
        Points_des_Regions=[[p["lat"], p["lon"]] for p in Region["points"]]
        # Points_der_Route=[Route["points"]["lat"],Route["points"]["lon"]]
        folium.Polygon(
            Points_des_Regions,
            color="green", 
            fill=True,
            fill_color="green",
            fill_opacity=0.4,
            tooltip=folium.Tooltip(Region["name"], permanent=False),
            popup=Region["name"]
        ).add_to(m)
        alle_Punkte.extend(Points_des_Regions)
    # zoom    
    if alle_Punkte:
        m.fit_bounds(alle_Punkte)
    

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

        m = folium.Map()
        if Daten["type"]=="poi":
            erstellen_karten_Punkte(Daten,m)
        elif Daten["type"]=="route":
            erstellen_karten_Routen(Daten,m)
        elif Daten["type"]=="area":
            erstellen_karten_Region(Daten,m)
        else:
            placeholder3 = st.empty()
            placeholder3.write("Entschudigung, ich kann jetzt diese Art von Karten leider nicht erstellen!")
            time.sleep(5)
            placeholder3.empty()
            st.stop()

        st.session_state.map_created=True
        st.session_state.map=m

        time.sleep(1)
        # st_folium(m, width=700, height=600)

    placeholder1.success("Die Karte wurde erfolgreich erstellt!")
    time.sleep(1)
    placeholder1.empty()

if st.session_state.map_created:
    st_folium(st.session_state.map, width=700, height=600)
