"""Conservative multilingual routing and explicit, inspectable task requirements.

Routing never grants tools to advice. The agent can extend the initial plan
with a validated task_plan block, without an extra planning-model round trip.
"""
import re

LAYER_PRODUCERS = {
    "fetch_city_boundary_osm", "visualize_layer_on_map", "calculate_buffer_analysis",
    "calculate_area_and_density", "calculate_derived_indicator", "run_machine_learning_spatial_prediction",
    "filter_dataset_by_attribute", "aggregate_features_by_area", "calculate_feature_area_by_area",
    "query_spatial_relationship", "run_spatial_overlay", "identify_spatial_hotspots",
}


def _has(pattern, text):
    return bool(re.search(pattern, text, re.I))


def request_intent(text):
    text = text.casefold().strip()
    if _has(r"依据什么|用了哪|使用了哪|哪个图层.*统计|基于哪|which (?:layer|dataset).*(?:use|used)|what.*(?:based|used)|welch.*(?:layer|datensatz).*(?:verwend|benutz)", text):
        return {"mode": "audit", "active_text": text, "original": text}
    # Remove negated clauses before checking actions, preserving an alternative
    # after "but/instead/而是". A bare prohibition stays consultation-only.
    clauses = re.split(r"[,，;；。.!！]|\bbut\b|\binstead\b|\bsondern\b|而是|只需|只要", text)
    negation = r"不要|别|不需要|无需|仅咨询|do not|don't|without|nicht\s+(?:erstell|lösch|zeichn)|keine?\s+.*(?:erstell|zeichn)"
    active = " ".join(clause for clause in clauses if not _has(negation, clause))
    advisory = _has(r"recommend|suggest|empfehl|empfiehl|vorschlag|推荐|建议|方案|选项|which map|welche (?:karte|visualisierung|darstellung)", text)
    explain = _has(r"\bhow\b|\bwhy\b|\bexplain\b|\bwhat is\b|\bwie\b|\bwarum\b|erklär|如何|怎么|为什么|原理|解释|是什么|什么是", text)
    action = _has(
        r"\b(?:create|draw|show|display|render|plot|fetch|load|calculate|compute|filter|export|remove|delete|hide|add|set|clear|transform|run|use|apply|continue|retry)\b"
        r"|erstell|zeichn|zeig|darstell|\bvisualisier(?:e|en)?\b|berechn|entfern|lösch|ausblend|einblend|exportier|lade\b|fortsetzen|weiter|wiederhol"
        r"|生成|创建|绘制|显示|展示|添加|删除|移除|隐藏|设置|清除|计算|统计|有多少|多少个|筛选|过滤|导出|转换|执行|运行|使用|采用|继续|重试|改成|改为|改颜色|缓冲区|训练", active)
    if explain or not active.strip():
        mode = "consult"
    elif action:
        mode = "execute"
    elif advisory:
        mode = "advice" if _has(r"地图|可视化|map|visual|karte|darstellung|方案|options", text) else "consult"
    else:
        mode = "agent"
    if mode == "consult" and advisory and _has(r"地图|可视化|map|visual|karte|darstellung", text) and not explain:
        mode = "advice"
    return {"mode": mode, "active_text": active, "original": text}


# These rules supply a minimum checklist; the model can add concrete steps.
REQUIREMENTS = (
    ("fetch_city_boundary_osm", r"(?:fetch|load|get|download).*city boundar|(?:lade|hol).*stadtgrenz|(?:获取|加载|下载|查询).*边界", {}),
    ("visualize_layer_on_map", r"\b(?:show|draw|plot|render|display)\b|visualisier|zeichn|zeig|绘制|画图|显示.*(?:图层|地图)|展示.*地图|生成.*地图", {}),
    ("calculate_buffer_analysis", r"\bbuffer\b|puffer|缓冲", {}),
    ("update_map_style", r"\bstyle\b|(?:make|change|turn).*(?:green|red|blue)|(?:改成|改为|设为).*(?:色|绿|红|蓝)|样式|改颜色|farbe.*ändern", {}),
    ("export_statistical_table", r"(?:export|导出|保存).*(?:csv|统计表)|csv.*(?:export|导出)", {}),
    ("export_current_map", r"(?:export|导出|保存).*(?:html|geojson|png|pdf|地图)|(?:html|geojson|png|pdf).*(?:export|导出)", {}),
    ("manage_map_layer", r"\b(?:remove|delete)\b.*layer|entfern.*layer|lösch.*layer|(?:删除|移除).*(?:图层|地图层)|图层.*(?:删除|移除)", {"action": "remove"}),
    ("manage_map_layer", r"\bhide\b|ausblend|隐藏|隐藏图层", {"action": "hide"}),
    ("manage_map_layer", r"\bunhide\b|\bshow\b.*\bhidden\b|einblend|取消隐藏|重新显示", {"action": "show"}),
    ("clear_map_title", r"(?:clear|remove|delete).*(?:map )?title|(?:删除|清除|移除|重置).*标题", {}),
    ("set_map_title", r"(?:set|add|create).*(?:map )?title|(?:设置|添加|生成).*标题", {}),
    ("check_dataset_crs_consistency", r"(?:check|compare|prüf|检查|比较).*(?:crs|坐标系|koordinaten)", {}),
    ("transform_dataset_crs", r"(?:transform|reproject|转换|重投影).*(?:crs|epsg|坐标)", {}),
    ("check_uploaded_data_quality", r"data quality|datenqualität|数据质量|检查.*(?:几何|缺失坐标)", {}),
    ("associate_dataset_attributes", r"associate.*attribute|属性关联|属性匹配|通过.*(?:字段|属性).*关联", {}),
    ("filter_dataset_by_attribute", r"\bfilter\b|筛选|过滤", {}),
    ("aggregate_features_by_area", r"aggregate|(?:count|sum).*\b(?:area|district|region)|(?:统计|汇总|计数).*(?:行政区|区域|各区)|区域内.*(?:有多少|数量)|\bhow many.*\b(?:in|within)\b", {}),
    ("calculate_feature_area_by_area", r"(?:park|green).*area.*(?:district|region)|(?:公园|绿地).*面积.*(?:各区|行政区)|(?:各区|行政区).*(?:公园|绿地).*面积", {}),
    ("calculate_derived_indicator", r"derived indicator|per capita|人均|除以|比率|百分比|每万人", {}),
    ("rank_dataset_by_field", r"\btop[ -]?\d|\brank\b|排名|最高.*\d|最低.*\d", {}),
    ("query_spatial_relationship", r"point.in.polygon|空间关系|点在面|点落在", {}),
    ("run_spatial_overlay", r"\b(?:overlay|intersection|difference|symmetric_difference)\b|空间叠加|求交|对称差|差集", {}),
    ("identify_spatial_hotspots", r"\bhotspot|热点", {}),
    ("calculate_feature_distances", r"\bdistance|\bnearest|距离|最近邻", {}),
    ("calculate_area_and_density", r"area and density|面积和密度|面积与密度", {}),
    ("run_machine_learning_spatial_prediction", r"random forest|machine learning|随机森林|机器学习|空间预测", {}),
)


