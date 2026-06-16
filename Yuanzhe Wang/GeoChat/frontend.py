# %%
# ==========================================
# BLOCK 1: FRONTEND INITIALIZATION & SESSION MANAGEMENT
# ==========================================
import streamlit as st
import folium
import json
import copy
from streamlit_folium import st_folium
from backend import agent_app
from backend import parse_uploaded_file
from backend import create_layered_map
from backend import DEFAULT_MAP_STYLE
from backend import is_polygon_geojson
from backend import is_line_geojson
from backend import get_numeric_geojson_fields
from backend import Titel_hinzufügen
from backend import html_to_png
from backend import png_to_pdf

st.set_page_config(layout="wide", page_title="GeoGPT Intelligent Agent")
st.title("Intelligenter Chatbot für Kartenerstellung")

if "messages" not in st.session_state: st.session_state.messages = []
if "current_geojson" not in st.session_state: st.session_state.current_geojson = None
if "layers" not in st.session_state: st.session_state.layers = []
if "processed_files" not in st.session_state: st.session_state.processed_files = set()
if "map_style" not in st.session_state: st.session_state.map_style = DEFAULT_MAP_STYLE.copy()
if "view_updates" not in st.session_state: st.session_state.view_updates = {}
#  
if "map_key" not in st.session_state: st.session_state.map_key = 0
if "available_choropleth_fields" not in st.session_state:st.session_state.available_choropleth_fields = []
#  
if "show_export_buttons" not in st.session_state: st.session_state.show_export_buttons = False
if "export_files" not in st.session_state: st.session_state.export_files = {}

# %%
# ==========================================
# BLOCK 2: SIDEBAR CONTROL PANEL
# ==========================================
with st.sidebar:
    st.markdown("### Kontrollzentrum")
    if st.button("Chat & Layer löschen"):
        st.session_state.messages = []
        st.session_state.current_geojson = None
        st.session_state.layers = []
        st.session_state.processed_files = set()
        st.session_state.map_style = DEFAULT_MAP_STYLE.copy()
        st.session_state.view_updates = {}
        st.session_state.available_choropleth_fields = []
        st.session_state.map_key += 1
        st.rerun()

# %%
# ==========================================
# BLOCK 3: LAYOUT SYSTEM (COLUMNS)
# ==========================================
col1, col2 = st.columns([1, 1.5])

