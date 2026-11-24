"""Typed DAG admission and parameter resolution, independent of graph layout."""

from collections import deque
import copy
import math
from .catalog import FEATURES
from .errors import DomainError

NUMERIC = {"number", "integer", "money", "ratio", "duration", "timecode"}
ARRAYS = {"table", "order", "set", "referenceList", "fileSelection", "rangeList"}
OBJECTS = {"object", "group", "weights", "region", "mask", "schedule"}
TEXT = {"text", "reference", "expression", "datetime", "timezone"}


def validate_controls(feature_id, values):
    if feature_id not in FEATURES:
        return [{"code": "UNKNOWN_FEATURE", "feature_id": feature_id}]
    if not isinstance(values, dict):
        return [{"code": "CONTROL_OBJECT", "message": "控件值必须是对象"}]
    controls = {c["key"]: c for c in FEATURES[feature_id].get("operation", {}).get("controls", [])}
    errors = []
    for key, value in values.items():
        c = controls.get(key)
        if c is None:
            errors.append({"code": "UNKNOWN_CONTROL", "key": key})
            continue
        if value is None:
            continue  # explicit unavailable/inherit is not coerced to zero
        typ = c["type"]
        valid = True
        if typ in NUMERIC:
            valid = (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
            )
            if typ == "integer":
                valid = valid and int(value) == value
            if typ in {"money", "duration"} and valid:
                valid = value >= 0
            if typ == "money" and isinstance(value, dict):
                amount = value.get("amount")
                currency = value.get("currency")
                valid = (
                    isinstance(amount, (int, float))
                    and not isinstance(amount, bool)
                    and math.isfinite(amount)
                    and amount >= 0
                    and isinstance(currency, str)
                    and len(currency) == 3
                    and currency.isupper()
                )
            if typ == "duration" and isinstance(value, dict):
                amount = value.get("value")
                valid = (
                    isinstance(amount, (int, float))
                    and not isinstance(amount, bool)
                    and math.isfinite(amount)
                    and amount >= 0
                    and value.get("unit")
                    in ("seconds", "milliseconds", "frames", "minutes", "hours", "days")
                )
                if c.get("timeDomain") == "business_duration" and value.get("unit") == "frames":
                    valid = False
            if typ == "timecode" and isinstance(value, str):
                import re

                valid = bool(
                    re.fullmatch(r"(?:[0-9]+:)?[0-5]?[0-9]:[0-5][0-9](?:[.:][0-9]+)?", value)
                )
            if typ == "timecode" and isinstance(value, dict):
                valid = (
                    isinstance(value.get("ticks"), int)
                    and not isinstance(value.get("ticks"), bool)
                    and isinstance(value.get("timebase_num"), int)
                    and value["timebase_num"] > 0
                    and isinstance(value.get("timebase_den"), int)
                    and value["timebase_den"] > 0
                )
        elif typ == "enum":
            valid = value in c.get("options", [])
        elif typ in ARRAYS:
            valid = isinstance(value, list)
        elif typ in OBJECTS:
            valid = isinstance(value, dict)
        elif typ in TEXT:
            valid = isinstance(value, str)
            if typ == "reference" and isinstance(value, dict):
                valid = (
                    isinstance(value.get("id"), str)
                    and isinstance(value.get("kind"), str)
                    and isinstance(value.get("version"), int)
                    and not isinstance(value.get("version"), bool)
                    and value["version"] > 0
                )
        elif typ in ("pair", "range", "timeRange"):
            valid = isinstance(value, (list, dict))
            if isinstance(value, list):
                valid = len(value) == 2 and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
                )
                if valid and typ != "pair":
                    valid = value[1] >= value[0]
        if valid and typ in ("number", "integer", "ratio") and isinstance(value, (int, float)):
            bounds = c.get("range", {})
            if isinstance(bounds, dict):
                if isinstance(bounds.get("min"), (int, float)) and value < bounds["min"]:
                    valid = False
                if isinstance(bounds.get("max"), (int, float)) and value > bounds["max"]:
                    valid = False
        if valid and typ == "timezone":
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                valid = False
        if not valid:
            errors.append(
                {"code": "CONTROL_TYPE", "key": key, "expected": typ, "label": c["label"]}
            )
    return errors