def initial_plan(intent):
    if intent["mode"] not in {"execute", "agent"}:
        return []
    text = intent["active_text"]
    items = []
    for tool, pattern, arguments in REQUIREMENTS:
        match = re.search(pattern, text, re.I)
        if match:
            # An explicit format is also an acceptance condition, not just a tool name.
            args = dict(arguments)
            if tool == "export_current_map":
                formats = re.findall(r"\b(html|geojson|png|pdf)\b", text)
                if len(set(formats)) == 1:
                    args["format"] = formats[0]
            items.append((match.start(), {"description": match.group(0), "tool": tool,
                                         "arguments": args, "status": "pending"}))
    plan = [item for _, item in sorted(items, key=lambda pair: pair[0])]
    action_text = re.sub(r"(?:刚才|之前|已经|先前|上次)(?:已经)?(?:生成|创建|计算|建立)(?:的)?"
                         r"|\b(?:previously|already|just) (?:created|generated|calculated)\b", "", text, flags=re.I)
    buffer_action = _has(r"(?:创建|生成|新建|建立|制作|构建).{0,30}缓冲|(?:计算|做).{0,15}\d+\s*(?:米|m).{0,10}缓冲"
                         r"|缓冲(?:分析|计算|处理)|(?:create|calculate|compute|make|build|add).{0,30}buffer"
                         r"|(?:erstell|berechn|anleg).{0,30}puffer|\b(?:then|and)\s+buffer\b|^\s*buffer\b", action_text)
    if not buffer_action:
        plan = [item for item in plan if item["tool"] != "calculate_buffer_analysis"]
    editing = any(item["tool"] in {"update_map_style", "manage_map_layer"} for item in plan)
    creating = _has(r"创建|生成|新建|建立|计算|\bcreate\b|\bcalculate\b|\bcompute\b|\bbuild\b|erstell|berechn|anleg", action_text)
    if editing and not creating:
        plan = [item for item in plan if item["tool"] not in LAYER_PRODUCERS]
    if any(item["tool"] == "manage_map_layer" and item["arguments"] == {"action": "show"} for item in plan):
        plan = [item for item in plan if item["tool"] != "visualize_layer_on_map"]
    for index, item in enumerate(plan):
        if item["tool"] == "visualize_layer_on_map":
            item["accepts_layer_result"] = any(previous["tool"] in LAYER_PRODUCERS for previous in plan[:index])
    return plan


def request_policy(intent):
    plan = initial_plan(intent)
    editing_only = bool(plan) and all(item["tool"] in {"update_map_style", "manage_map_layer"} for item in plan)
    count = _has(r"有多少|多少个|数量|统计|计数|\bhow many\b|\bcount\b|wie viele|zähle", intent["original"])
    draw = _has(r"绘制|画图|可视化|生成.{0,15}(?:图层|地图)|显示.{0,15}(?:地图|结果)|\b(?:draw|plot|visualize|display)\b|show.*(?:map|result)|darstell|visualisier", intent["active_text"])
    return {"editing_only": editing_only, "count_only": count and not draw}


def is_continuation(text):
    return _has(r"继续|重试|接着|沿用|同样|刚才|上一步|\b(?:continue|resume|retry|same|previous)\b|fortsetzen|\bweiter\b|wiederhol|vorher", text)
