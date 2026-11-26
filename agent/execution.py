"""Task bookkeeping, argument validation and evidence-based workflow completion."""
from copy import deepcopy
import hashlib
import json
import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.tasks import TaskJournal
from core.session import SessionData, AmbiguousReference
from .intent import initial_plan, is_continuation, request_intent, request_policy, LAYER_PRODUCERS
from .language import detect_language, language_preference


MAX_TOOL_STEPS = 10
MAX_ATTEMPTS = 2
REFERENCE_ARGUMENTS = {
    "layer_name", "dataset_name", "source_layer_name", "target_layer_name",
    "area_layer_name", "polygon_layer_name", "point_layer_name", "layer_a_name",
    "layer_b_name", "source_dataset", "target_dataset",
}


def fingerprint(name, arguments):
    value = json.dumps([name, arguments], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(value.encode()).hexdigest()


def failure(message, category):
    return {"success": False, "message": str(message), "dataset_ids": [], "layer_ids": [],
            "updated_layer_ids": [], "metrics": {}, "warnings": [], "error_category": category}


def task_context(task):
    if not task:
        return "No earlier task."
    compact = {key: task.get(key) for key in ("task_id", "goal", "follow_up", "status", "plan", "pending_question", "stop_reason", "policy", "explicit_layers", "language")}
    compact["steps"] = [
        {"step_id": step["step_id"], "tool": step["tool"], "arguments": step["arguments"],
         "success": step["result"]["success"], "dataset_ids": step["result"].get("dataset_ids", []),
         "layer_ids": step["result"].get("layer_ids", []),
         "inputs": step.get("inputs", {}),
         "message": step["result"].get("message", "")[:600]}
        for step in task.get("steps", [])[-20:]
    ]
    return json.dumps(compact, ensure_ascii=False, default=str)


def clean_model_response(response):
    """A batch is a proposal. Publish just one call and replan after its result."""
    calls = getattr(response, "tool_calls", []) or []
    updates = {"tool_calls": calls[:1], "additional_kwargs": {
        key: value for key, value in response.additional_kwargs.items() if key != "tool_calls"}}
    text = response.content
    if isinstance(text, str):
        updates["content"] = re.sub(r"<task_plan>.*?</task_plan>", "", text, flags=re.S).strip()
    return response.model_copy(update=updates)


class ExecutionController:
    def __init__(self, tools, get_layers):
        self.tools = {tool.name: tool for tool in tools}
        owner = getattr(get_layers, "__self__", None)
        self.session = owner if isinstance(owner, SessionData) else getattr(owner, "session", None)
        self.journal = self.session.task_journal if self.session is not None else TaskJournal()

    def save(self, task):
        if task and task.get("intent") not in {"advice", "consult", "audit"}:
            self.journal.save(task)

    def explain_previous_statistics(self, language):
        for task in reversed(self.journal.history):
            steps = [step for step in task["steps"] if step["tool"] == "aggregate_features_by_area" and step["result"]["success"]]
            if not steps:
                continue
            lines = []
            for step in steps:
                inputs = deepcopy(step.get("inputs", {}))
                for key in ("area_layer_name", "source_layer_name"):
                    if key not in inputs and self.session is not None:
                        target = self.session.resolve_in(list(self.session.raw_view) + self.session.layer_management_view,
                                                         step["arguments"].get(key, ""))
                        if target:
                            inputs[key] = target
                def label(key):
                    target = inputs.get(key, {})
                    number = target.get("layer_number")
                    name = target.get("name") or step["arguments"].get(key, "?")
                    prefix = f"图层{number}" if language == "zh" else f"Layer {number}"
                    if number is not None and target.get("layer_id") and self.session is not None:
                        current_layer = self.session.layers.get(target["layer_id"])
                        current_number = current_layer.display_number if current_layer else None
                        if current_number != number:
                            if language == "zh":
                                prefix += f"（执行时编号，当前为图层{current_number}）" if current_layer else "（执行时编号，现已移除）"
                            elif language == "en":
                                prefix += f" (at execution; now Layer {current_number})" if current_layer else " (at execution; now removed)"
                            else:
                                prefix += f" (bei Ausführung; jetzt Layer {current_number})" if current_layer else " (bei Ausführung; inzwischen entfernt)"
                    return (prefix + " " if number is not None else "") + f"「{name}」"
                for key, titles in (("area_layer_name", {"zh": "统计区域", "en": "Areas", "de": "Gebiete"}),
                                    ("source_layer_name", {"zh": "统计对象", "en": "Counted features", "de": "Gezählte Objekte"})):
                    lines.append(f"- {titles.get(language, titles['de'])}: {label(key)}")
                count = step["result"].get("metrics", {}).get("unique_matched_source_count")
                if count is not None:
                    heading = {"zh": "命中的不同对象数", "en": "Distinct matched features", "de": "Unterschiedliche Treffer"}.get(language, "Treffer")
                    lines.append(f"- {heading}: {count}")
            intro = {"zh": "根据实际执行记录，刚才的统计使用了：", "en": "The execution record shows these actual inputs:",
                     "de": "Laut Ausführungsprotokoll wurden tatsächlich diese Eingaben verwendet:"}
            return intro.get(language, intro["de"]) + "\n" + "\n".join(lines)
        return None

    def success_reports(self, task, steps):
        if task.get("language") != "zh":
            return [step["result"]["message"] for step in steps]
        names = {"update_map_style": "样式修改", "manage_map_layer": "图层管理",
                 "calculate_buffer_analysis": "缓冲区计算", "aggregate_features_by_area": "区域统计",
                 "visualize_layer_on_map": "地图绘制", "export_current_map": "地图导出",
                 "export_statistical_table": "统计表导出", "fetch_city_boundary_osm": "城市边界获取",
                 "filter_dataset_by_attribute": "属性筛选", "transform_dataset_crs": "坐标系转换"}
        layers = self.session.layers if self.session is not None else {}
        reports = []
        for step in steps:
            text = "已完成" + names.get(step["tool"], "数据处理")
            outputs = [layers[key] for key in step["result"].get("layer_ids", ()) if key in layers]
            if outputs:
                text += "：" + "、".join(f"图层{layer.display_number}「{layer.name}」" for layer in outputs)
            count = step["result"].get("metrics", {}).get("unique_matched_source_count")
            if count is not None:
                text += f"，匹配到{count}个不同对象"
            reports.append(text + "。")
        return reports

    def prepare(self, state):
        latest = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
        text = latest.content if latest and isinstance(latest.content, str) else ""
        intent = request_intent(text)
        previous = self.journal.latest
        preference = language_preference(text)
        if self.session is not None and preference:
            self.session.reply_language_preference = preference
        language = ((getattr(self.session, "reply_language_preference", None) if self.session else None)
                    or detect_language(text, (previous or {}).get("language", "de")))
        if previous and not (previous["steps"] or previous["plan"] or previous["status"] == "waiting"):
            previous = next((task for task in reversed(self.journal.history)
                             if task["steps"] or task["plan"] or task["status"] == "waiting"), previous)
        # A direct answer to a saved question belongs to that task. Explicit
        # advice/questions/new action requests retain their own routing.
        proposed = initial_plan(intent)
        pending_tools = {item["tool"] for item in previous["plan"] if item["status"] != "completed"} if previous else set()
        answering = (previous and previous["status"] == "waiting" and intent["mode"] in {"agent", "execute"}
                     and (not proposed or {item["tool"] for item in proposed}.issubset(pending_tools))
                     and not re.search(r"new task|start over|新任务|重新开始|neue aufgabe", text, re.I))
        resume = bool(previous and (is_continuation(text) or answering)
                      and intent["mode"] not in {"advice", "consult", "audit"})
        if resume:
            intent["mode"] = "execute"
        plan = initial_plan(intent)
        if intent["mode"] in {"advice", "consult", "audit"}:
            task = {"task_id": "", "goal": text, "plan": [], "steps": [],
                    "status": "advisory", "intent": intent["mode"], "stop_reason": ""}
        else:
            task = self.journal.begin(text, plan, resume=resume,
                                      task_id=previous["task_id"] if resume else None)
            if resume:
                for entry in plan:
                    pending = next((item for item in task["plan"] if item["tool"] == entry["tool"] and item["status"] != "completed"), None)
                    if pending is not None:
                        pending["arguments"].update(entry["arguments"])
                        pending["status"] = "pending"
                    elif not any(item["tool"] == entry["tool"] and item["arguments"] == entry["arguments"] for item in task["plan"]):
                        task["plan"].append(entry)
                formats = re.findall(r"\b(html|geojson|png|pdf)\b", text.casefold())
                if len(set(formats)) == 1:
                    for item in task["plan"]:
                        if item["tool"] == "export_current_map" and item["status"] != "completed":
                            item["arguments"]["format"] = formats[0]
            task["intent"] = intent["mode"]
            task["pending_question"] = ""
        task["language"] = language
        continuing = bool(resume and previous and task["task_id"] == previous["task_id"])
        earlier_references = deepcopy(task.get("explicit_layers", [])) if continuing else []
        policy = request_policy(intent)
        if continuing and not plan:
            policy = deepcopy(task.get("policy", request_policy(request_intent(task["goal"]))))
        task["policy"] = policy
        task["explicit_layers"] = []
        if self.session is not None:
            for match in re.finditer(r"(?:图层|\blayer|\bebene)\s*#?\s*(\d+)", text, re.I):
                reference = match.group(0)
                target = self.session.resolve_in(self.session.layer_management_view, reference, require_layer=True)
                if any(item["number"] == int(match.group(1)) for item in task["explicit_layers"]):
                    continue
                data = target.get("data", {}) if target else {}
                features = data.get("features", []) if isinstance(data, dict) else data if isinstance(data, list) else []
                geometry_types = sorted({feature.get("geometry", {}).get("type") for feature in features
                                         if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
                                         and feature["geometry"].get("type")})
                task["explicit_layers"].append({"reference": reference, "number": int(match.group(1)),
                    "layer_id": target.get("layer_id") if target else None,
                    "dataset_id": target.get("dataset_id") if target else None,
                    "name": target.get("name") if target else None, "geometry_types": geometry_types})
        if continuing:
            if not task["explicit_layers"]:
                task["explicit_layers"] = earlier_references
            else:
                def family(item):
                    kinds = set(item.get("geometry_types", []))
                    return ("polygon" if kinds & {"Polygon", "MultiPolygon"} else
                            "point" if kinds & {"Point", "MultiPoint"} else "other")
                new_families = {family(item) for item in task["explicit_layers"]}
                task["explicit_layers"] = [item for item in earlier_references if family(item) not in new_families] + task["explicit_layers"]
                # The user's new explicit target supersedes old model-supplied
                # acceptance references; scalar conditions remain in force.
                for requirement in task["plan"]:
                    if requirement["status"] != "completed":
                        requirement["arguments"] = {key: value for key, value in requirement["arguments"].items()
                                                    if key not in REFERENCE_ARGUMENTS}
        self.save(task)
        return {"task": task, "intent": intent["mode"], "tool_count": 0,
                "review_count": 0, "stop_reason": "", "review_note": "",
                "previous_task_context": task_context(previous), "resuming": resume,
                "turn_start": len(task["steps"])}

    def accept_plan(self, task, response):
        """Only add valid tool obligations; model plans cannot erase requirements."""
        if not isinstance(response.content, str):
            return task
        match = re.search(r"<task_plan>(.*?)</task_plan>", response.content, re.S)
        if not match:
            return task
        try:
            proposed = json.loads(match.group(1))
        except (ValueError, TypeError):
            return task
        if not isinstance(proposed, list):
            return task
        for item in proposed[:20]:
            if not isinstance(item, dict) or item.get("tool") not in self.tools:
                continue
            if not self.allowed_operation(task, item["tool"]):
                continue
            args = item.get("arguments", {})
            if not isinstance(args, dict) or set(args) - set(self.tools[item["tool"]].args_schema.model_fields):
                continue
            args = dict(args)
            args = self.pin_references(item["tool"], args, task)
            if item["tool"] == "aggregate_features_by_area" and task.get("policy", {}).get("count_only") and "create_layer" in args:
                args["create_layer"] = False
            for key in ("format", "action"):
                if isinstance(args.get(key), str):
                    args[key] = args[key].strip().casefold().lstrip(".")
            requirement = {"description": str(item.get("description", item["tool"]))[:300],
                           "tool": item["tool"], "arguments": args, "status": "pending"}
            if not any(entry["tool"] == requirement["tool"] and entry["arguments"] == args for entry in task["plan"]):
                task["plan"].append(requirement)
        self.save(task)
        return task

    def allowed_operation(self, task, name):
        policy = task.get("policy", {})
        if policy.get("editing_only") and name in LAYER_PRODUCERS:
            return False
        if policy.get("count_only") and name in LAYER_PRODUCERS and name != "aggregate_features_by_area":
            return any(entry["tool"] == name for entry in initial_plan(request_intent(task.get("goal", ""))))
        return True

    def pin_references(self, name, arguments, task):
        args = dict(arguments)
        references = task.get("explicit_layers", [])
        if not references or any(not item["layer_id"] for item in references):
            return args
        fields = self.tools[name].args_schema.model_fields
        if "area_layer_name" in fields:
            polygons = [item for item in references if set(item["geometry_types"]) & {"Polygon", "MultiPolygon"}]
            source = [item for item in references if item not in polygons]
            if polygons:
                args["area_layer_name"] = polygons[0]["layer_id"]
            if source and "source_layer_name" in fields:
                args["source_layer_name"] = source[0]["layer_id"]
        else:
            slots = [key for key in ("layer_name", "dataset_name", "polygon_layer_name", "source_layer_name",
                     "layer_a_name", "source_dataset", "target_layer_name", "layer_b_name", "target_dataset") if key in fields]
            for key, reference in zip(slots, references):
                args[key] = reference["layer_id"]
        return args

    def normalize_call(self, call, task=None):
        name = call["name"]
        if name not in self.tools:
            raise ValueError(f"Unbekanntes Werkzeug: {name}")
        args = self.tools[name].args_schema.model_validate(call["args"]).model_dump()
        task = task or {}
        references = task.get("explicit_layers", [])
        if references:
            if any(not item["layer_id"] for item in references):
                raise LookupError("指定的图层编号不存在，请检查侧栏中的图层编号。")
            args = self.pin_references(name, args, task)
        if name == "aggregate_features_by_area" and task.get("policy", {}).get("count_only"):
            args["create_layer"] = False
        for key in ("format", "action"):
            if isinstance(args.get(key), str):
                args[key] = args[key].strip().casefold().lstrip(".")
        inputs = {}
        if self.session is not None:
            pool = list(self.session.raw_view) + self.session.layer_management_view
            for key, value in list(args.items()):
                if key not in REFERENCE_ARGUMENTS or not value:
                    continue
                require_layer = name in {"manage_map_layer", "update_map_style"} and key == "layer_name"
                match = self.session.resolve_in(pool, value, require_layer=require_layer)
                if match is None:
                    raise LookupError(f"Datensatz oder Layer '{value}' nicht gefunden. Bitte eine gültige ID oder einen eindeutigen Namen angeben.")
                args[key] = match.get("layer_id") if require_layer else match["dataset_id"]
                inputs[key] = {field: match.get(field) for field in ("name", "layer_number", "layer_id", "dataset_id")}
        return {**call, "args": args, "input_references": inputs}

    def before_tool(self, state, call):
        task = state["task"]
        if not self.allowed_operation(task, call["name"]):
            return failure("This operation was not requested. Edit the existing layer for style/visibility requests; use read-only aggregation for count-only questions.", "out_of_order"), None
        if state.get("tool_count", 0) >= MAX_TOOL_STEPS:
            return failure("Schrittlimit erreicht. Bereits ausgeführte Schritte bleiben erhalten.", "budget"), None
        recent = task["steps"][state.get("turn_start", 0):]
        if (recent and not recent[-1]["result"]["success"]
                and recent[-1]["result"].get("error_category") != "out_of_order"
                and call["name"] != recent[-1]["tool"]):
            return failure("Der vorherige Schritt ist fehlgeschlagen. Abhängige Schritte wurden angehalten.", "dependency"), None
        pending = [item for item in task["plan"] if item["status"] != "completed"]
        if pending and call["name"] != pending[0]["tool"]:
            equivalent = (pending[0].get("accepts_layer_result") and call["name"] in LAYER_PRODUCERS)
            later = any(item["tool"] == call["name"] for item in pending[1:])
            if later and not equivalent:
                return failure("Ein vorausgehender geplanter Schritt fehlt. Zuerst ausführen: "
                               + pending[0]["description"], "out_of_order"), None
        try:
            normalized = self.normalize_call(call, task)
        except AmbiguousReference as exc:
            return failure(exc, "ambiguous_reference"), None
        except LookupError as exc:
            # A successful removal intentionally makes its own reference
            # unresolvable. Its task evidence is the idempotency proof.
            if call["name"] == "manage_map_layer" and str(call["args"].get("action", "")).casefold() == "remove":
                for step in reversed(task["steps"]):
                    if step["tool"] != call["name"] or not step["result"]["success"]:
                        continue
                    references = {step["arguments"].get("layer_name"), step.get("original_arguments", {}).get("layer_name")}
                    if (step["arguments"].get("action") == "remove" and call["args"].get("layer_name") in references
                            and self.result_still_valid(call["name"], step["arguments"], step["result"])):
                        cached = deepcopy(step["result"])
                        cached["reused"] = True
                        return cached, {**call, "args": deepcopy(step["arguments"])}
            return failure(exc, "not_found"), None
        except (ValueError, TypeError) as exc:
            return failure(exc, "needs_input"), None
        if self.session is not None:
            # Bind acceptance references before a removal makes them disappear.
            pool = list(self.session.raw_view) + self.session.layer_management_view
            for requirement in task["plan"]:
                if requirement["tool"] != call["name"]:
                    continue
                for parameter, value in list(requirement["arguments"].items()):
                    if parameter not in REFERENCE_ARGUMENTS or not value:
                        continue
                    require_layer = call["name"] in {"manage_map_layer", "update_map_style"} and parameter == "layer_name"
                    try:
                        target = self.session.resolve_in(pool, value, require_layer=require_layer)
                    except AmbiguousReference:
                        target = None
                    if target:
                        requirement["arguments"][parameter] = target["layer_id"] if require_layer else target["dataset_id"]
        key = fingerprint(call["name"], normalized["args"])
        matching = [step for step in task["steps"] if step["fingerprint"] == key]
        successful = next((step for step in reversed(matching) if step["result"]["success"]), None)
        # Cache only within a task. A new request always has a new task ID.
        if successful and self.result_still_valid(call["name"], normalized["args"], successful["result"]):
            cached = deepcopy(successful["result"])
            cached["reused"] = True
            cached["message"] = "Bereits ausgeführt; vorhandenes Ergebnis wiederverwendet. " + cached["message"]
            return cached, normalized
        current_attempts = sum(step["fingerprint"] == key for step in task["steps"][state.get("turn_start", 0):])
        if current_attempts >= MAX_ATTEMPTS:
            return failure("Derselbe Schritt hat wiederholt keinen Fortschritt erzielt. Bitte Eingaben prüfen.", "no_progress"), normalized
        return None, normalized

    def result_still_valid(self, name, args, result):
        if self.session is None:
            return True
        layers = self.session.layers
        if any(key not in layers for key in result.get("layer_ids", ())):
            return False
        if any(key not in layers for key in result.get("updated_layer_ids", ())):
            return False
        # Exports are deliberately regenerated: visibility/title may have changed.
        if name in {"export_current_map", "export_statistical_table"}:
            return False
        if name == "manage_map_layer":
            target = layers.get(args["layer_name"])
            return (target is None if args["action"] == "remove" else
                    target is not None and target.visible == (args["action"] == "show"))
        if name == "set_map_title":
            return self.session.map_title == args["title"].strip()
        if name == "clear_map_title":
            return not self.session.map_title
        if name == "update_map_style":
            return self.verify(name, args, result)["success"]
        return bool(result.get("layer_ids"))

    def verify(self, name, args, result):
        if not result.get("success") or self.session is None:
            return result
        layers = self.session.layers
        problem = ""
        if any(key not in layers for key in list(result.get("layer_ids", ())) + list(result.get("updated_layer_ids", ()))):
            problem = "Ein gemeldeter Ergebnislayer fehlt in der Sitzung."
        creates_layer = name in LAYER_PRODUCERS and not (name == "aggregate_features_by_area" and args.get("create_layer") is False)
        if creates_layer and not result.get("layer_ids"):
            problem = "Das Werkzeug hat keinen Ergebnislayer angelegt."
        if name in {"export_current_map", "export_statistical_table"}:
            artifact = self.session.export_artifact
            if not artifact or not artifact.get("data"):
                problem = "Der Download-Anhang fehlt oder ist leer."
            elif name == "export_statistical_table" and not artifact["file_name"].endswith(".csv"):
                problem = "Der CSV-Anhang fehlt."
            elif name == "export_current_map":
                suffix = args["format"].casefold().lstrip(".")
                allowed = (".geojson", ".zip") if suffix == "geojson" else (f".{suffix}",)
                if not artifact["file_name"].endswith(allowed):
                    problem = "Das Exportformat stimmt nicht mit dem angeforderten Format überein."
        if name == "manage_map_layer":
            target = layers.get(args["layer_name"])
            action = args["action"].strip().casefold()
            if action == "remove" and target is not None:
                problem = "Der zu entfernende Layer existiert weiterhin."
            elif action in {"hide", "show"} and (target is None or target.visible != (action == "show")):
                problem = "Die angeforderte Sichtbarkeit wurde nicht übernommen."
        if name == "set_map_title" and self.session.map_title != args["title"].strip():
            problem = "Der angeforderte Kartentitel wurde nicht übernommen."
        if name == "clear_map_title" and self.session.map_title:
            problem = "Der Kartentitel wurde nicht entfernt."
        if name == "update_map_style":
            target = layers.get(args["layer_name"])
            if target is None:
                problem = "Der geänderte Layer fehlt."
            else:
                for parameter, field in (("color", "color"), ("weight", "weight"), ("fill_opacity", "fillOpacity")):
                    if args.get(parameter) is not None and target.style.get(field) != args[parameter]:
                        problem = f"Die Stiländerung '{parameter}' wurde nicht übernommen."
                if args.get("fill_color") is not None:
                    field = "fill_color" if target.renderer == "choropleth" else "fillColor"
                    if str(target.style.get(field, "")).casefold() != args["fill_color"].casefold():
                        problem = "Die Füllfarbe wurde nicht übernommen."
        if problem:
            return {**result, "success": False, "error_category": "verification",
                    "message": problem + " " + result.get("message", "")}
        return result

    def record_tool(self, state, call, normalized, message):
        task = deepcopy(state["task"])
        artifact = getattr(message, "artifact", None)
        result = deepcopy(artifact) if isinstance(artifact, dict) else failure(
            "Werkzeug lieferte kein strukturiertes Ausführungsergebnis. " + str(message.content), "tool_error")
        args = normalized["args"] if normalized else call["args"]
        result = self.verify(call["name"], args, result)
        key = fingerprint(call["name"], args)
        step = {"step_id": f"{task['task_id']}:{len(task['steps']) + 1}", "tool_call_id": call["id"],
                "tool": call["name"], "arguments": deepcopy(args), "original_arguments": deepcopy(call["args"]),
                "inputs": deepcopy(normalized.get("input_references", {})) if normalized else {},
                "fingerprint": key, "result": result}
        task["steps"].append(step)
        for requirement in task["plan"]:
            # Analysis tools already create visible result layers. Do not force
            # an extra duplicate visualization just to satisfy a tool-name list.
            compatible = requirement["tool"] == call["name"] or (
                requirement.get("accepts_layer_result") and call["name"] in LAYER_PRODUCERS)
            expected = dict(requirement["arguments"])
            if self.session is not None:
                for key, value in list(expected.items()):
                    if key not in REFERENCE_ARGUMENTS or not value:
                        continue
                    try:
                        pool = list(self.session.raw_view) + self.session.layer_management_view
                        require_layer = call["name"] in {"manage_map_layer", "update_map_style"} and key == "layer_name"
                        target = self.session.resolve_in(pool, value, require_layer=require_layer)
                        if target:
                            expected[key] = target["layer_id"] if require_layer else target["dataset_id"]
                    except AmbiguousReference:
                        pass
            if compatible and all(args.get(k) == v for k, v in expected.items()):
                if result["success"] or requirement["status"] != "completed":
                    requirement["status"] = "completed" if result["success"] else "failed"
        stop = ""
        category = result.get("error_category")
        attempts = sum(s["fingerprint"] == key for s in task["steps"][state.get("turn_start", 0):])
        if not result["success"]:
            if category in {"needs_input", "not_found", "ambiguous_reference"}:
                stop = "needs_input"
            elif category not in {"transient", "out_of_order"} or attempts >= MAX_ATTEMPTS:
                stop = category or "tool_error"
        elif result.get("reused") and attempts >= MAX_ATTEMPTS + 1:
            stop = "no_progress"
        if state.get("tool_count", 0) + 1 >= MAX_TOOL_STEPS:
            stop = stop or "budget"
        task["stop_reason"] = stop
        self.save(task)
        # Artifacts are kept as evidence. Explicit IDs also enter model content.
        content = result["message"] + "\n\nexecution_result=" + json.dumps(
            {key: result.get(key) for key in ("success", "dataset_ids", "layer_ids", "updated_layer_ids", "metrics", "error_category")},
            ensure_ascii=False, default=str)
        evidence = message.model_copy(update={"content": content, "artifact": result})
        return {"messages": [evidence], "task": task, "tool_count": state.get("tool_count", 0) + 1,
                "stop_reason": stop}

    def review(self, state):
        task = deepcopy(state["task"])
        missing = [entry for entry in task["plan"] if entry["status"] != "completed"]
        last = state["messages"][-1]
        text = last.content if isinstance(last.content, str) else ""
        asking = "?" in text or "？" in text
        unresolved = bool(task["steps"] and not task["steps"][-1]["result"]["success"])
        unexecuted = task.get("intent") == "execute" and not task["steps"]
        if (missing or unresolved or unexecuted) and not state.get("stop_reason") and not asking and state.get("review_count", 0) < 1:
            return {"review_action": "agent", "review_count": state.get("review_count", 0) + 1,
                    "review_note": "Required steps remain unfinished: " + json.dumps(missing, ensure_ascii=False)
                    + ". Execute them in order, or ask for the missing information. Do not claim completion."}
        return {"review_action": "finish"}

    def finish(self, state):
        task = deepcopy(state["task"])
        last = state["messages"][-1]
        text = last.content if isinstance(last, AIMessage) and isinstance(last.content, str) else ""
        missing = [entry for entry in task["plan"] if entry["status"] != "completed"]
        successful = [s for s in task["steps"] if s["result"]["success"] and not s["result"].get("reused")]
        stop = state.get("stop_reason", "")
        if not stop and task.get("intent") == "execute" and not task["steps"] and not missing:
            stop = "needs_input"
        if not stop and task["steps"] and not task["steps"][-1]["result"]["success"]:
            stop = task["steps"][-1]["result"].get("error_category") or "tool_error"
            task["stop_reason"] = stop
        waiting = stop == "needs_input" or ((missing or not successful) and ("?" in text or "？" in text))
        verified_complete = bool(task["plan"] and not missing and successful and stop in {"budget", "no_progress"})
        if task.get("intent") in {"advice", "consult", "audit"}:
            task["status"] = "advisory"
        elif verified_complete:
            task["status"] = "completed"
            text = "\n".join(self.success_reports(task, successful))
        elif stop or missing or waiting:
            task["status"] = "waiting" if waiting else ("partial" if successful else "failed")
        else:
            task["status"] = "completed"
        if not verified_complete and (stop or (missing and not waiting)):
            language = task.get("language", "de")
            labels = {
                "zh": ("已完成：", "任务尚未全部完成。", "还需要补充信息或执行后续步骤。", "请补充或更正必要的信息。", "已完成的结果会保留，可以发送“继续”恢复任务。"),
                "en": ("Already completed:", "The task is not fully completed.", "Further steps or information are required.", "Please supply or correct the missing information.", "Completed results are retained. Send 'continue' to resume."),
                "de": ("Bereits abgeschlossen:", "Die Aufgabe ist noch nicht vollständig abgeschlossen.", "Weitere Schritte oder Angaben sind erforderlich.", "Bitte ergänze oder korrigiere die fehlenden Angaben.", "Die Ergebnisse bleiben erhalten. Die Aufgabe kann mit 'Weiter' fortgesetzt werden."),
            }.get(language)
            reports = self.success_reports(task, successful)
            failed = next((s["result"]["message"] for s in reversed(task["steps"]) if not s["result"]["success"]), "")
            detail = task.get("stop_reason") if stop == "model_error" else failed
            if language == "zh":
                category = next((step["result"].get("error_category") for step in reversed(task["steps"]) if not step["result"]["success"]), stop)
                detail = {"not_found": "未找到指定的数据或图层，请核对侧栏中的图层编号或名称。",
                          "ambiguous_reference": "图层名称有歧义，请指定侧栏中的图层编号。",
                          "needs_input": "缺少必要信息，或字段、坐标系、参数不可用。",
                          "model_error": "模型服务调用失败。", "transient": "外部数据服务暂时不可用。",
                          "out_of_order": "请求中的前置步骤尚未完成。", "budget": "已达到本轮执行上限。",
                          "no_progress": "重复操作未产生新的进展，已停止重试。"}.get(category, "执行结果未通过检查，请核对数据和参数。")
            text = "\n\n".join(([labels[0] + "\n" + "\n".join(reports)] if reports else []) + [
                labels[1], detail or task.get("stop_reason") or labels[2]])
            if waiting:
                text += "\n\n" + labels[3]
            elif successful:
                text += "\n\n" + labels[4]
        if not text:
            text = ({"zh": "请补充你想执行的操作或要了解的问题。", "en": "Please clarify the request.",
                     "de": "Bitte präzisiere die Anfrage."}.get(task.get("language", "de")) if not successful
                    else "\n".join(self.success_reports(task, successful)))
        task["pending_question"] = text if task["status"] == "waiting" else ""
        self.save(task)
        # Replace the tentative final answer, so chat and execution memory agree.
        result = AIMessage(content=text, id=last.id if isinstance(last, AIMessage) and not last.tool_calls else None)
        return {"messages": [result], "task": task}
