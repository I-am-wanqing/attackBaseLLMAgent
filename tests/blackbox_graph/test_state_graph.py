from pathlib import Path

import pytest

from strix.blackbox_graph.contracts import validate_reason_result, validate_worker_result
from strix.blackbox_graph.coverage import (
    initialize_coverage,
    mark_intent_coverage,
    missing_coverage,
)
from strix.blackbox_graph.models import Fact, GraphState, Intent, IntentKind, IntentStatus
from strix.blackbox_graph.orchestrator import is_pure_blackbox_scan
from strix.blackbox_graph.store import GraphStore


def _state() -> GraphState:
    return GraphState(
        run_name="test-run",
        scan_mode="quick",
        targets=[
            {
                "type": "web_application",
                "details": {"target_url": "https://example.test"},
                "original": "https://example.test",
            }
        ],
    )


def test_store_persists_and_releases_claimed_intents_on_resume(tmp_path: Path) -> None:
    state = _state()
    state.reports.append({"id": "vuln-0001", "title": "Persisted finding"})
    fact = state.add_fact(Fact(kind="origin", target="https://example.test", description="origin"))
    intent = state.add_intent(
        Intent(
            kind=IntentKind.RECON,
            target="https://example.test",
            source_fact_ids=[fact.id],
            description="map target",
            status=IntentStatus.CLAIMED,
            worker_id="worker-1",
        )
    )
    store = GraphStore(tmp_path)
    store.save(state, "test.saved")

    loaded = store.load()

    assert loaded.intents[0].id == intent.id
    assert loaded.intents[0].status == IntentStatus.PENDING
    assert loaded.intents[0].worker_id is None
    assert loaded.reports[0]["id"] == "vuln-0001"
    assert store.events_path.exists()
    assert store.snapshot_path.exists()
    assert store.yaml_path.exists()


def test_coverage_requires_all_mode_items() -> None:
    state = _state()
    initialize_coverage(state)
    assert missing_coverage(state)

    keys = list(state.coverage)
    mark_intent_coverage(state, keys, "negative")

    assert missing_coverage(state) == []


def test_worker_and_reason_contract_validation() -> None:
    worker = validate_worker_result(
        {
            "outcome": "candidate",
            "description": "Possible IDOR",
            "evidence_refs": ["/workspace/evidence.txt"],
            "candidate_vulnerability": {"type": "idor"},
        }
    )
    assert worker["outcome"] == "candidate"

    reason = validate_reason_result(
        {
            "complete": False,
            "intents": [
                {
                    "kind": "validation",
                    "description": "Validate IDOR",
                    "target": "https://example.test",
                    "source_fact_ids": ["fact_1"],
                }
            ],
        }
    )
    assert reason["intents"][0]["kind"] == "validation"

    with pytest.raises(ValueError, match="invalid worker outcome"):
        validate_worker_result({"outcome": "unknown", "description": "bad"})


def test_only_web_and_ip_targets_use_graph_orchestrator() -> None:
    pure = {
        "targets": [
            {"type": "web_application", "details": {"target_url": "https://example.test"}},
            {"type": "ip_address", "details": {"target_ip": "192.0.2.1"}},
        ]
    }
    mixed = {
        "targets": [
            {"type": "web_application", "details": {"target_url": "https://example.test"}},
            {"type": "local_code", "details": {"target_path": "/tmp/repo"}},
        ]
    }

    assert is_pure_blackbox_scan(pure) is True
    assert is_pure_blackbox_scan(pure, [{"source_path": "/tmp/repo"}]) is False
    assert is_pure_blackbox_scan(mixed) is False
