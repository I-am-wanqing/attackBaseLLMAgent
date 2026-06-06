from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .models import GraphState


MODE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "quick": (
        "recon:alive",
        "recon:entrypoints",
        "test:auth",
        "test:access_control",
        "test:injection",
    ),
    "standard": (
        "recon:alive",
        "recon:entrypoints",
        "recon:services",
        "test:auth",
        "test:access_control",
        "test:injection",
        "test:server_side",
        "test:file_handling",
        "test:business_logic",
    ),
    "deep": (
        "recon:alive",
        "recon:entrypoints",
        "recon:services",
        "recon:deep_enumeration",
        "test:auth",
        "test:access_control",
        "test:injection",
        "test:server_side",
        "test:file_handling",
        "test:business_logic",
        "test:state_transitions",
        "test:protocol_features",
        "test:attack_chains",
    ),
}


def initialize_coverage(state: GraphState) -> None:
    requirements = MODE_REQUIREMENTS.get(state.scan_mode, MODE_REQUIREMENTS["deep"])
    for target in state.target_values():
        for requirement in requirements:
            state.coverage.setdefault(f"{target}|{requirement}", "pending")


def mark_intent_coverage(state: GraphState, coverage_keys: list[str], outcome: str) -> None:
    status = "complete" if outcome in {"confirmed", "negative", "candidate"} else "blocked"
    for key in coverage_keys:
        if key in state.coverage:
            state.coverage[key] = status


def missing_coverage(state: GraphState) -> list[str]:
    return [key for key, status in state.coverage.items() if status not in {"complete", "blocked"}]


def coverage_summary(state: GraphState) -> dict[str, int]:
    values = list(state.coverage.values())
    return {
        "total": len(values),
        "complete": values.count("complete"),
        "blocked": values.count("blocked"),
        "pending": values.count("pending"),
    }
