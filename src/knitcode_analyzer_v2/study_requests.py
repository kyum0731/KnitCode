"""Compact, purpose-specific study packets and constrained Ollama responses."""

import copy
import json

from .models import digest


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def response_schema(context, purpose):
    text = {"type": "string"}
    refs = {"type": "array", "items": {"type": "string", "enum": [e["id"] for e in context["evidence"]]},
            "minItems": 1, "maxItems": 4}
    if purpose == "plan":
        stop = object_schema({"action": {"const": "stop"}, "reason": text,
                              "evidence_ids": {**refs, "minItems": 0}})
        candidates = [c["selector"] for c in context["allowed_candidates"]]
        if not candidates:
            return stop
        fields = {
            "action": {"const": "execute"}, "entry": {"type": "string", "enum": candidates},
            "args": {"type": "array", "items": text, "maxItems": 32},
            "call_args": {"type": "array"}, "call_kwargs": {"type": "object"},
            "stdin": text, "fixtures": {"type": "array", "maxItems": 5,
                "items": object_schema({"path": text, "content": text})},
            "reason": text, "evidence_ids": refs}
        branches = []
        files = [s for s in candidates if "::" not in s]
        functions = [s for s in candidates if "::" in s]
        if files:
            branches.append(object_schema({**fields, "entry": {"type": "string", "enum": files},
                "call_args": {"type": "array", "maxItems": 0}, "call_kwargs": object_schema({})}))
        if functions:
            branches.append(object_schema({**fields, "entry": {"type": "string", "enum": functions},
                "args": {"type": "array", "maxItems": 0}}))
        return {"oneOf": [*branches, stop]}
    claim = {"text": text, "kind": {"type": "string", "enum": ["observation", "inference"]},
             "evidence_ids": refs}
    def claims(paths=None, limit=3):
        fields = dict(claim)
        if paths is not None:
            if not paths:
                return {"type": "array", "maxItems": 0, "items": object_schema(fields)}
            fields = {"path": {"type": "string", "enum": paths}, **fields}
        return {"type": "array", "maxItems": limit, "items": object_schema(fields)}
    return object_schema({"purpose": object_schema(claim),
        "directories": claims(context["allowed_directories"]),
        "files": claims(context["allowed_files"]), "flow": claims(),
        "reading_order": claims(context["allowed_files"]),
        "limitations": {"type": "array", "items": text, "maxItems": 4}})


def compact_evidence(evidence):
    item = {k: copy.deepcopy(v) for k, v in evidence.items() if k != "file_sha256"}
    facts = item["facts"]
    if item["kind"] == "node_facts":
        facts["author_docstring_excerpt"] = facts.get("author_docstring_excerpt", "")[:160]
        if "candidate" in facts:
            facts["candidate"] = {k: v for k, v in facts["candidate"].items()
                                  if k in {"selector", "required_parameters"}}
    elif item["kind"] == "author_documentation":
        facts["excerpt"] = facts["excerpt"][:600]
    elif item["kind"] == "directory_structure":
        facts["definitions"] = facts.get("definitions", [])[:8]
        facts["files"] = facts.get("files", [])[:12]
    elif item["kind"] == "execution_candidate":
        facts.pop("reasons", None)
    elif item["kind"] == "input_sites":
        # Preserve complete expressions, but avoid duplicating argparse assignments.
        item["facts"] = [{k: v for k, v in site.items()
                          if k != "enclosing_expression" or "input(" in site.get("expression", "")}
                         for site in facts[:12]]
    elif item["kind"] == "runtime_observation":
        for key in ("event_sequences", "omitted_event_sequences", "id"):
            facts.pop(key, None)
    return item