def validate_workflow(spec):
    from .schemas import validate_json

    validate_json(spec)
    errors = []
    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {"valid": False, "errors": [{"code": "INVALID_GRAPH"}], "order": []}
    if not nodes:
        errors.append({"code": "EMPTY_GRAPH", "message": "流程至少需要一个节点。"})
    if len(nodes) > 1000 or len(edges) > 5000:
        errors.append({"code": "GRAPH_LIMIT", "message": "流程超出本地编译上限。"})
    node_map = {}
    inputs = {}
    outputs = {}
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append({"code": "INVALID_NODE", "index": i})
            continue
        nid = node.get("id")
        fid = node.get("feature_id", node.get("featureId"))
        if not isinstance(nid, str) or not nid or nid in node_map:
            errors.append({"code": "DUPLICATE_NODE", "id": nid})
            continue
        node_map[nid] = node
        f = FEATURES.get(fid)
        if not f:
            errors.append({"code": "UNKNOWN_FEATURE", "node": nid, "feature_id": fid})
            continue
        route = node.get("route_id", node.get("routeId"))
        if route not in {r["id"] for r in f["routes"]}:
            errors.append({"code": "ROUTE_REQUIRED", "node": nid, "route_id": route})
        for e in validate_controls(fid, node.get("parameters", {})):
            errors.append({"node": nid, **e})
        inputs[nid] = {
            x.get("entity") for x in f.get("operation", {}).get("inputs", []) if isinstance(x, dict)
        }
        outputs[nid] = {
            x.get("entity")
            for x in f.get("operation", {}).get("outputs", [])
            if isinstance(x, dict)
        }
    adjacent = {n: [] for n in node_map}
    degree = {n: 0 for n in node_map}
    seen = set()
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append({"code": "INVALID_EDGE"})
            continue
        source = edge.get("source", edge.get("from"))
        target = edge.get("target", edge.get("to"))
        if source not in node_map or target not in node_map:
            errors.append({"code": "DANGLING_EDGE", "source": source, "target": target})
            continue
        kind = edge.get("type", edge.get("kind", "data"))
        if kind in ("layout", "navigation", "feedback", "constraint"):
            errors.append(
                {
                    "code": "NONEXECUTABLE_EDGE",
                    "source": source,
                    "target": target,
                    "message": "导航、反馈与约束连线不能直接当执行依赖；循环须展开为有界子流程。",
                }
            )
            continue
        if kind not in ("data", "production_data", "reference", "control"):
            errors.append({"code": "UNKNOWN_EDGE_TYPE"})
            continue
        key = (source, target, kind)
        if key in seen:
            errors.append({"code": "DUPLICATE_EDGE", "source": source, "target": target})
            continue
        seen.add(key)
        if kind in ("data", "production_data", "reference"):
            sp = edge.get("source_port")
            tp = edge.get("target_port")
            if sp and sp not in outputs.get(source, set()):
                errors.append({"code": "SOURCE_PORT", "node": source, "port": sp})
            if tp and tp not in inputs.get(target, set()):
                errors.append({"code": "TARGET_PORT", "node": target, "port": tp})
            if sp and tp and sp != tp:
                errors.append({"code": "PORT_TYPE_MISMATCH", "source": source, "target": target})
            if not sp and not tp and not (outputs.get(source, set()) & inputs.get(target, set())):
                errors.append(
                    {
                        "code": "NO_COMPATIBLE_PORT",
                        "source": source,
                        "target": target,
                        "message": "显式绑定兼容的数据类型，或将其声明为控制边。",
                    }
                )
        adjacent[source].append(target)
        degree[target] += 1
    queue = deque(sorted(n for n, d in degree.items() if d == 0))
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for target in adjacent[n]:
            degree[target] -= 1
            if degree[target] == 0:
                queue.append(target)
    if len(order) != len(node_map):
        errors.append({"code": "CYCLE", "message": "执行图存在环；请显式展开有界循环。"})
    for key, maximum in (("max_steps", 10000), ("max_attempts", 20)):
        value = spec.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            errors.append({"code": "BOUND_REQUIRED", "field": key, "maximum": maximum})
    budget = spec.get("budget")
    if not isinstance(budget, (int, float, dict)) or isinstance(budget, bool):
        errors.append({"code": "BUDGET_REQUIRED", "message": "必须声明预算上限。"})
    elif isinstance(budget, (int, float)) and (budget < 0 or not math.isfinite(budget)):
        errors.append({"code": "INVALID_BUDGET"})
    if spec.get("loops"):
        errors.append(
            {
                "code": "LOOP_BACKEND_UNAVAILABLE",
                "message": "循环合同可保存，但此本地调度器仅执行已展开 DAG，不能假装无限/动态循环已可运行。",
            }
        )
    return {
        "valid": not errors,
        "errors": errors,
        "order": order if not errors else [],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "compiler_version": "ai-ship.dag/1",
        "requires_runtime_admission": [
            "input_versions",
            "current_rights",
            "capability",
            "budget",
            "human_locks",
        ],
        "boundary": "结构编译通过不等于每个节点的真实执行能力已具备。",
    }


