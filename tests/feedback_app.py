"""Synthetic-data entrypoint for the map zoom regression browser test."""
import json
import os
from pathlib import Path
import runpy
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from shapely.geometry import Point, box
from agent.graph import AGENT_RUNTIME_VERSION, create_agent_session
from core.session import SessionData
from core.contracts import Dataset, Layer
from tests.conftest import collection


class LocalModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if os.environ.get("FEEDBACK_WORKSPACE_LAYOUT") == "1" and messages[-1].content.startswith("Datei:"):
            name = messages[-1].content.splitlines()[0].removeprefix("Datei: ")
            return AIMessage(content=json.dumps({"recommended_layer_name": name, "detected_type": "geojson", "data_fields": ["name", "value"]}))
        if os.environ.get("FEEDBACK_LAYER_UI") == "1":
            start = max(index for index, message in enumerate(messages) if isinstance(message, HumanMessage))
            query = messages[start].content
            if not any(isinstance(message, ToolMessage) for message in messages[start + 1:]):
                if "点图层" in query:
                    return AIMessage(content="", tool_calls=[{"name": "visualize_layer_on_map",
                        "args": {"layer_name": "车站数据", "map_type": "points"}, "id": "draw", "type": "tool_call"}])
                if "删除图层2" in query:
                    return AIMessage(content="", tool_calls=[{"name": "manage_map_layer",
                        "args": {"layer_name": "图层2", "action": "remove"}, "id": "delete", "type": "tool_call"}])
            return AIMessage(content="图层操作已完成。")
        return AIMessage(content="你好，地图保持当前状态。")


if "business_session" not in st.session_state:
    session = SessionData()
    source = collection(box(9, 51, 9.04, 51.04), box(9.04, 51, 9.08, 51.04))
    stress = os.environ.get("FEEDBACK_STRESS") == "1"
    if stress:
        source = collection(*(box(9 + x * .004, 51 + y * .004,
                                 9.003 + x * .004, 51.003 + y * .004)
                              for x in range(30) for y in range(20)))
    source_id = session.add_upload("测试行政区", source)
    agent = create_agent_session(session, model_factory=LocalModel)
    agent.visualize_layer_on_map.invoke({"layer_name": "测试行政区", "map_type": "choropleth", "data_field": "value"})
    if os.environ.get("FEEDBACK_LAYER_UI") == "1" or os.environ.get("FEEDBACK_SIDEBAR_LAYOUT") == "1":
        session = SessionData()
        stations = collection(Point(13.75, 51.07))
        stations["features"][0]["properties"] = {
            "einrichtung": "Bahnhof Bischofsplatz", "einrichtungsart": "Bahnhof",
            "barrierefreiheit": "FULLY_ACCESSIBLE", "zugang_oepnv": True,
            "adressnotiz": "Bischofsplatz 6", "related_name": "Dresden",
            "related_source": "OpenStreetMap", "spatial_match_status": "matched"}
        station_id = session.add_upload("车站数据", stations)
        session.add_layer(Layer(station_id, "原始车站图层", "points"))
        boundary_id = session.add_upload("边界数据", collection(box(13.74, 51.06, 13.76, 51.08)))
        session.add_layer(Layer(boundary_id, "边界图层", "geojson"))
        if os.environ.get("FEEDBACK_SIDEBAR_LAYOUT") == "1":
            session = SessionData()
            station_id = session.add_upload("station data", collection(Point(13.75, 51.07), Point(13.751, 51.071)))
            boundary_id = session.add_upload("boundary data", collection(box(13.74, 51.06, 13.76, 51.08)))
            session.add_layer(Layer(station_id, "Baukultur_points", "points"))
            session.add_layer(Layer(station_id, "Motorradstellplätze_points", "points", visible=False))
            session.add_layer(Layer(boundary_id,
                'Bewohnerparken Altstadt_geojson — ein sehr langer Layername mit "Details" & <Nord> und weiteren Angaben',
                "geojson"))
        agent = create_agent_session(session, model_factory=LocalModel)
    if stress:
        for index in range(30):
            session.add_dataset(Dataset(f"保留的分析结果{index}", source, crs="EPSG:4326", parent_ids=(source_id,)))
        for index in range(3):
            session.add_layer(Layer(source_id, f"附加图层{index}", "geojson", style={"color": "red", "fillOpacity": .05}))
        st.session_state.messages = [
            {"role": role, "content": f"历史第{index}轮 {role}：" + "这里是已经完成的分析说明。" * 100}
            for index in range(100) for role in ("user", "assistant")]
        st.session_state.agent_msg_history = [
            HumanMessage(content="历史提问"), AIMessage(content="历史回复") ] * 12
    if os.environ.get("FEEDBACK_WORKSPACE_LAYOUT") == "1":
        for index in range(11):
            data_id = session.add_upload(f"Datensatz {index + 2} mit einem sehr langen Namen und weiteren Details", source)
            session.add_layer(Layer(data_id, f"Kartenlayer {index + 2} mit einem sehr langen Namen", "geojson", visible=index % 2 == 0))
        st.session_state.messages = [{"role": "user" if index % 2 == 0 else "assistant",
            "content": f"Historische Nachricht {index}: " + "Lange Beschreibung. " * 25} for index in range(20)]
    session.set_basemap_visibility(False)
    st.session_state.business_session = session
    st.session_state.session_agent = agent
    st.session_state.agent_runtime_version = AGENT_RUNTIME_VERSION

