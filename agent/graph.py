"""Single-agent ReAct workflow with explicit model, tools and layer access."""
from functools import partial
import math
from threading import RLock
from typing import TypedDict, Sequence, Annotated, Optional, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from core.session import SessionData
from .attributes import is_non_measure_attribute
from .llm import create_llm

AGENT_RUNTIME_VERSION = 10


def _layer_attribute_summary(layer: dict) -> str:
    """Describe real property names and sampled value types for the agent."""
    data = layer.get("data")
    if isinstance(data, dict):
        if data.get("type") == "Feature":
            features = [data]
        else:
            features = data.get("features", [])
    elif isinstance(data, list):
        features = data
    else:
        features = []
    if not isinstance(features, list):
        features = []

    samples = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            continue
        for name, value in properties.items():
            field_samples = samples.setdefault(name, [])
            if len(field_samples) < 50:
                field_samples.append(value)

    summaries = []
    for name, values in samples.items():
        if is_non_measure_attribute(name):
            summaries.append(f"{name} (Kennung/Adressnummer; kein Mengenwert)")
            continue
        present = [value for value in values if value is not None]
        numeric = bool(present)
        saw_finite_number = False
        for value in present:
            if isinstance(value, bool):
                numeric = False
                break
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                numeric = False
                break
            if not math.isfinite(number):
                continue
            saw_finite_number = True
        numeric = numeric and saw_finite_number
        summaries.append(f"{name} ({'numerisch' if numeric else 'text/other'})")
    return ", ".join(summaries) if summaries else "Keine Attribute erkannt"


def _layer_geometry_summary(layer: dict) -> str:
    """Return geometry types present in a GeoJSON-like layer."""
    data = layer.get("data")
    if isinstance(data, dict):
        features = [data] if data.get("type") == "Feature" else data.get("features", [])
    elif isinstance(data, list):
        features = data
    else:
        features = []
    types = {
        geometry_type
        for feature in features
        if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
        for geometry_type in [feature["geometry"].get("type")]
        if isinstance(geometry_type, str)
    }
    return ", ".join(sorted(types)) or "unknown"