with col1:
    st.subheader("Daten & Chat")
    uploaded_file = st.file_uploader(
        "Bitte laden Sie Ihre Daten hoch",
        type=["json", "csv", "geojson"]
    )

    if uploaded_file is not None:
        file_id = uploaded_file.name

        if file_id not in st.session_state.processed_files:
            try:
                parsed_data = parse_uploaded_file(uploaded_file)

                current_layer={
                    "name": uploaded_file.name,
                    "type": parsed_data["type"],
                    "data": parsed_data["data"],
                    "style": copy.deepcopy(st.session_state.map_style),
                    "data_fields": ""
                }

                          

                # Choropleth-Felder automatisch erkennen
                if parsed_data["type"] == "polygon":
                    geojson_data = parsed_data["data"]

                    choropleth_fields = get_numeric_geojson_fields(geojson_data)

                    if choropleth_fields:
                        current_layer["data_fields"]=choropleth_fields
                        st.session_state.layers.append(current_layer)      
                        st.session_state.available_choropleth_fields = choropleth_fields
                        st.session_state.latest_choropleth_source_layer = len(st.session_state.layers) - 1                          
                        st.success(
                            f"{len(choropleth_fields)} Choropleth-Felder erkannt."
                        )

                elif parsed_data["type"] == "line":
                    st.session_state.layers.append(current_layer)      
                    st.success(
                        "Line-Daten erkannt."
                    )

                # Heatmap-Felder automatisch erkennen
                elif parsed_data["type"] == "points":
                    available_fields = []

                    for point in parsed_data["data"]:
                        for key, value in point.get("properties", {}).items():
                            if isinstance(value, (int, float)):
                                available_fields.append(key)

                    available_fields = list(set(available_fields))

                    if available_fields:
                        current_layer["data_fields"]=available_fields    
                        st.session_state.layers.append(current_layer)                          
                        st.session_state.available_heatmap_fields = available_fields
                        st.session_state.latest_heatmap_source_layer = len(st.session_state.layers) - 1                       



                st.session_state.processed_files.add(file_id)
                st.session_state.map_key += 1
                st.success(f"Layer hinzugefügt: {uploaded_file.name}")


            except Exception as e:
                st.error(f"Fehler: {e}")

    # ======================
    # Heatmap Auswahl
    # ======================
    if "available_heatmap_fields" in st.session_state:

        st.markdown("---")
        st.markdown("### Heatmap")

        selected_field = st.selectbox(
            "Heatmap-Feld auswählen:",
            st.session_state.available_heatmap_fields
        )

        if st.button("Heatmap erstellen"):

            source_index = st.session_state.get("latest_heatmap_source_layer", None)

            if source_index is not None and source_index < len(st.session_state.layers):

                source_layer = st.session_state.layers[source_index]

                st.session_state.layers.append({
                    "name": f"Heatmap_{selected_field}",
                    "type": "heatmap",
                    "data": source_layer["data"],
                    "data_field": selected_field
                })

                st.session_state.map_key += 1
                st.rerun()

    # ======================
    # Choropleth Auswahl
    # ======================
    if "available_choropleth_fields" in st.session_state and st.session_state.available_choropleth_fields:

        st.markdown("---")
        st.markdown("### Choropleth")

        selected_choropleth_field = st.selectbox(
            "Choropleth-Feld auswählen:",
            st.session_state.available_choropleth_fields
        )

        if st.button("Choropleth erstellen"):

            source_index = st.session_state.get("latest_choropleth_source_layer", None)

            if source_index is not None and source_index < len(st.session_state.layers):

                source_layer = st.session_state.layers[source_index]

                st.session_state.layers.append({
                    "name": f"Choropleth_{selected_choropleth_field}",
                    "type": "choropleth",
                    "data": source_layer["data"],
                    "data_field": selected_choropleth_field
                })

                st.session_state.map_key += 1
                st.rerun()

    st.markdown("### Aktive Kartenebenen (Layers)")

    if st.session_state.layers:
        for i, layer in enumerate(st.session_state.layers):
            col_name, col_delete = st.columns([4, 1])

            with col_name:
                st.write(f"{i + 1}. {layer['name']} ({layer['type']})")

            with col_delete:
                if st.button("✕", key=f"delete_layer_{i}"):
                    deleted_name = st.session_state.layers[i]["name"]
                    st.session_state.layers.pop(i)
                    st.session_state.processed_files.discard(deleted_name)
                    st.session_state.map_key += 1
                    st.rerun()
    else:
        st.write("Noch keine Datei hochgeladen.")

    st.markdown("---")
    st.markdown("### Chatverlauf")

    for msg in st.session_state.messages:
        st.chat_message(msg["role"]).write(msg["content"])
    if st.session_state.get("show_export_buttons", False):
        col_out1, col_out2, col_out3,col_out4 = st.columns([1, 1, 1, 2])  
        with open(st.session_state.export_files["html"], "rb") as f:
            html_bytes = f.read()
        with col_out1:
            st.download_button("als HTML", html_bytes, "map.html", "text/html")

        with open(st.session_state.export_files["png"], "rb") as f:
            png_bytes = f.read()
        with col_out2:
            st.download_button("als PNG", png_bytes, "map.png", "image/png")

        with open(st.session_state.export_files["pdf"], "rb") as f:
            pdf_bytes = f.read()
        with col_out3:
            st.download_button("als PDF", pdf_bytes, "map.pdf", "application/pdf")

with col2:
    st.subheader("Interaktive Karte")
    m = create_layered_map(st.session_state.layers, st.session_state.view_updates)
    


    
    # Titel_hinzufügen(m,"Meine Karte")
    st_folium(m, width="100%", height=600, key=f"map_instance_{st.session_state.map_key}")

    # %%
    # ==========================================
    # zheng
    # ==========================================

    col2_1, col2_2, col2_3= st.columns([1, 1, 3])
    with col2_1:
        if st.button("Karte exportieren", icon="⬇️"):
            with col2_2:
                with st.popover("Dateityp auswählen"):
                    html_path = "map.html"
                    png_path = "map.png"
                    pdf_path = "map.pdf"
                    m.save(html_path)
                    html_to_png(html_path,png_path)
                    png_to_pdf(png_path,pdf_path)
                    with open(html_path, "rb") as f:
                        html_bytes = f.read()
                    st.download_button("als HTML", html_bytes, "map.html", "text/html")
                    with open(png_path, "rb") as f:
                        png_bytes = f.read()
                    st.download_button("als PNG", png_bytes, "map.png", "image/png")
                    with open(pdf_path, "rb") as f:
                        pdf_bytes = f.read()
                    st.download_button("als PDF", pdf_bytes, "map.pdf", "application/pdf")