if os.environ.get("FEEDBACK_STALE_MODULES") == "1" and not st.session_state.get("stale_modules_injected"):
    import core.session as session_module
    from agent import graph, execution, tools
    session = st.session_state.business_session
    st.session_state.pre_migration_session_id = session.session_id
    old_members = {key: value for key, value in vars(session_module.SessionData).items()
                   if key not in {"__dict__", "__weakref__", "ensure_compatible"}}
    old_type = type("SessionData", (), old_members)
    session_module.SessionData = old_type
    session_module.__dict__.pop("SESSION_SCHEMA_VERSION", None)
    for consumer in (graph, execution, tools):
        consumer.SessionData = old_type
    graph.AGENT_RUNTIME_VERSION = 18
    st.session_state.agent_runtime_version = 18
    session.__class__ = old_type
    del session.diagnostics
    st.session_state.stale_modules_injected = True

st.session_state.feedback_runs = st.session_state.get("feedback_runs", 0) + 1
import streamlit_folium
component = streamlit_folium._component_func
arguments = {}
def trace_component(**kwargs):
    for key, value in kwargs.items():
        if callable(value):
            continue
        serialized = json.dumps(value, sort_keys=True, default=str)
        arguments[key] = {"bytes": len(serialized), "hash": hashlib.sha256(serialized.encode()).hexdigest()}
    return component(**kwargs)
streamlit_folium._component_func = trace_component
try:
    runpy.run_path(str(ROOT / "frontend.py"), run_name="__main__")
finally:
    streamlit_folium._component_func = component
snapshot = {"runs": st.session_state.feedback_runs, "layers": len(st.session_state.business_session.layers),
            "map_key": st.session_state.map_key, "runtime": st.session_state.business_session.diagnostics.snapshot(),
            "stats": st.session_state.business_session.runtime_stats(), "chat_messages": len(st.session_state.messages),
            "component_arguments": arguments, "agent_runtime": st.session_state.agent_runtime_version,
            "layer_summaries": st.session_state.business_session.layer_summaries,
            "legacy_session_preserved": (st.session_state.get("pre_migration_session_id") ==
                                         st.session_state.business_session.session_id)
                                        if st.session_state.get("stale_modules_injected") else None}
destination = Path(os.environ["FEEDBACK_BROWSER_STATE"])
destination.with_name(f"state-{snapshot['runs']}.json").write_text(json.dumps(snapshot), encoding="utf-8")
destination.with_suffix(".tmp").write_text(json.dumps(snapshot), encoding="utf-8")
destination.with_suffix(".tmp").replace(destination)