LAYERS = ["project", "sequence", "track", "clip", "range", "keyframe"]
_MISSING = object()


def resolve_parameters(spec):
    from .schemas import validate_json

    validate_json(spec)
    layers = spec.get("layers", {})
    if isinstance(layers, list):
        layers = {layer.get("scope", layer.get("scope_kind")): layer for layer in layers}
    if not isinstance(layers, dict):
        raise DomainError("INVALID_LAYERS", "参数层必须按作用域提供。")
    unknown = set(layers) - set(LAYERS)
    if unknown:
        raise DomainError("UNKNOWN_SCOPE", "存在未定义参数层。", details=sorted(unknown))
    orientation = spec.get("orientation", "all")
    effective = {}
    origins = {}
    conflicts = []
    proposals = []
    locks = {}
    history = []
    for index, scope in enumerate(LAYERS):
        layer = layers.get(scope, {})
        if not isinstance(layer, dict):
            raise DomainError("INVALID_LAYER", "每层必须是对象。")
        values = layer.get("values", layer.get("parameters", {}))
        if not isinstance(values, dict):
            raise DomainError("INVALID_VALUES", "参数集合必须是对象。")
        values = copy.deepcopy(values)
        override = layer.get("orientation_overrides", {}).get(orientation, {})
        if not isinstance(override, dict):
            raise DomainError("INVALID_VARIANT", "朝向参数覆盖必须是对象。")
        values.update(override)
        for path, binding in values.items():
            if isinstance(binding, dict) and "mode" in binding:
                mode = binding["mode"]
                val = binding.get("value", _MISSING)
                if mode in ("inherit", "delete_override"):
                    continue
                if mode not in ("override", "propose"):
                    raise DomainError("INVALID_BINDING_MODE", "不支持的参数覆盖方式。")
                if val is _MISSING:
                    raise DomainError(
                        "VALUE_REQUIRED", "显式覆盖必须提供 value；空值与未提供不同。"
                    )
            else:
                mode = "override"
                val = binding
            protected = locks.get(path)
            if (
                protected
                and val != protected["captured_value"]
                and (protected["descendants"] or protected["scope"] == scope)
            ):
                conflicts.append(
                    {
                        "path": path,
                        "scope": scope,
                        "owner": protected.get("owner"),
                        "kept": protected["captured_value"],
                        "proposed": val,
                    }
                )
                proposals.append(
                    {"path": path, "scope": scope, "value": val, "reason": "human_lock"}
                )
                continue
            if mode == "propose":
                proposals.append(
                    {"path": path, "scope": scope, "value": val, "reason": "explicit_proposal"}
                )
                continue
            effective[path] = val
            origins[path] = {
                "scope": scope,
                "orientation": orientation if path in override else "all",
            }
            history.append({"path": path, "scope": scope, "value": val})
        lock_spec = layer.get("locks", [])
        if isinstance(lock_spec, dict):
            lock_spec = [
                {"path": p, **(l if isinstance(l, dict) else {})} for p, l in lock_spec.items()
            ]
        for lock in lock_spec:
            if not isinstance(lock, dict):
                raise DomainError("INVALID_LOCK", "人工锁必须为对象。")
            path = lock.get("path", lock.get("parameter_path"))
            if not path:
                raise DomainError("INVALID_LOCK", "人工锁缺少参数路径。")
            captured = lock.get("captured_value", effective.get(path, _MISSING))
            if captured is _MISSING:
                raise DomainError("LOCK_WITHOUT_VALUE", "有效值未定时不能伪装锁定成功。")
            if (
                path in locks
                and locks[path]["descendants"]
                and captured != locks[path]["captured_value"]
            ):
                conflicts.append(
                    {
                        "path": path,
                        "scope": scope,
                        "reason": "ancestor_lock",
                        "kept": locks[path]["captured_value"],
                        "proposed": captured,
                    }
                )
                continue
            locks[path] = {
                "path": path,
                "scope": scope,
                "owner": lock.get("owner"),
                "captured_value": captured,
                "descendants": lock.get("descendants", lock.get("ancestor_scope") == "descendants"),
            }
            effective[path] = captured
    return {
        "effective": effective,
        "origins": origins,
        "conflicts": conflicts,
        "proposals": proposals,
        "locks": list(locks.values()),
        "history": history,
        "order": LAYERS,
        "valid": not conflicts,
        "geometry_note": "几何父子矩阵仍须合成；此函数解析参数取值，不替代变换矩阵。",
    }