class AgentState(TypedDict):
    """LangGraph-Zustand: add_messages verwaltet den Nachrichtenverlauf der ReAct-Schleife."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    updated_show_layers: Optional[List[dict]]


def agent_reasoner(state: AgentState, *, llm_with_tools, get_layers) -> dict:
    """Reasoning-Phase: Das LLM entscheidet anhand des Kontexts, ob es antwortet oder ein Werkzeug aufruft."""
    raw_layers, show_layers = get_layers()
    raw_summary = [f"'{l['name']}' ({l['type']}; Attribute: {_layer_attribute_summary(l)})" + (
        f" [Dataset-ID: {l['dataset_id']}]" if l.get("dataset_id") else "") for l in raw_layers]
    show_summary = [f"'{l['name']}' ({l['type']}; Attribute: {_layer_attribute_summary(l)})" + (
        (f" [data field: {l['data_field']}]" if l.get("data_field") else "") +
        (f" [Layer-ID: {l['layer_id']}, Dataset-ID: {l['dataset_id']}]" if l.get("layer_id") else ""))
        for l in show_layers]
    owner = getattr(get_layers, "__self__", None)
    session = getattr(owner, "session", None)
    managed_layers = session.layer_management_view if session is not None else show_layers
    hidden_summary = [layer["name"] for layer in managed_layers if not layer.get("visible", True)]
    map_title = session.map_title if session is not None else ""

    system_prompt = f"""
    Du bist GeoGPT, ein autonomer und professioneller GIS-Agent.
    Deine Aufgabe ist es, Nutzerbefehle über räumliche Analysen und Kartenerstellung selbstständig auszuführen.

    AKTUELLER ZUSTAND DER ANWENDUNG:
    VERBINDLICHER LIVE-KARTENSTATUS (aktuelle Anwendungssitzung):
    - Aktuell sichtbar und auf der Karte gerendert: [{', '.join(show_summary) if show_summary else 'Keine'}]
    - Nur hochgeladen, derzeit nicht zwingend angezeigt: [{', '.join(raw_summary) if raw_summary else 'Keine'}]
    Die sichtbare Liste ist die einzige Quelle für Fragen nach derzeit angezeigten oder aktiven Kartenlayern.
    Uploads, ausgeblendete/gelöschte Layer und frühere Chat-Aussagen dürfen dabei nicht als aktuell angezeigt gelten.
    - Ausgeblendete Layer (nur für Ein-/Ausblenden/Entfernen): [{', '.join(hidden_summary) if hidden_summary else 'Keine'}]
    - Aktueller Kartentitel: {map_title or 'Keiner'}

    HANDLUNGSLEITFADEN:
    1. Du darfst und sollst Werkzeuge (Tools) verwenden, um Aktionen durchzuführen.
    2. Wenn ein Befehl mehrere Schritte erfordert (z.B. 'Lade Stadt Dresden und lege einen 1000m Puffer an'), führe die Werkzeuge nacheinander aus.
    3. Bei Choropleth-Karten darfst du nur ein tatsächlich aufgeführtes, numerisches Attribut verwenden; erfinde niemals einen Feldnamen aus dem Layer- oder Dateinamen.
    4. Wurde kein Choropleth-Attribut genannt und gibt es genau ein numerisches Attribut, verwende es und nenne es in der Antwort. Gibt es mehrere, frage nach einer Auswahl, außer der Nutzer hat die Auswahl ausdrücklich dir überlassen; dann wähle ein passendes Attribut und nenne es.
    5. Bei Punktekarte und genau einem variierenden numerischen Attribut verwende dieses Feld für proportionale Symbolgrößen; bei einer Linienkarte verwende proportionale Linienstärken. Nenne das Feld in der Antwort. Ohne geeignetes numerisches Feld nutze normale Symbole.
    6. Bei Punkte- oder Linienkarte und mehreren variierenden numerischen Attributen prüfe Geometrietyp, Feldnamen und Datensatzbeschreibung. Wähle selbst das inhaltlich passendste Feld, rufe das Werkzeug mit diesem Feld erneut auf und nenne das gewählte Feld sowie weitere verfügbare numerische Felder in der Antwort.
    7. Bei einem nicht verfügbaren oder nicht numerischen Attribut erkläre den Fehler und nenne die verfügbaren numerischen Attribute. Behaupte nur dann, eine Karte erstellt zu haben, wenn das Werkzeug erfolgreich war.
    8. Gib dem Nutzer nach Beendigung aller Tool-Aufrufe eine prägnante, informative Zusammenfassung auf Deutsch.
    9. Bei Fragen zu aktuell sichtbaren Layern nenne ausschließlich Einträge aus der sichtbaren Live-Liste oben. Nutze niemals ältere Layerlisten aus dem Gesprächsverlauf.
    10. Trenne bei Stiländerungen Umrandung und Füllung. Für Choropleth ändert 'color' nur die Umrandung und 'fill_color' die Farbpalette (YlOrRd, Blues, Greens, Purples, Reds, Oranges, YlGn, YlGnBu, GnBu, PuBu, BuPu oder RdYlBu). Ordne Grün/green der Palette Greens, Blau/blue der Palette Blues, Rot/red der Palette Reds, Orange/orange der Palette Oranges, Gelb/yellow der Palette YlGn und Lila/purple der Palette Purples zu; ändere niemals beide Parameter, wenn nur einer gewünscht ist.
    11. Maßgeblich sind die aktuell registrierten Werkzeugparameter und diese Regeln, nicht frühere Aussagen im Chat. 'update_map_style' unterstützt getrennte Parameter 'color' (Umrandung) und 'fill_color' (Füllung); behaupte nicht, dass diese Änderung nicht möglich sei.
    12. Jede ausdrückliche Aufforderung, einen Layer zu entfernen, auszublenden oder einzublenden, MUSS 'manage_map_layer' aufrufen. Nicht nur verbal bestätigen. Verwende action='remove' zum Entfernen, action='hide' zum Ausblenden und action='show' zum Einblenden. Auch ausgeblendete Layer sind über ihren Namen verfügbar. Das Entfernen löscht nur den Kartenlayer, nicht die hochgeladenen Quelldaten.
    13. OpenStreetMap-Basiskarte: Sie kann ausschließlich über das Kontrollkästchen in der linken Seitenleiste ein- oder ausgeschaltet werden. Behaupte niemals, sie per Chat geändert zu haben.
    14. Bei einer ausdrücklichen Exportanfrage MUSS 'export_current_map' aufgerufen werden. Verwende format='html' für eine interaktive Karte, format='geojson' für sichtbare Vektordaten, format='png' für einen Screenshot und format='pdf' für eine druckbare Kartenansicht. HTML, PNG und PDF enthalten die derzeit sichtbaren Layer und den aktuellen Basiskartenstatus; GeoJSON enthält nur sichtbare Vektorlayer, keine Basiskarte. Bei mehreren GeoJSON-Layern entsteht ein ZIP-Archiv. Frage nach dem Format, wenn es nicht genannt wurde. Behaupte den Export erst nach erfolgreichem Werkzeugergebnis.
    15. Wenn der Nutzer ausdrücklich einen Kartentitel setzen, hinzufügen oder automatisch erzeugen lassen möchte, rufe 'set_map_title' auf. Bei einem vorgegebenen Titel übernimm den gewünschten Wortlaut exakt. Für einen automatischen Titel verwende ausschließlich Namen und tatsächlich angezeigte Attribute der aktuell sichtbaren Layer; beziehe ausgeblendete oder nur hochgeladene Daten nicht ein. Erfinde keine Inhalte. Formuliere den automatisch erzeugten Titel knapp und in der Sprache der Nutzeranfrage, wobei Layer-Eigennamen erhalten bleiben. Wenn keine Layer sichtbar sind, frage nach den gewünschten Titelinhalten. Eine reine Bitte um Titelvorschläge ohne Aufforderung zum Setzen beantwortest du, ohne den Kartentitel zu ändern.
    16. Wenn der Nutzer ausdrücklich den aktuellen Kartentitel entfernen, löschen oder zurücksetzen möchte, MUSS 'clear_map_title' aufgerufen werden. Behaupte die Entfernung erst nach erfolgreichem Werkzeugergebnis.
    """

    # Systemnachrichten entfernen und den aktuellen Systemkontext an den Anfang setzen.
    user_and_assistant_msgs = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
    full_messages = [SystemMessage(content=system_prompt)] + list(user_and_assistant_msgs)

    response = llm_with_tools.invoke(full_messages)
    return {"messages": [response]}


def should_continue(state: AgentState):
    """Bedingte Weiterleitung: Werkzeugaufruf ausführen oder die abschließende Antwort zurückgeben."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


