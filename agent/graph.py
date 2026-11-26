"""Single-agent ReAct workflow with explicit model, tools and layer access."""
from functools import partial
import asyncio
import math
from threading import RLock
from typing import TypedDict, Sequence, Annotated, Optional, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from core.session import SessionData
from .attributes import is_non_measure_attribute
from .llm import create_llm
from .execution import ExecutionController, clean_model_response, failure, task_context
from .intent import request_intent
from .language import language_instruction

AGENT_RUNTIME_VERSION = 21


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

class AgentState(TypedDict, total=False):
    """LangGraph-Zustand: add_messages verwaltet den Nachrichtenverlauf der ReAct-Schleife."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    updated_show_layers: Optional[List[dict]]
    task: dict
    intent: str
    tool_count: int
    review_count: int
    review_action: str
    review_note: str
    stop_reason: str
    previous_task_context: str
    resuming: bool
    turn_start: int


def agent_reasoner(state: AgentState, *, llm_with_tools, get_layers, controller=None) -> dict:
    """Reasoning-Phase: Das LLM entscheidet anhand des Kontexts, ob es antwortet oder ein Werkzeug aufruft."""
    raw_layers, show_layers = get_layers()
    raw_summary = [f"'{l['name']}' ({l['type']}; CRS: {l.get('crs') or 'unbekannt'}; Attribute: {_layer_attribute_summary(l)})" + (
        f" [Dataset-ID: {l['dataset_id']}]" if l.get("dataset_id") else "") for l in raw_layers]
    show_summary = [f"Layer {l.get('layer_number', '?')}: '{l['name']}' ({l['type']}; CRS: {l.get('crs') or 'unbekannt'}; Attribute: {_layer_attribute_summary(l)})" + (
        (f" [data field: {l['data_field']}]" if l.get("data_field") else "") +
        (f" [Layer-ID: {l['layer_id']}, Dataset-ID: {l['dataset_id']}]" if l.get("layer_id") else ""))
        for l in show_layers]
    owner = getattr(get_layers, "__self__", None)
    session = getattr(owner, "session", None)
    managed_layers = session.layer_management_view if session is not None else show_layers
    hidden_summary = [f"Layer {layer.get('layer_number', '?')}: {layer['name']}" for layer in managed_layers if not layer.get("visible", True)]
    map_title = session.map_title if session is not None else ""
    relation_summary = [
        f"{item['source_dataset']}.{item['source_field']} → {item['target_dataset']}.{item['target_field']}"
        for item in (session.attribute_relations if session is not None else [])
    ]

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
    - Temporär gespeicherte Attributzuordnungen dieser Sitzung: [{'; '.join(relation_summary) if relation_summary else 'Keine'}]

    HANDLUNGSLEITFADEN:
    1. Du darfst und sollst Werkzeuge (Tools) verwenden, um Aktionen durchzuführen.
    2. Wenn ein Befehl mehrere Schritte erfordert (z.B. 'Lade Stadt Dresden und lege einen 1000m Puffer an'), führe die Werkzeuge nacheinander aus.
    3. Bei Choropleth-Karten darfst du nur ein tatsächlich aufgeführtes, numerisches Attribut verwenden; erfinde niemals einen Feldnamen aus dem Layer- oder Dateinamen.
    4. Wurde kein Choropleth-Attribut genannt und gibt es genau ein numerisches Attribut, verwende es und nenne es in der Antwort. Gibt es mehrere, frage nach einer Auswahl, außer der Nutzer hat die Auswahl ausdrücklich dir überlassen; dann wähle ein passendes Attribut und nenne es.
    5. Bei Punktekarte und genau einem variierenden numerischen Attribut verwende dieses Feld für proportionale Symbolgrößen; bei einer Linienkarte verwende proportionale Linienstärken. Nenne das Feld in der Antwort. Ohne geeignetes numerisches Feld nutze normale Symbole.
    6. Bei Punkte- oder Linienkarte und mehreren variierenden numerischen Attributen prüfe Geometrietyp, Feldnamen und Datensatzbeschreibung. Wähle selbst das inhaltlich passendste Feld, rufe das Werkzeug mit diesem Feld erneut auf und nenne das gewählte Feld sowie weitere verfügbare numerische Felder in der Antwort.
    7. Bei einem nicht verfügbaren oder nicht numerischen Attribut erkläre den Fehler und nenne die verfügbaren numerischen Attribute. Behaupte nur dann, eine Karte erstellt zu haben, wenn das Werkzeug erfolgreich war.
    8. Gib dem Nutzer nach Beendigung aller Tool-Aufrufe eine prägnante, informative Zusammenfassung in der Sprache seiner Anfrage.
    9. Bei Fragen zu aktuell sichtbaren Layern nenne ausschließlich Einträge aus der sichtbaren Live-Liste oben. Nutze niemals ältere Layerlisten aus dem Gesprächsverlauf.
    10. Trenne bei Stiländerungen Umrandung und Füllung. Für Choropleth ändert 'color' nur die Umrandung und 'fill_color' die Farbpalette (YlOrRd, Blues, Greens, Purples, Reds, Oranges, YlGn, YlGnBu, GnBu, PuBu, BuPu oder RdYlBu). Ordne Grün/green der Palette Greens, Blau/blue der Palette Blues, Rot/red der Palette Reds, Orange/orange der Palette Oranges, Gelb/yellow der Palette YlGn und Lila/purple der Palette Purples zu; ändere niemals beide Parameter, wenn nur einer gewünscht ist.
    11. Maßgeblich sind die aktuell registrierten Werkzeugparameter und diese Regeln, nicht frühere Aussagen im Chat. 'update_map_style' unterstützt getrennte Parameter 'color' (Umrandung) und 'fill_color' (Füllung); behaupte nicht, dass diese Änderung nicht möglich sei.
    12. Jede ausdrückliche Aufforderung, einen Layer zu entfernen, auszublenden oder einzublenden, MUSS 'manage_map_layer' aufrufen. Nicht nur verbal bestätigen. Verwende action='remove' zum Entfernen, action='hide' zum Ausblenden und action='show' zum Einblenden. Auch ausgeblendete Layer sind über ihren Namen verfügbar. Das Entfernen löscht nur den Kartenlayer, nicht die hochgeladenen Quelldaten.
    13. OpenStreetMap-Basiskarte: Sie kann ausschließlich über das Kontrollkästchen in der linken Seitenleiste ein- oder ausgeschaltet werden. Behaupte niemals, sie per Chat geändert zu haben.
    14. Bei einer ausdrücklichen CSV-Anfrage für statistische/tabellarische Daten rufe 'export_statistical_table' auf. Verwende den angegebenen Ergebnislayer und nur gewünschte, tatsächlich vorhandene Attributfelder; ohne Feldliste exportiere alle Attributspalten. Geometrien werden ausgelassen. Die CSV wird als Download-Anhang bereitgestellt. Für Kartenexporte MUSS 'export_current_map' aufgerufen werden: format='html' für eine interaktive Karte, 'geojson' für sichtbare Vektordaten, 'png' für einen Kartenscreenshot und 'pdf' für eine druckbare Kartenansicht. HTML, PNG und PDF enthalten die derzeit sichtbaren Layer und den aktuellen Basiskartenstatus; GeoJSON enthält nur sichtbare Vektorlayer, keine Basiskarte. Bei mehreren GeoJSON-Layern entsteht ein ZIP-Archiv. Frage nach dem Format, wenn es nicht genannt wurde. Behaupte einen Export erst nach erfolgreichem Werkzeugergebnis.
    15. Wenn der Nutzer ausdrücklich einen Kartentitel setzen, hinzufügen oder automatisch erzeugen lassen möchte, rufe 'set_map_title' auf. Bei einem vorgegebenen Titel übernimm den gewünschten Wortlaut exakt. Für einen automatischen Titel verwende ausschließlich Namen und tatsächlich angezeigte Attribute der aktuell sichtbaren Layer; beziehe ausgeblendete oder nur hochgeladene Daten nicht ein. Erfinde keine Inhalte. Formuliere den automatisch erzeugten Titel knapp und in der Sprache der Nutzeranfrage, wobei Layer-Eigennamen erhalten bleiben. Wenn keine Layer sichtbar sind, frage nach den gewünschten Titelinhalten. Eine reine Bitte um Titelvorschläge ohne Aufforderung zum Setzen beantwortest du, ohne den Kartentitel zu ändern.
    16. Wenn der Nutzer ausdrücklich den aktuellen Kartentitel entfernen, löschen oder zurücksetzen möchte, MUSS 'clear_map_title' aufgerufen werden. Behaupte die Entfernung erst nach erfolgreichem Werkzeugergebnis.
    17. Wenn der Nutzer die Koordinatenreferenzsysteme (CRS) seiner hochgeladenen Datensätze prüfen oder vergleichen lassen möchte, rufe 'check_dataset_crs_consistency' auf und berichte das Werkzeugergebnis. Das Werkzeug prüft nur und verändert keine Daten.
    18. Wenn der Nutzer hochgeladene Daten auf fehlende Koordinaten, auffällige Datensätze oder ungültige Geometrien prüfen lassen möchte, rufe 'check_uploaded_data_quality' auf und berichte die gezählten Befunde. Das Werkzeug prüft nur und verändert keine Daten.
    19. Wenn der Nutzer zwei Datensätze über Attribute verknüpfen möchte, rufe 'associate_dataset_attributes' auf. Verwende die Datensätze in der angegebenen Richtung (Quelldaten → Zieldaten), nutze eindeutig erkennbare Felder oder bitte bei Mehrdeutigkeit um Präzisierung. Berichte die Feldzuordnung, Treffer, Nichttreffer und doppelte Schlüssel. Dies ist eine temporäre Zuordnung in der Sitzung; führe keine räumliche Verknüpfung und keinen Datenmerge aus.
    20. Wenn der Nutzer Datensätze nach einem Attribut filtern und einen neuen Layer erzeugen möchte, rufe 'filter_dataset_by_attribute' auf. Verwende nur tatsächlich vorhandene Felder. Wähle 'equals' oder 'contains' für Kategorien, numerische Operatoren für Messwerte und 'within_last_years' mit der Anzahl der Jahre für relative Zeitangaben. Frage nach einer Präzisierung, wenn Feld oder Wert nicht eindeutig ist. Der Quell-Datensatz bleibt unverändert; berichte Bedingung und Anzahl der ausgewählten Datensätze.
    21. Für Distanzabfragen rufe 'calculate_feature_distances' auf. Bei einem Layer und allen Objektpaaren verwende query_mode='all_pairs'; zwischen zwei Layern verwende query_mode='nearest' für den nächsten Nachbarn je Quellobjekt oder query_mode='within_distance' mit distance_threshold_meters für alle Paare im Umkreis. Ein Schwellenwert zusammen mit 'nearest' klassifiziert Quellobjekte danach, ob ihr nächster Nachbar innerhalb oder außerhalb des Grenzwerts liegt. Unterstützt werden Punkt-, Linien- und Flächengeometrien; berichte, welche Layer verglichen wurden und welche Ergebnisse die Abfrage lieferte.
    22. Wenn der Nutzer räumliche Beziehungen zwischen zwei Layern prüfen und die Ergebnisse auf der Karte sehen möchte, rufe 'query_spatial_relationship' auf. Für Punkte und Verwaltungsflächen verwende relationship='point_in_polygon'; für räumliche Überschneidungen verwende 'intersects'. Berichte Treffer, Nichttreffer und Mehrfachtreffer. Bei point_in_polygon bleiben Nichttreffer im neuen Ergebnislayer, ihre related_-Attribute sind leer. Es wird keine räumliche Distanz berechnet.
    23. Wenn der Nutzer Quellobjekte je Verwaltungsgebiet zählen oder ein numerisches Feld je Gebiet summieren möchte, rufe 'aggregate_features_by_area' auf. Übergib den Polygonlayer als area_layer_name, den Datensatz mit den zu zählenden Objekten/Werten als source_layer_name und operation='count' oder 'sum'; bei sum muss value_field ein vorhandenes numerisches Feld sein. Das Werkzeug gibt eine Tabelle mit allen Gebieten zurück; Punkte auf Grenzen gelten nicht als innerhalb, andere Geometrien zählen bei Überschneidung. Bei überlappenden Gebieten kann ein Objekt mehrfach zählen.
    24. Wenn der Nutzer aus vorhandenen numerischen Attributen eine Kennzahl, Quote oder normierte Rate berechnen möchte, rufe 'calculate_derived_indicator' auf. Verwende dasselbe Layer für numerator_field und denominator_field, sofern beide Werte je Gebiet bereits vorliegen; berechne Zähler / Nenner × scale_factor (Prozent: 100, je 10.000: 10000). Nutze nur vorhandene Felder und frage bei unklarer fachlicher Definition nach. Nenner 0 oder fehlende/ungültige Werte werden fehlend. Das Werkzeug erstellt einen neuen Ergebnislayer und ändert die Quelle nicht. Wenn der Zähler erst je Gebiet ermittelt werden muss, führe zuerst 'aggregate_features_by_area' aus und verwende dessen Ergebnislayer.
    25. Wenn der Nutzer die tatsächliche Polygonfläche einer Kategorie (z. B. Parks/Grünflächen) je Verwaltungsgebiet berechnen möchte, rufe 'calculate_feature_area_by_area' auf. Das Werkzeug schneidet Quellpolygone an den Gebietsgrenzen, berechnet die Schnittflächen im lokalen metrischen CRS, summiert sie je Gebiet in km² und gibt Tabelle sowie Ergebnislayer zurück. Verwende es statt einfacher Flächensummen, wenn Quellpolygone Gebietsgrenzen überschreiten. Überlappende Quellpolygone werden jeweils mitgezählt.
    26. Wenn der Nutzer die höchsten/niedrigsten oder Top-N-Werte eines numerischen Felds wissen möchte, rufe 'rank_dataset_by_field' auf. Verwende den tatsächlich vorhandenen Layer und Feldnamen, limit=N und order='descending' für höchste bzw. 'ascending' für niedrigste Werte. Frage nach, falls Feld, N oder Sortierrichtung unklar sind. Das Werkzeug gibt ausschließlich eine Rangliste als Texttabelle zurück, erstellt keinen neuen Layer und lässt fehlende/nichtnumerische Werte aus.
    27. Wenn der Nutzer Polygonlayer räumlich überlagern möchte, rufe 'run_spatial_overlay' mit der passenden Operation auf: intersection, difference, union oder symmetric_difference. 'difference' bedeutet immer Layer A minus Layer B. 'union' teilt Überlappungen und erhält Attribute beider Layer. Für mehrstufige Analysen darfst du Operationen nacheinander ausführen und erzeugte Ergebnislayer als nächsten Eingang verwenden; erkläre die Reihenfolge und fasse die Ergebnisse zusammen. Verwende das Werkzeug nur für Polygon-/MultiPolygon-Layer.
    28. Wenn der Nutzer hohe Ereignisdichten oder Hotspots in Punktdaten identifizieren möchte, rufe 'identify_spatial_hotspots' auf. Verwende nur Punktlayer. Nenne Zellgröße und Hotspot-Perzentil; Ergebnis ist ein Raster-Choropleth mit Zählwerten und Hotspot-Zellen. Erkläre, dass dies eine Perzentil-basierte Dichteklassifikation und kein statistischer Signifikanztest ist. Eine reine Bitte um Heatmap-Visualisierung bleibt eine Kartenvisualisierung und verwendet stattdessen den bestehenden Heatmap-Weg.
    29. Wenn der Nutzer einen Datensatz dauerhaft in ein anderes Koordinatenreferenzsystem umwandeln möchte, rufe 'transform_dataset_crs' auf. Verwende ein vom Nutzer genanntes oder eindeutig angegebenes Ziel-CRS; rate es nicht. Wenn das Ziel-CRS fehlt, frage danach. Ein unbekanntes Quell-CRS darf nicht erraten werden. Die Transformation erstellt eine neue Ebene und lässt die Quelle unverändert.
    """

    system_prompt += (
        "\nEXECUTION CONTRACT:\n"
        "Execute exactly ONE tool per response. Read its actual execution_result before choosing the next call. "
        "Use returned Dataset-ID/Layer-ID values instead of predicting generated names. "
        "For a multi-step request include a short <task_plan> JSON array in your first response, "
        "with entries {\"description\":\"...\",\"tool\":\"registered_tool_name\",\"arguments\":{}}. "
        "List every requested operation, including exports. Arguments may contain known acceptance conditions; "
        "never invent IDs, fields, CRS, or values. Ask a specific question when required information is absent. "
        "Previously completed steps must not be repeated on continuation. "
        "Only describe a task as complete when every required step has verified evidence. "
        "An error is not evidence of success. If a step fails, do not execute dependent operations. "
        "Distinguish clarification questions from completion. Reply in the user's language.\n"
        "Layer numbers are the current sidebar positions, compacted after deletion. '图层4', 'Layer 4' "
        "and 'Ebene 4' refer to the layer currently numbered 4, never to filtered descendants. "
        "Use immutable Layer-ID/Dataset-ID values for saved steps and continuations. "
        "Pure style or visibility changes must reuse "
        "the existing layer and must not recompute buffers or recreate boundaries. "
        "For count-only questions use aggregate_features_by_area with create_layer=False. Only create "
        "a statistical map when explicitly requested. Report actual area/source names and counts.\n"
        "CURRENT TASK: " + task_context(state.get("task"))
        + "\nPREVIOUS TASK (reference only; not current map state): " + state.get("previous_task_context", "")
        + "\nCOMPLETION REVIEW: " + state.get("review_note", "")
        + "\n" + language_instruction(state.get("task", {}).get("language", "de"))
    )
    # Systemnachrichten entfernen und den aktuellen Systemkontext an den Anfang setzen.
    user_and_assistant_msgs = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
    full_messages = [SystemMessage(content=system_prompt)] + list(user_and_assistant_msgs)

    try:
        if controller is not None and controller.session is not None:
            controller.session.diagnostics.record("model_call", route="agent")
        response = llm_with_tools.invoke(full_messages)
    except Exception as exc:
        task = dict(state.get("task", {}))
        task["stop_reason"] = f"Modellanfrage fehlgeschlagen ({type(exc).__name__}): {exc}"
        return {"messages": [AIMessage(content="")], "task": task, "stop_reason": "model_error"}
    task = state.get("task")
    if controller is not None and task is not None:
        task = controller.accept_plan(task, response)
        # Retain proposed operations, not their speculative arguments. The next
        # model turn must bind inputs using the first tool's actual result.
        for call in (response.tool_calls or [])[1:]:
            if (call["name"] in controller.tools and controller.allowed_operation(task, call["name"])
                    and not any(item["tool"] == call["name"] for item in task["plan"])):
                task["plan"].append({"description": call["name"], "tool": call["name"],
                                     "arguments": {}, "status": "pending"})
        controller.save(task)
    result = {"messages": [clean_model_response(response)]}
    if task is not None:
        result["task"] = task
    return result


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
    return request_intent(content)["mode"] in {"advice", "consult"}