# %%
# ==========================================
# BLOCK 4: UNIFIED INTELLIGENT INPUT GATEWAY
# ==========================================
from backend import parse_intent
from backend import get_selected_layer_name
from backend import bestimmen_Kartentyp


user_query = st.chat_input("Was möchtest du mit der Karte machen?")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})

    with st.spinner("GeoGPT analysiert und führt Befehl aus..."):
        #initiieren
        st.session_state.show_export_buttons = False
        st.session_state.export_files = {}
        
        result = parse_intent(user_query)
        layers=st.session_state.layers
        if result["tool"]=="create_map":
            selected_layer_name = get_selected_layer_name(user_query,layers)
            layer = next((l for l in layers if l["name"] == selected_layer_name), layers[-1])
            kartentype=bestimmen_Kartentyp(user_query,layer)
            st.session_state.layers.append({
                        "name": f"{layer["name"]}_{kartentype["type"]}_{kartentype["data_field"]}" ,
                        "type": kartentype["type"],
                        "data": layer["data"],
                        "style": layer["style"],
                        "data_fields": layer["data_fields"],
                        "data_field": kartentype["data_field"]
                    })
            output="Erfolgreich erstellen die Karte."
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })

        elif result["tool"]=="export_map":
            html_path = "map.html"
            png_path = "map.png"
            pdf_path = "map.pdf"
            st.session_state.show_export_buttons = True
            st.session_state.export_files = {
                "html": html_path,
                "png": png_path,
                "pdf": pdf_path
            }

            m.save(html_path)
            html_to_png(html_path,png_path)
            png_to_pdf(png_path,pdf_path)
            output="Die Karte wurde erfolgreich exportiert. Bitte laden Sie sie über die Buttons oben herunter."
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })
            
        elif result["tool"]=="error":
            output="unknown intent!"
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })
            
        else:
            output="error!"
            st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })            
        st.session_state.map_key += 1
    st.rerun()

    #     result = agent_app.invoke({
    #         "input_text": user_query,
    #         "chat_history": st.session_state.messages,
    #         "intent_type": "",
    #         "geojson_data": st.session_state.current_geojson,
    #         "view_updates": {},
    #         "map_style": st.session_state.map_style,
    #         "error_message": ""
    #     })

    #     if not result.get("error_message"):
    #         st.session_state.map_style = result.get("map_style", DEFAULT_MAP_STYLE)
    #         st.session_state.view_updates = result.get("view_updates", {})
            
    #         if result.get("intent_type") == "generate_data" and result.get("geojson_data"):
    #             st.session_state.current_geojson = result["geojson_data"]
    #             st.session_state.layers.append({
    #                 "name": f"AI_Generated_{len(st.session_state.layers)+1}",
    #                 "type": "geojson",
    #                 "data": result["geojson_data"],
    #                 "style": copy.deepcopy(st.session_state.map_style)
    #             })
    #             st.session_state.messages.append({
    #                 "role": "assistant",
    #                 "content": "Neue Geodaten wurden generiert und die Ansicht optimal angepasst!"
    #             })
    #         else:
    #             #  核心修复：样式更新精准指向列表末尾最新的独立图层快照
    #             if st.session_state.layers:
    #                 st.session_state.layers[-1]["style"] = copy.deepcopy(st.session_state.map_style)
    #             st.session_state.messages.append({
    #                 "role": "assistant",
    #                 "content": "Kartenansicht aktualisiert und Stil angewendet!"
    #             })
            
    #         #  强制递增渲染键，告诉 Streamlit 这一轮数据流变了，必须重新绘制图层颜色！
    #         st.session_state.map_key += 1
    #     else:
    #         st.session_state.messages.append({"role": "assistant", "content": f"Fehler: {result.get('error_message')}"})

    # st.rerun()
