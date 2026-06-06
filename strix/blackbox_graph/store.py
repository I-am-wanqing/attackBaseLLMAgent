from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

import yaml

from .models import GraphState, IntentStatus, utcnow


if TYPE_CHECKING:
    from pathlib import Path


class GraphStore:
    def __init__(self, run_dir: Path):
        self.root = run_dir / "state_graph"
        self.events_path = self.root / "events.jsonl"
        self.snapshot_path = self.root / "snapshot.json"
        self.yaml_path = self.root / "graph.yaml"
        self._lock = threading.RLock()

    def exists(self) -> bool:
        return self.snapshot_path.exists() or self.events_path.exists()

    def load(self) -> GraphState:
        if self.events_path.exists():
            state = self._replay_events()
        else:
            state = GraphState.model_validate_json(self.snapshot_path.read_text(encoding="utf-8"))
        for intent in state.intents:
            if intent.status == IntentStatus.CLAIMED:
                intent.status = IntentStatus.PENDING
                intent.worker_id = None
                intent.claimed_at = None
                intent.updated_at = utcnow()
        state.reason_lease = None
        return state

    def save(self, state: GraphState, event: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": utcnow(),
                "event": event,
                "payload": payload or {},
                "state": state.model_dump(mode="json"),
            }
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=True) + "\n")
            self.snapshot_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            self.yaml_path.write_text(
                yaml.safe_dump(state.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

    def _replay_events(self) -> GraphState:
        latest: dict[str, Any] | None = None
        with self.events_path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event.get("state"), dict):
                    latest = event["state"]
        if latest is None:
            raise ValueError("state graph event log contains no valid state")
        return GraphState.model_validate(latest)