def prepare_study_request(context, purpose, prompt, num_ctx, output_limit, *, structured=True):
    """Bound the serialized prompt+schema using a conservative UTF-8 byte budget.

    This is not a tokenizer. Reserve one token per UTF-8 byte plus a 256-token
    framing allowance, and cap larger contexts too. Never slice serialized JSON.
    """
    from .ai import AIError
    budget = min(18000, num_ctx - output_limit - 256)
    packet = {k: copy.deepcopy(context[k]) for k in
              ("snapshot_id", "selected_node_id", "execution_observed")}
    packet.update(context_version="study-request-2", prompt_version="knitcode-study-2-" + purpose,
                  purpose=purpose, evidence=[], attempts=[], allowed_candidates=[],
                  allowed_files=context["allowed_files"][:12],
                  allowed_directories=context["allowed_directories"][:8],
                  omissions={"evidence": len(context["evidence"]), "candidates": 0,
                             "attempts": max(0, len(context.get("attempts", [])) - 3),
                             "files": max(0, len(context["allowed_files"]) - 12),
                             "directories": max(0, len(context["allowed_directories"]) - 8)},
                  limitations="Bounded excerpts; omitted code and paths remain unknown. Do not assume missing observations mean unused code.")
    if purpose == "plan":
        packet["allowed_candidates"] = [{k: copy.deepcopy(c[k]) for k in ("selector", "file", "callable", "required_parameters")}
                                        for c in context["allowed_candidates"][:8]]
    packet["omissions"]["candidates"] = len(context["allowed_candidates"]) - len(packet["allowed_candidates"])
    for attempt in context.get("attempts", [])[-3:]:
        feedback = {k: copy.deepcopy(v) for k, v in attempt.items()
                    if k in {"number", "error", "execution_status", "trace_status", "target_exit_code", "exception_types"}}
        if "plan" in attempt:
            plan = attempt["plan"]
            feedback["entry"] = plan["entry"]
            feedback["plan_fingerprint"] = digest(plan)
            if len(compact_json(plan).encode("utf-8")) <= 1000:
                feedback["plan"] = copy.deepcopy(plan)
            else:
                feedback["plan_omitted"] = True
        if "coverage" in attempt:
            feedback["coverage"] = {k: v for k, v in attempt["coverage"].items() if k in {"total", "observed", "percent"}}
        packet["attempts"].append(feedback)

    # Anchor identity is retained for report links and the existing context contract.
    anchor = next(e for e in context["evidence"] if e["node_id"] == context["selected_node_id"])
    packet["evidence"] = [compact_evidence(anchor)]
    packet["omissions"]["evidence"] -= 1

    def size():
        schema = response_schema(packet, purpose) if structured else "json"
        # Reserve room for the final digest and omission/budget fields.
        return len((prompt + compact_json(packet) + compact_json(schema)).encode("utf-8")) + 180

    # Keep at least one candidate if possible. Reject rather than silently truncate
    # an indispensable prompt or a large signature when even that cannot fit.
    while size() > budget and len(packet["allowed_candidates"]) > 1:
        packet["allowed_candidates"].pop()
        packet["omissions"]["candidates"] += 1
    while size() > budget and packet["attempts"]:
        packet["attempts"].pop(0)
        packet["omissions"]["attempts"] = packet["omissions"].get("attempts", 0) + 1
    if size() > budget:
        raise AIError("AI 문맥 예산이 너무 작습니다. --num-ctx를 8192 이상으로 설정하거나 분석 범위를 줄이세요.")

    priorities = (["execution_candidate", "input_sites", "project_summary", "author_documentation", "package_metadata",
                   "directory_structure", "runtime_observation", "node_facts"] if purpose == "plan" else
                  ["project_summary", "supplement_observation", "author_documentation", "runtime_observation",
                   "directory_structure", "node_facts", "static_relation", "package_metadata"])
    candidates = {c["selector"] for c in packet["allowed_candidates"]}
    def priority(pair):
        index, evidence = pair
        rank = priorities.index(evidence["kind"]) + 1 if evidence["kind"] in priorities else 99
        if purpose == "plan" and evidence["kind"] == "execution_candidate":
            rank = 0 if "::" in evidence["facts"]["selector"] else 3
        return rank, -index if evidence["kind"] == "project_summary" else index
    ordered = sorted(enumerate(context["evidence"]), key=priority)
    for _, evidence in ordered:
        if evidence["id"] == anchor["id"] or evidence["kind"] not in priorities:
            continue
        if evidence["kind"] == "execution_candidate" and evidence["facts"]["selector"] not in candidates:
            continue
        item = compact_evidence(evidence)
        packet["evidence"].append(item)
        if item["kind"] == "input_sites":
            # Retain complete individual CLI/input expressions rather than losing
            # every input hint when a file has many arguments.
            while size() > budget and len(item["facts"]) > 1:
                item["facts"].pop()
            item["omitted_sites"] = len(evidence["facts"]) - len(item["facts"])
            while size() > budget and len(item["facts"]) > 1:
                item["facts"].pop()
                item["omitted_sites"] += 1
        if size() > budget:
            packet["evidence"].pop()
        else:
            packet["omissions"]["evidence"] -= 1
    packet["input_hash"] = digest(packet)
    schema = response_schema(packet, purpose)
    return packet, schema, {"input_budget_bytes": budget, "budget_method": "utf8_bytes_plus_framing",
                           "context_bytes": len(compact_json(packet).encode("utf-8")),
                           "original_context_bytes": len(compact_json(context).encode("utf-8")),
                           "omissions": packet["omissions"]}
