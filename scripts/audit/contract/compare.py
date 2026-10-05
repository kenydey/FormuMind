"""Compare the TS API layer (wrappers.json) with the backend OpenAPI schema (openapi.json).

Usage (from the repository root):
    node   scripts/audit/contract/extract_ts.cjs
    python scripts/audit/contract/dump_openapi.py
    python scripts/audit/contract/compare.py [category ...]

Prints a count per drift category and, for every category named on the command line, each finding. Categories:
route_missing / route_method_mismatch, query_unknown_to_backend, form_field_unknown_to_backend,
body_field_unknown_to_backend / body_required_not_sent / body_type_mismatch, resp_field_not_in_backend,
resp_array_vs_other / resp_object_vs_array / resp_type_mismatch / resp_enum_*, resp_untyped_on_backend.

Read the output as leads, not verdicts: pydantic ``populate_by_name`` aliases, ``model_validator(mode="before")`` shims
(e.g. ``constraints``, ``measured``) and FormData members show up as "unknown fields", and
``resp_ts_required_backend_optional`` is noise (fields with defaults are always serialised). The real finds of
round 4 were in ``resp_array_vs_other`` (3), ``form_field_unknown_to_backend`` (2) and ``body_required_not_sent`` (2).
"""
import collections
import json
import re
import sys

import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = json.load(open(HERE / "data" / "openapi.json"))
wr = json.load(open(HERE / "data" / "wrappers.json"))
SCHEMAS = spec["components"]["schemas"]
METHODS = {"get", "post", "put", "patch", "delete"}

ops = {}
for p, item in spec["paths"].items():
    for m, op in item.items():
        if m in METHODS:
            ops[(m.upper(), re.sub(r"\{[^}]*\}", "{}", p))] = (p, op)
by_path = collections.defaultdict(set)
for (m, p) in ops:
    by_path[p].add(m)


def deref(s, depth=0):
    if not isinstance(s, dict) or depth > 8:
        return s or {}
    if "$ref" in s:
        return deref(SCHEMAS[s["$ref"].split("/")[-1]], depth + 1)
    if "allOf" in s:
        merged = {"type": "object", "properties": {}, "required": []}
        for part in s["allOf"]:
            part = deref(part, depth + 1)
            merged["properties"].update(part.get("properties", {}))
            merged["required"] += part.get("required", [])
            if part.get("additionalProperties"):
                merged["additionalProperties"] = part["additionalProperties"]
        return merged
    for key in ("anyOf", "oneOf"):
        if key in s:
            opts = [deref(o, depth + 1) for o in s[key]]
            opts_nn = [o for o in opts if o.get("type") != "null"]
            nullable = len(opts_nn) != len(opts)
            if len(opts_nn) == 1:
                out = dict(opts_nn[0])
                out["_nullable"] = nullable
                return out
            props, req = {}, None
            for o in opts_nn:
                props.update(o.get("properties", {}))
            return {"type": "union", "properties": props, "required": [], "_opts": opts_nn, "_nullable": nullable}
    return s


def oa_kind(s):
    s = deref(s)
    t = s.get("type")
    if "enum" in s:
        return "enum"
    if t in ("integer", "number"):
        return "number"
    if t == "string":
        return "string"
    if t == "boolean":
        return "boolean"
    if t == "array":
        return "array"
    if t == "object" or "properties" in s:
        return "object"
    if t == "union":
        return "union"
    if t is None and not s:
        return "any"
    return t or "any"


def ts_kind(sh):
    if not sh:
        return "any"
    return sh.get("kind")


findings = collections.defaultdict(list)


def add(cat, rec, msg):
    findings[cat].append(f"{rec['http']} {rec['path']}  [{rec['file']}:{rec['line']} {rec['wrapper']}]  {msg}")


def compare_shape(rec, ts, oa, where, depth=0):
    """ts: TS shape dict, oa: OpenAPI schema; report drift on object props."""
    if depth > 4 or not ts:
        return
    oa = deref(oa)
    tk = ts.get("kind")
    if tk == "array":
        if oa.get("type") == "array":
            compare_shape(rec, ts.get("item"), oa.get("items", {}), where + "[]", depth + 1)
        elif oa and oa_kind(oa) not in ("any",):
            add("resp_array_vs_other", rec, f"{where}: TS array but backend {oa_kind(oa)}")
        return
    if tk == "union":
        for o in ts.get("of", []):
            compare_shape(rec, o, oa, where, depth + 1)
        return
    if tk == "object":
        props = ts.get("props", {})
        if oa.get("type") == "array":
            add("resp_object_vs_array", rec, f"{where}: TS object but backend array")
            return
        oa_props = oa.get("properties")
        if oa_props is None:
            return  # untyped dict on the backend: nothing to compare
        req = set(oa.get("required", []))
        for name, sh in props.items():
            if name not in oa_props:
                if oa.get("additionalProperties") in (True, {}) or isinstance(oa.get("additionalProperties"), dict):
                    continue
                add("resp_field_not_in_backend", rec, f"{where}.{name} ({ts_kind(sh)}{'?' if sh.get('optional') else ''}) not in backend model")
                continue
            sub = deref(oa_props[name])
            # type mismatch
            bk, fk = oa_kind(sub), sh.get("kind")
            if fk in ("number",) and bk in ("string", "boolean", "array", "object"):
                add("resp_type_mismatch", rec, f"{where}.{name}: TS number, backend {bk}")
            elif fk == "string" and bk in ("number", "boolean", "array", "object"):
                add("resp_type_mismatch", rec, f"{where}.{name}: TS string, backend {bk}")
            elif fk == "boolean" and bk in ("string", "number", "array", "object"):
                add("resp_type_mismatch", rec, f"{where}.{name}: TS boolean, backend {bk}")
            elif fk == "array" and bk in ("string", "number", "boolean", "object"):
                add("resp_type_mismatch", rec, f"{where}.{name}: TS array, backend {bk}")
            elif fk == "object" and bk in ("string", "number", "boolean", "array"):
                add("resp_type_mismatch", rec, f"{where}.{name}: TS object, backend {bk}")
            if fk == "enum" and "enum" in sub:
                extra = [v for v in sh.get("values", []) if v not in sub["enum"]]
                if extra:
                    add("resp_enum_mismatch", rec, f"{where}.{name}: TS enum has {extra}, backend {sub['enum']}")
                missing = [v for v in sub["enum"] if v not in sh.get("values", [])]
                if missing:
                    add("resp_enum_backend_extra", rec, f"{where}.{name}: backend enum has {missing} unknown to TS {sh.get('values')}")
            # non-optional in TS but backend may omit
            if not sh.get("optional") and name not in req:
                add("resp_ts_required_backend_optional", rec, f"{where}.{name}")
            if not sh.get("nullable") and not sh.get("optional") and sub.get("_nullable"):
                add("resp_ts_nonnull_backend_nullable", rec, f"{where}.{name}")
            compare_shape(rec, sh, oa_props[name], f"{where}.{name}", depth + 1)
        return