def _is_advisory_request(message: BaseMessage) -> bool:
    """Recognize recommendation-only requests; explicit map actions stay executable."""
    if not isinstance(message, HumanMessage):
        return False
    content = message.content
    if not isinstance(content, str):
        return False
    text = content.casefold()
    action_terms = (
        "create", "draw", "show", "display", "render", "add to map", "plot",
        "erstell", "zeichn", "zeig", "darstell", "visualisier", "auf die karte",
        "\u751f\u6210", "\u7ed8\u5236", "\u663e\u793a", "\u5c55\u793a", "\u7ed8\u5236", "\u6dfb\u52a0",
    )
    advisory_terms = (
        "recommend", "recommendation", "suggest", "suggestion", "which map", "options",
        "empfehl", "vorschlag", "welche visualisierung", "welche darstellung",
        "\u63a8\u8350", "\u5efa\u8bae", "\u65b9\u6848", "\u9009\u9879",
    )
    return any(term in text for term in advisory_terms) and not any(term in text for term in action_terms)


def advisory_response(state: AgentState, *, llm, get_layers) -> dict:
    """Answer visualization advice requests without exposing mutation tools."""
    raw_layers, show_layers = get_layers()
    raw_summary = [
        f"{layer['name']} ({layer['type']}; geometries: {_layer_geometry_summary(layer)}; "
        f"attributes: {_layer_attribute_summary(layer)})"
        + (" [LATEST UPLOAD]" if index == len(raw_layers) - 1 else "")
        for index, layer in enumerate(raw_layers)
    ]
    prompt = SystemMessage(content=(
        "You are a concise GIS visualization advisor. This application supports exactly these map types: "
        "'points' (point markers with optional value-scaled size), 'line' (line features with optional "
        "value-scaled width), 'geojson' (render supported GeoJSON geometries), 'heatmap' "
        "(point density, optionally weighted by a numeric field), and 'choropleth' (color features by a "
        "varying numeric field, most suitable for polygon regions). Never recommend any other visualization "
        "type, library, or feature. Recommend at most three applicable supported types, using each exact type "
        "name, with one short reason per option. Only recommend types compatible with the target geometry "
        "and its actual attributes; do not invent fields or infer data that is not listed. If none fit, say so. "
        "Keep the answer simple and concise. This is advice only: do not create, display, or modify map layers. "
        "Ask which option the user wants if they want one created. "
        "Treat the dataset marked [LATEST UPLOAD] as the target when the user refers to their uploaded data. "
        f"Uploaded datasets, in upload order: {', '.join(raw_summary) if raw_summary else 'none'}. "
        f"Current map layers: {', '.join(layer['name'] for layer in show_layers) if show_layers else 'none'}. "
        "Reply in the user's language."
    ))
    messages = [message for message in state["messages"] if not isinstance(message, SystemMessage)]
    response = llm.invoke([prompt, *messages])
    return {"messages": [response]}