def advisory_response(state: AgentState, *, llm, get_layers, controller=None) -> dict:
    """Answer visualization advice requests without exposing mutation tools."""
    raw_layers, show_layers = get_layers()
    if state.get("intent") == "audit" and controller is not None:
        answer = controller.explain_previous_statistics(state["task"].get("language", "de"))
        if answer:
            return {"messages": [AIMessage(content=answer)]}
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
    if state.get("intent") in {"consult", "audit"}:
        prompt = SystemMessage(content=(
            "You are a concise GIS advisor. Answer the user's actual question (including CRS, data quality, "
            "analysis methods or visualization). This is consultation only: do not execute or claim changes. "
            "Respect negations. If asked to check actual data, explain what is known from the supplied context "
            "and what would require running a tool. Reply in the user's language. "
            f"Uploaded datasets: {', '.join(raw_summary) or 'none'}. "
            f"Visible layers: {', '.join(layer['name'] for layer in show_layers) or 'none'}. "
            "Previous task: " + state.get("previous_task_context", "")
        ))
    prompt = SystemMessage(content=prompt.content + "\n" + language_instruction(state.get("task", {}).get("language", "de")))
    messages = [message for message in state["messages"] if not isinstance(message, SystemMessage)]
    try:
        if controller is not None and controller.session is not None:
            controller.session.diagnostics.record("model_call", route="advice")
        response = llm.invoke([prompt, *messages])
        if response.tool_calls:
            text = {"zh": "请明确你想了解的内容。", "en": "Please clarify the information you need.",
                    "de": "Bitte formuliere die gewünschte Auskunft genauer."}[state.get("task", {}).get("language", "de")]
            response = AIMessage(content=text)
        return {"messages": [response]}
    except Exception as exc:
        text = {"zh": "咨询服务调用失败，请稍后重试。", "en": "The consultation service failed. Please try again.",
                "de": "Die Beratung konnte nicht abgeschlossen werden. Bitte versuche es erneut."}[state.get("task", {}).get("language", "de")]
        return {"messages": [AIMessage(content=text)]}