def body_schema(op):
    rb = op.get("requestBody")
    if not rb:
        return None, None
    content = rb.get("content", {})
    for ct in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded"):
        if ct in content:
            return ct, content[ct].get("schema", {})
    return None, None


for rec in wr:
    http = rec["http"].replace("(url)", "")
    key = (http, rec["path"])
    if rec["path"] in ("/health", "{}") or not rec["path"].startswith("/api"):
        continue
    if key not in ops:
        if rec["path"] in by_path:
            add("route_method_mismatch", rec, f"backend has {sorted(by_path[rec['path']])}")
        else:
            # try prefix match with optional trailing segments
            add("route_missing", rec, "")
        continue
    path, op = ops[key]
    # query params
    declared = {p["name"] for p in op.get("parameters", []) if p.get("in") == "query"}
    sent = set(rec.get("qLiteral", [])) | set(rec.get("qNames", []))
    for q in sorted(sent - declared):
        add("query_unknown_to_backend", rec, f"query param '{q}' is not declared by backend ({sorted(declared)})")
    # form names
    ct, sch = body_schema(op)
    sch = deref(sch) if sch else None
    # body
    body = rec["body"]
    if rec.get("formNames"):
        props = (sch or {}).get("properties", {})
        for f in rec["formNames"]:
            if sch is not None and props and f not in props:
                add("form_field_unknown_to_backend", rec, f"form field '{f}' not in backend multipart model {sorted(props)}")
    if body["kind"] in ("object", "typed", "cond") and sch is not None and sch.get("properties") is not None:
        props = sch.get("properties", {})
        req = set(sch.get("required", []))
        sent_fields = {f["name"] for f in body["fields"]}
        extra_ok = sch.get("additionalProperties") in (True,) or isinstance(sch.get("additionalProperties"), dict)
        if not extra_ok:
            for f in sorted(sent_fields - set(props)):
                add("body_field_unknown_to_backend", rec, f"body field '{f}' is dropped by backend model {sorted(props)[:12]}")
        for r in sorted(req - sent_fields):
            # required on backend, not present in TS body
            add("body_required_not_sent", rec, f"backend requires '{r}' but TS body doesn't provide it")
        # type compare for literal-typed shapes
        shp = body.get("shape")
        if shp and shp.get("kind") == "object":
            for name, sh in shp.get("props", {}).items():
                if name in props:
                    bk = oa_kind(props[name])
                    fk = sh.get("kind")
                    if fk == "number" and bk in ("string", "boolean", "array", "object"):
                        add("body_type_mismatch", rec, f"{name}: TS number, backend {bk}")
                    elif fk == "string" and bk in ("number", "boolean", "array", "object"):
                        add("body_type_mismatch", rec, f"{name}: TS string, backend {bk}")
                    elif fk == "boolean" and bk in ("string", "number", "array", "object"):
                        add("body_type_mismatch", rec, f"{name}: TS boolean, backend {bk}")
                    elif fk == "array" and bk in ("string", "number", "boolean", "object"):
                        add("body_type_mismatch", rec, f"{name}: TS array, backend {bk}")
                    if fk == "enum" and "enum" in deref(props[name]):
                        extra = [v for v in sh.get("values", []) if v not in deref(props[name])["enum"]]
                        if extra:
                            add("body_enum_mismatch", rec, f"{name}: TS may send {extra}, backend accepts {deref(props[name])['enum']}")
    # responses
    resp = op["responses"]
    ok = next((resp[c] for c in ("200", "201", "202") if c in resp), None)
    osch = (ok or {}).get("content", {}).get("application/json", {}).get("schema") if ok else None
    shp = (rec.get("resp") or {}).get("shape")
    if osch and shp:
        compare_shape(rec, shp, osch, "$")
    elif shp and shp.get("kind") in ("object", "array") and not osch:
        add("resp_untyped_on_backend", rec, f"TS expects {shp.get('kind')} ({(rec['resp'].get('typeText') or '')[:50]}) but backend declares no response schema")

order = sorted(findings, key=lambda k: -len(findings[k]))
print("category counts:")
for k in order:
    print(f"  {len(findings[k]):4d}  {k}")
json.dump(findings, open(HERE / "data" / "findings.json", "w"), indent=1, ensure_ascii=False)
want = sys.argv[1:] or []
for k in want:
    print("\n=== ", k)
    for line in findings.get(k, []):
        print(" ", line[:260])