def build_workflow(llm_with_tools, tools, get_layers, llm_for_advice=None):
    """Build the original graph with an already-bound model and explicit inputs."""
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", partial(agent_reasoner, llm_with_tools=llm_with_tools, get_layers=get_layers))
    if llm_for_advice is not None:
        workflow.add_node("advice", partial(advisory_response, llm=llm_for_advice, get_layers=get_layers))
    node = ToolNode(tools)

    def one_call_state(state, call):
        messages = list(state["messages"])
        messages[-1] = messages[-1].model_copy(update={"tool_calls": [call]})
        return {**state, "messages": messages}

    def run_tools(state, config):
        messages = []
        for call in state["messages"][-1].tool_calls:
            messages.extend(node.invoke(one_call_state(state, call), config)["messages"])
        return {"messages": messages}

    async def run_tools_async(state, config):
        messages = []
        for call in state["messages"][-1].tool_calls:
            result = await node.ainvoke(one_call_state(state, call), config)
            messages.extend(result["messages"])
        return {"messages": messages}

    # ToolNode still executes/validates each tool, but a model batch is ordered
    # explicitly in sync AND async paths rather than relying on the prompt.
    workflow.add_node("tools", RunnableLambda(run_tools, afunc=run_tools_async))
    if llm_for_advice is None:
        workflow.add_edge(START, "agent")
    else:
        def route_request(state):
            latest = next((message for message in reversed(state["messages"])
                           if isinstance(message, HumanMessage)), None)
            return "advice" if _is_advisory_request(latest) else "agent"
        workflow.add_conditional_edges(START, route_request, {"advice": "advice", "agent": "agent"})
        workflow.add_edge("advice", END)
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")
    return workflow


def create_agent_app(llm, tools, get_layers):
    """Bind tools exactly once and compile; caller supplies the model instance."""
    return build_workflow(llm.bind_tools(tools), tools, get_layers).compile()


class AgentRuntime:
    """Lazy execution runtime, with one model/binding/graph per instance.

    The initialization lock only protects construction. Session-bound tools own
    their data and enforce single-tool atomic publication separately.
    """

    def __init__(self, model_factory, tools, get_layers):
        self._model_factory = model_factory
        self._tools = tools
        self._get_layers = get_layers
        self._model = self._bound_model = self._workflow = self._app = None
        self._lock = RLock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                self._model = self._model_factory()
            return self._model

    @property
    def bound_model(self):
        with self._lock:
            if self._bound_model is None:
                self._bound_model = self.model.bind_tools(self._tools)
            return self._bound_model

    @property
    def workflow(self):
        with self._lock:
            if self._workflow is None:
                self._workflow = build_workflow(self.bound_model, self._tools, self._get_layers,
                                                llm_for_advice=self.model)
            return self._workflow

    @property
    def app(self):
        with self._lock:
            if self._app is None:
                self._app = self.workflow.compile()
            return self._app

    def invoke(self, *args, **kwargs):
        return self.app.invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        return await self.app.ainvoke(*args, **kwargs)


class SessionAgent:
    """Agent execution and session-bound tools explicitly bound to one business session."""

    def __init__(self, session: SessionData, model_factory=None):
        from .tools import create_tools

        self.session = session
        self.TOOLS = create_tools(session)
        for registered in self.TOOLS:
            setattr(self, registered.name, registered)
        self._runtime = AgentRuntime(model_factory or create_llm, self.TOOLS, self.get_agent_layers)
        self.agent_app = self._runtime

    def get_agent_layers(self):
        return self.session.get_agent_layers()

    @property
    def llm(self):
        return self._runtime.model

    @property
    def llm_with_tools(self):
        return self._runtime.bound_model

    @property
    def workflow(self):
        return self._runtime.workflow


def create_agent_session(session: SessionData | None = None, model_factory=None) -> SessionAgent:
    """Create a private agent; an omitted session creates a fresh container."""
    return SessionAgent(session if session is not None else SessionData(), model_factory)