def build_workflow(llm_with_tools, tools, get_layers, llm_for_advice=None):
    """Single-tool execution with task evidence, bounded recovery and review."""
    controller = ExecutionController(tools, get_layers)
    workflow = StateGraph(AgentState)
    workflow.add_node("prepare", controller.prepare)
    workflow.add_node("agent", partial(agent_reasoner, llm_with_tools=llm_with_tools,
                                         get_layers=get_layers, controller=controller))
    if llm_for_advice is not None:
        workflow.add_node("advice", partial(advisory_response, llm=llm_for_advice, get_layers=get_layers, controller=controller))
    node = ToolNode(tools, handle_tool_errors=True)

    def one_call_state(state, call):
        messages = list(state["messages"])
        messages[-1] = messages[-1].model_copy(update={"tool_calls": [call]})
        return {**state, "messages": messages}

    def staged_call(state):
        call = state["messages"][-1].tool_calls[0]
        result, normalized = controller.before_tool(state, call)
        message = (ToolMessage(content=result["message"], artifact=result,
                               tool_call_id=call["id"], name=call["name"]) if result is not None else None)
        return call, normalized, message

    def run_tools(state, config):
        call, normalized, message = staged_call(state)
        if message is None:
            try:
                message = node.invoke(one_call_state(state, normalized), config)["messages"][0]
            except Exception as exc:
                message = ToolMessage(content=str(exc), artifact=failure(exc, "tool_error"),
                                      tool_call_id=call["id"], name=call["name"])
        return controller.record_tool(state, call, normalized, message)

    async def run_tools_async(state, config):
        call, normalized, message = staged_call(state)
        if message is None:
            try:
                result = await node.ainvoke(one_call_state(state, normalized), config)
                message = result["messages"][0]
            except Exception as exc:
                message = ToolMessage(content=str(exc), artifact=failure(exc, "tool_error"),
                                      tool_call_id=call["id"], name=call["name"])
        return controller.record_tool(state, call, normalized, message)

    # Both paths execute one validated call. Remaining batch proposals must be
    # replanned using the actual result rather than speculative arguments.
    workflow.add_node("tools", RunnableLambda(run_tools, afunc=run_tools_async))
    workflow.add_node("review", controller.review)
    workflow.add_node("finish", controller.finish)
    workflow.add_edge(START, "prepare")
    if llm_for_advice is None:
        workflow.add_edge("prepare", "agent")
    else:
        def route_request(state):
            return "advice" if state["intent"] in {"advice", "consult", "audit"} else "agent"
        workflow.add_conditional_edges("prepare", route_request, {"advice": "advice", "agent": "agent"})
        workflow.add_edge("advice", "finish")
    workflow.add_conditional_edges("agent", lambda state: "finish" if state.get("stop_reason") else (
        "tools" if should_continue(state) == "tools" else "review"))
    workflow.add_conditional_edges("tools", lambda state: "finish" if state.get("stop_reason") else "agent")
    workflow.add_conditional_edges("review", lambda state: state["review_action"], {"agent": "agent", "finish": "finish"})
    workflow.add_edge("finish", END)
    return workflow


def create_agent_app(llm, tools, get_layers):
    """Bind tools exactly once and compile; caller supplies the model instance."""
    return build_workflow(llm.bind_tools(tools), tools, get_layers, llm_for_advice=llm).compile()


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
        self._execution_lock = RLock()

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
        with self._execution_lock:
            return self.app.invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        # Serialize a whole session invocation, including task bookkeeping.
        # Running the sync entry off-loop avoids holding a thread lock across await.
        return await asyncio.to_thread(self.invoke, *args, **kwargs)


class SessionAgent:
    """Agent execution and session-bound tools explicitly bound to one business session."""

    def __init__(self, session: SessionData, model_factory=None):
        from .tools import create_tools
        self.session = session
        SessionData.ensure_compatible(session)
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
