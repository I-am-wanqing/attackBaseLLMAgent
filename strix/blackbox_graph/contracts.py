from __future__ import annotations

import json
import re
from typing import Any

from .models import IntentKind


def extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE))
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("no JSON object found")


def validate_worker_result(payload: dict[str, Any]) -> dict[str, Any]:
    outcome = payload.get("outcome")
    if outcome not in {"confirmed", "negative", "inconclusive", "candidate"}:
        raise ValueError("invalid worker outcome")
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("worker description is required")
    return {
        "outcome": outcome,
        "description": description.strip(),
        "evidence_refs": _string_list(payload.get("evidence_refs")),
        "discovered_entities": _dict_list(payload.get("discovered_entities")),
        "candidate_vulnerability": (
            payload.get("candidate_vulnerability")
            if isinstance(payload.get("candidate_vulnerability"), dict)
            else None
        ),
        "recommended_followups": _dict_list(payload.get("recommended_followups")),
    }


def validate_reason_result(payload: dict[str, Any]) -> dict[str, Any]:
    complete = bool(payload.get("complete", False))
    raw_intents = payload.get("intents", [])
    if not isinstance(raw_intents, list):
        raise TypeError("reason intents must be an array")
    intents: list[dict[str, Any]] = []
    for raw in raw_intents[:8]:
        if not isinstance(raw, dict):
            raise TypeError("reason intent must be an object")
        kind = raw.get("kind")
        if kind not in {item.value for item in IntentKind}:
            raise ValueError("invalid reason intent kind")
        description = raw.get("description")
        target = raw.get("target")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("reason intent description is required")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("reason intent target is required")
        intents.append(
            {
                "kind": kind,
                "description": description.strip(),
                "target": target.strip(),
                "source_fact_ids": _string_list(raw.get("source_fact_ids")),
                "skills": _string_list(raw.get("skills"))[:5],
                "coverage_keys": _string_list(raw.get("coverage_keys")),
            }
        )
    return {"complete": complete, "intents": intents}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
