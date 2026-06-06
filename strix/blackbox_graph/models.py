from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


class IntentKind(StrEnum):
    RECON = "recon"
    DISCOVERY = "discovery"
    VALIDATION = "validation"


class IntentStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    CONCLUDED = "concluded"
    RELEASED = "released"
    FAILED = "failed"


class Fact(BaseModel):
    id: str = Field(default_factory=lambda: make_id("fact"))
    kind: str
    description: str
    target: str | None = None
    source_intent_id: str | None = None
    outcome: str = "confirmed"
    evidence_refs: list[str] = Field(default_factory=list)
    discovered_entities: list[dict[str, Any]] = Field(default_factory=list)
    candidate_vulnerability: dict[str, Any] | None = None
    report_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class Intent(BaseModel):
    id: str = Field(default_factory=lambda: make_id("intent"))
    kind: IntentKind
    description: str
    target: str
    source_fact_ids: list[str]
    skills: list[str] = Field(default_factory=list)
    coverage_keys: list[str] = Field(default_factory=list)
    status: IntentStatus = IntentStatus.PENDING
    worker_id: str | None = None
    claimed_at: str | None = None
    concluded_fact_id: str | None = None
    attempts: int = 0
    last_error: str | None = None
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class ReasonLease(BaseModel):
    worker_id: str
    claimed_at: str = Field(default_factory=utcnow)


class GraphState(BaseModel):
    version: int = 1
    run_name: str
    scan_mode: str
    targets: list[dict[str, Any]]
    hints: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    intents: list[Intent] = Field(default_factory=list)
    reports: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, str] = Field(default_factory=dict)
    reason_lease: ReasonLease | None = None
    reason_complete: bool = False
    revision: int = 0
    last_reason_revision: int = -1
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)

    def fact_ids(self) -> set[str]:
        return {fact.id for fact in self.facts}

    def target_values(self) -> list[str]:
        values: list[str] = []
        for target in self.targets:
            details = target.get("details", {})
            value = details.get("target_url") or details.get("target_ip")
            if value:
                values.append(str(value))
        return values

    def touch(self) -> None:
        self.revision += 1
        self.updated_at = utcnow()

    def add_fact(self, fact: Fact) -> Fact:
        self.facts.append(fact)
        self.touch()
        return fact

    def add_intent(self, intent: Intent) -> Intent:
        self.intents.append(intent)
        self.touch()
        return intent
