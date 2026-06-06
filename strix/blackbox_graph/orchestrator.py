from __future__ import annotations

# Prompt text is intentionally kept as readable contiguous strings.
# ruff: noqa: E501
import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from strix.agents import StrixAgent
from strix.agents.state import AgentState
from strix.llm.config import LLMConfig
from strix.telemetry.tracer import get_global_tracer
from strix.tools.agents_graph import agents_graph_actions

from .contracts import extract_json_object, validate_reason_result, validate_worker_result
from .coverage import coverage_summary, initialize_coverage, mark_intent_coverage, missing_coverage
from .models import Fact, GraphState, Intent, IntentKind, IntentStatus, ReasonLease, utcnow
from .store import GraphStore


LOG = logging.getLogger(__name__)
MAX_CONCURRENT_TASKS = 3
MAX_INTENT_ATTEMPTS = 2
MAX_REASON_FAILURES = 3

REQUIREMENT_TASKS: dict[str, tuple[str, list[str]]] = {
    "recon:alive": (
        "Confirm the target is reachable and identify externally visible services.",
        ["tooling/httpx", "tooling/nmap"],
    ),
    "recon:entrypoints": (
        "Map important web entry points, endpoints, parameters, forms, and APIs.",
        ["tooling/katana", "tooling/ffuf"],
    ),
    "recon:services": (
        "Enumerate exposed ports, services, versions, and technologies.",
        ["tooling/nmap", "tooling/naabu"],
    ),
    "recon:deep_enumeration": (
        "Perform deep content, subdomain, API, and hidden surface enumeration.",
        ["tooling/subfinder", "tooling/ffuf", "tooling/katana"],
    ),
    "test:auth": (
        "Test authentication, session, token, and account recovery surfaces.",
        ["vulnerabilities/authentication_jwt"],
    ),
    "test:access_control": (
        "Test horizontal and vertical access control on discovered surfaces.",
        ["vulnerabilities/idor", "vulnerabilities/broken_function_level_authorization"],
    ),
    "test:injection": (
        "Test applicable inputs for injection vulnerabilities.",
        ["vulnerabilities/sql_injection", "vulnerabilities/xss"],
    ),
    "test:server_side": (
        "Test server-side request and execution surfaces.",
        ["vulnerabilities/ssrf", "vulnerabilities/xxe", "vulnerabilities/rce"],
    ),
    "test:file_handling": (
        "Test upload, download, inclusion, and path handling surfaces.",
        ["vulnerabilities/insecure_file_uploads", "vulnerabilities/path_traversal_lfi_rfi"],
    ),
    "test:business_logic": (
        "Test workflows and business invariants for abuse.",
        ["vulnerabilities/business_logic", "vulnerabilities/race_conditions"],
    ),
    "test:state_transitions": (
        "Test workflow state transitions, replay, ordering, and race behavior.",
        ["vulnerabilities/business_logic", "vulnerabilities/race_conditions"],
    ),
    "test:protocol_features": ("Test applicable protocol-specific and advanced web behaviors.", []),
    "test:attack_chains": (
        "Revisit confirmed primitives and attempt meaningful in-scope attack chains.",
        ["vulnerabilities/business_logic"],
    ),
}


def is_pure_blackbox_scan(
    scan_config: dict[str, Any], local_sources: list[dict[str, str]] | None = None
) -> bool:
    targets = scan_config.get("targets", [])
    return (
        bool(targets)
        and not local_sources
        and all(target.get("type") in {"web_application", "ip_address"} for target in targets)
    )


class BlackboxGraphOrchestrator:
    def __init__(
        self,
        scan_config: dict[str, Any],
        *,
        scan_mode: str,
        interactive: bool = False,
        resume: bool = False,
        max_concurrent_tasks: int = MAX_CONCURRENT_TASKS,
    ):
        self.scan_config = scan_config
        self.scan_mode = scan_mode
        self.interactive = interactive
        self.max_concurrent_tasks = max_concurrent_tasks
        tracer = get_global_tracer()
        run_dir = tracer.get_run_dir() if tracer else Path("strix_runs") / scan_config["run_name"]
        self.store = GraphStore(run_dir)
        self.state = self.store.load() if resume and self.store.exists() else self._new_state()
        resumed_hint = self.scan_config.get("user_instructions", "").strip()
        if resume and resumed_hint and resumed_hint not in self.state.hints:
            self.state.hints.append(resumed_hint)
            self.state.touch()
            self.store.save(self.state, "hint.added", {"hint": resumed_hint})
        self.running: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._report_lock = asyncio.Lock()
        self._reason_failures = 0
        self._hydrate_tracer_reports()
        self._publish_summary()

    def _new_state(self) -> GraphState:
        state = GraphState(
            run_name=self.scan_config["run_name"],
            scan_mode=self.scan_mode,
            targets=self.scan_config.get("targets", []),
            hints=[self.scan_config.get("user_instructions", "")]
            if self.scan_config.get("user_instructions")
            else [],
        )
        for target in state.target_values():
            origin = state.add_fact(
                Fact(
                    id=f"origin_{len(state.facts) + 1}",
                    kind="origin",
                    target=target,
                    description=f"Authorized black-box target: {target}",
                )
            )
            state.add_intent(
                Intent(
                    kind=IntentKind.RECON,
                    target=target,
                    source_fact_ids=[origin.id],
                    description="Establish reachability, enumerate visible services, and map critical entry points.",
                    skills=["tooling/httpx", "tooling/nmap", "tooling/katana"],
                    coverage_keys=[
                        f"{target}|recon:alive",
                        f"{target}|recon:entrypoints",
                        f"{target}|recon:services",
                    ],
                )
            )
        initialize_coverage(state)
        self.store.save(state, "graph.initialized")
        return state

    async def run(self) -> dict[str, Any]:
        while True:
            await self._reap_tasks()
            self._dispatch_pending()
            if self.running:
                await asyncio.sleep(0.25)
                continue

            if any(intent.status == IntentStatus.PENDING for intent in self.state.intents):
                continue

            await self._run_reason()
            if any(intent.status == IntentStatus.PENDING for intent in self.state.intents):
                continue

            if self._can_complete():
                self._finalize_scan()
                return {"success": True, "state_graph": self.summary()}

            self._create_coverage_fallback_intents()
            if not any(intent.status == IntentStatus.PENDING for intent in self.state.intents):
                if self._reason_failures >= MAX_REASON_FAILURES:
                    self.store.save(
                        self.state,
                        "graph.failed",
                        {"reason": "reason agent repeatedly failed or produced no decision"},
                    )
                    return {
                        "success": False,
                        "error": "Reason Agent repeatedly failed or produced no completion decision.",
                        "state_graph": self.summary(),
                    }
                continue

    def summary(self) -> dict[str, Any]:
        return {
            "facts": len(self.state.facts),
            "pending_intents": sum(i.status == IntentStatus.PENDING for i in self.state.intents),
            "running_intents": sum(i.status == IntentStatus.CLAIMED for i in self.state.intents),
            "concluded_intents": sum(
                i.status == IntentStatus.CONCLUDED for i in self.state.intents
            ),
            "reason_running": self.state.reason_lease is not None,
            "coverage": coverage_summary(self.state),
            "candidates": sum(f.outcome == "candidate" for f in self.state.facts),
            "reported": len(self.state.reports),
        }

    def _dispatch_pending(self) -> None:
        available = self.max_concurrent_tasks - len(self.running)
        if available <= 0:
            return
        pending = [intent for intent in self.state.intents if intent.status == IntentStatus.PENDING]
        for intent in pending[:available]:
            intent.status = IntentStatus.CLAIMED
            intent.worker_id = f"graph-worker:{intent.id}"
            intent.claimed_at = utcnow()
            intent.updated_at = utcnow()
            intent.attempts += 1
            self.state.touch()
            self.store.save(self.state, "intent.claimed", {"intent_id": intent.id})
            self.running[intent.id] = asyncio.create_task(self._execute_intent(intent))
        self._publish_summary()

    async def _reap_tasks(self) -> None:
        done = [intent_id for intent_id, task in self.running.items() if task.done()]
        for intent_id in done:
            task = self.running.pop(intent_id)
            intent = self._intent(intent_id)
            try:
                result = task.result()
            except Exception as exc:  # noqa: BLE001
                self._release_or_fail(intent, str(exc))
                continue
            fact = self.state.add_fact(
                Fact(
                    kind=intent.kind.value,
                    target=intent.target,
                    source_intent_id=intent.id,
                    description=result["description"],
                    outcome=result["outcome"],
                    evidence_refs=result["evidence_refs"],
                    discovered_entities=result["discovered_entities"],
                    candidate_vulnerability=result["candidate_vulnerability"],
                    report_ids=[report["id"] for report in result.get("reports", [])],
                )
            )
            known_report_ids = {report["id"] for report in self.state.reports}
            self.state.reports.extend(
                report
                for report in result.get("reports", [])
                if report["id"] not in known_report_ids
            )
            intent.status = IntentStatus.CONCLUDED
            intent.concluded_fact_id = fact.id
            intent.worker_id = None
            intent.updated_at = utcnow()
            mark_intent_coverage(self.state, intent.coverage_keys, fact.outcome)
            self._add_followups(intent, fact, result)
            self.state.touch()
            self.store.save(
                self.state, "intent.concluded", {"intent_id": intent.id, "fact_id": fact.id}
            )
        self._publish_summary()

    async def _execute_intent(self, intent: Intent) -> dict[str, Any]:
        result = await self._run_agent(
            name=f"Graph {intent.kind.value.title()} Agent",
            task=self._worker_prompt(intent),
            skills=intent.skills,
            role="worker",
        )
        parsed = validate_worker_result(extract_json_object(result))
        if intent.kind == IntentKind.VALIDATION and parsed["outcome"] == "confirmed":
            async with self._report_lock:
                parsed["reports"] = await self._run_reporting_agent(intent, parsed)
            if not parsed["reports"]:
                raise ValueError("confirmed validation did not produce a vulnerability report")
        return parsed

    async def _run_reason(self) -> None:
        if (
            self.state.reason_lease is not None
            or self.state.last_reason_revision == self.state.revision
        ):
            return
        worker_id = "blackbox-reason"
        self.state.reason_lease = ReasonLease(worker_id=worker_id)
        self.store.save(self.state, "reason.claimed")
        try:
            raw = await self._run_agent(
                name="Blackbox Reason Agent",
                task=self._reason_prompt(),
                skills=[],
                role="reason",
            )
            result = validate_reason_result(extract_json_object(raw))
            self.state.reason_complete = result["complete"]
            for item in result["intents"]:
                self._add_reason_intent(item)
            if result["complete"] or result["intents"]:
                self._reason_failures = 0
            else:
                self._reason_failures += 1
        except Exception as exc:  # noqa: BLE001
            LOG.warning("blackbox reason failed: %s", exc)
            self.state.reason_complete = False
            self._reason_failures += 1
        finally:
            self.state.reason_lease = None
            self.state.last_reason_revision = self.state.revision
            self.state.touch()
            self.store.save(self.state, "reason.released")
            self._publish_summary()

    async def _run_agent(
        self, *, name: str, task: str, skills: list[str], role: str = "worker"
    ) -> str:
        state = AgentState(
            task=task, agent_name=name, parent_id="blackbox-orchestrator", max_iterations=300
        )
        state.context["blackbox_graph_worker"] = True
        state.context["blackbox_graph_role"] = role
        agent = StrixAgent(
            {
                "state": state,
                "llm_config": LLMConfig(
                    skills=skills[:5],
                    scan_mode=self.scan_mode,
                    is_whitebox=False,
                    interactive=False,
                ),
            }
        )
        await agent.agent_loop(task)
        node = agents_graph_actions._agent_graph["nodes"].get(state.agent_id, {})
        result = node.get("result") or {}
        summary = result.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError(f"agent {name} did not return a structured result_summary")
        return summary

    async def _run_reporting_agent(
        self, intent: Intent, result: dict[str, Any]
    ) -> list[dict[str, Any]]:
        tracer = get_global_tracer()
        before = {report["id"] for report in tracer.vulnerability_reports} if tracer else set()
        await self._run_agent(
            name="Blackbox Reporting Agent",
            skills=[],
            role="reporting",
            task=(
                "Create exactly one vulnerability report for the independently confirmed black-box "
                "finding below using create_vulnerability_report, then call agent_finish. Do not create "
                "other agents.\n\n"
                f"Target: {intent.target}\nFinding:\n{json.dumps(result, ensure_ascii=False, indent=2)}\n\n"
                'For agent_finish, set result_summary to {"outcome":"confirmed","description":"report created",'
                '"evidence_refs":[],"discovered_entities":[],"candidate_vulnerability":null,"recommended_followups":[]}.'
            ),
        )
        if not tracer:
            return []
        return [report for report in tracer.vulnerability_reports if report["id"] not in before]

    def _worker_prompt(self, intent: Intent) -> str:
        facts = [
            fact.model_dump(mode="json")
            for fact in self.state.facts
            if fact.id in intent.source_fact_ids
        ]
        return (
            "You are executing one task from a black-box security exploration state graph. "
            "Stay strictly within the authorized target. Do not create subagents. Perform only this "
            "single task, preserve useful evidence in shared files, then call agent_finish.\n\n"
            f"Intent ID: {intent.id}\nKind: {intent.kind.value}\nTarget: {intent.target}\n"
            f"Task: {intent.description}\nSource facts:\n{json.dumps(facts, ensure_ascii=False, indent=2)}\n\n"
            "Set agent_finish.result_summary to ONLY a JSON object with keys: outcome "
            "(confirmed|negative|inconclusive|candidate), description, evidence_refs (array), "
            "discovered_entities (array of objects), candidate_vulnerability (object or null), and "
            "recommended_followups (array of objects with kind, description, target, skills, coverage_keys). "
            "Use candidate only for a plausible vulnerability that needs independent validation. "
            "Use confirmed for validated non-vulnerability discoveries or a confirmed validation result."
        )

    def _reason_prompt(self) -> str:
        graph = {
            "targets": self.state.target_values(),
            "facts": [fact.model_dump(mode="json") for fact in self.state.facts],
            "intents": [intent.model_dump(mode="json") for intent in self.state.intents],
            "missing_coverage": missing_coverage(self.state),
            "hints": self.state.hints,
        }
        return (
            "You are the Reason Agent for a black-box security state graph. Read the complete graph, "
            "identify valuable next recon/discovery/validation tasks, and do not execute testing or create "
            "subagents. Every proposed task must remain within the listed targets and cite existing source "
            "fact IDs. Do not repeat completed work. Call agent_finish with result_summary set to ONLY a JSON "
            'object: {"complete": boolean, "intents": [{"kind":"recon|discovery|validation",'
            '"description":"...","target":"...","source_fact_ids":["..."],"skills":["..."],'
            '"coverage_keys":["..."]}]}. Set complete true only when no valuable direction remains.\n\n'
            f"{json.dumps(graph, ensure_ascii=False, indent=2)}"
        )

    def _add_followups(self, intent: Intent, fact: Fact, result: dict[str, Any]) -> None:
        if result["outcome"] == "candidate":
            self._add_intent_if_valid(
                Intent(
                    kind=IntentKind.VALIDATION,
                    target=intent.target,
                    source_fact_ids=[fact.id],
                    description=f"Independently validate candidate vulnerability: {fact.description}",
                    skills=intent.skills,
                )
            )
        for followup in result["recommended_followups"]:
            try:
                followup_intent = Intent(
                    kind=IntentKind(followup.get("kind", "discovery")),
                    target=str(followup.get("target") or intent.target),
                    source_fact_ids=[fact.id],
                    description=str(followup["description"]),
                    skills=[str(item) for item in followup.get("skills", [])][:5],
                    coverage_keys=[str(item) for item in followup.get("coverage_keys", [])],
                )
            except (KeyError, ValueError):
                continue
            self._add_intent_if_valid(followup_intent)

    def _add_reason_intent(self, item: dict[str, Any]) -> None:
        source_ids = item["source_fact_ids"] or [self.state.facts[-1].id]
        self._add_intent_if_valid(
            Intent(
                kind=IntentKind(item["kind"]),
                description=item["description"],
                target=item["target"],
                source_fact_ids=source_ids,
                skills=item["skills"],
                coverage_keys=item["coverage_keys"],
            )
        )

    def _add_intent_if_valid(self, intent: Intent) -> bool:
        if not self._target_in_scope(intent.target):
            return False
        if not set(intent.source_fact_ids).issubset(self.state.fact_ids()):
            return False
        intent.coverage_keys = [
            key
            for key in intent.coverage_keys
            if key in self.state.coverage and key.startswith(f"{intent.target}|")
        ]
        signature = (
            intent.kind.value,
            intent.target.lower(),
            " ".join(intent.description.lower().split()),
        )
        for existing in self.state.intents:
            existing_signature = (
                existing.kind.value,
                existing.target.lower(),
                " ".join(existing.description.lower().split()),
            )
            if existing_signature == signature:
                return False
        self.state.add_intent(intent)
        return True

    def _target_in_scope(self, candidate: str) -> bool:
        for target in self.state.target_values():
            if candidate == target:
                return True
            parsed_target = urlparse(target if "://" in target else f"//{target}")
            parsed_candidate = urlparse(candidate if "://" in candidate else f"//{candidate}")
            if parsed_target.hostname and parsed_target.hostname == parsed_candidate.hostname:
                return True
        return False

    def _create_coverage_fallback_intents(self) -> None:
        for key in missing_coverage(self.state):
            target, requirement = key.split("|", 1)
            description, skills = REQUIREMENT_TASKS[requirement]
            source_id = next(
                (fact.id for fact in reversed(self.state.facts) if fact.target == target),
                self.state.facts[0].id,
            )
            kind = IntentKind.RECON if requirement.startswith("recon:") else IntentKind.DISCOVERY
            self._add_intent_if_valid(
                Intent(
                    kind=kind,
                    target=target,
                    source_fact_ids=[source_id],
                    description=description,
                    skills=skills,
                    coverage_keys=[key],
                )
            )
        self.store.save(self.state, "coverage.intents_created")

    def _release_or_fail(self, intent: Intent, error: str) -> None:
        intent.worker_id = None
        intent.claimed_at = None
        intent.last_error = error[:1000]
        intent.updated_at = utcnow()
        intent.status = (
            IntentStatus.PENDING if intent.attempts < MAX_INTENT_ATTEMPTS else IntentStatus.FAILED
        )
        if intent.status == IntentStatus.FAILED:
            mark_intent_coverage(self.state, intent.coverage_keys, "inconclusive")
        self.state.touch()
        self.store.save(
            self.state,
            "intent.failed",
            {"intent_id": intent.id, "retry": intent.status == IntentStatus.PENDING},
        )

    def _can_complete(self) -> bool:
        return (
            self.state.reason_complete
            and not missing_coverage(self.state)
            and not self.running
            and not any(
                intent.status in {IntentStatus.PENDING, IntentStatus.CLAIMED}
                for intent in self.state.intents
            )
        )

    def _finalize_scan(self) -> None:
        tracer = get_global_tracer()
        summary = self.summary()
        if tracer:
            tracer.update_scan_final_fields(
                executive_summary=f"Completed state-graph black-box assessment of {len(self.state.target_values())} authorized target(s).",
                methodology="Fact/Intent state-space search with programmatic coverage gates, specialized discovery agents, and independent validation.",
                technical_analysis=f"Graph summary: {json.dumps(summary, ensure_ascii=False)}",
                recommendations="Review confirmed vulnerability reports and retained state graph evidence. Re-run or resume when scope or application state changes.",
            )
        self.store.save(self.state, "graph.completed")
        self._publish_summary()

    def _publish_summary(self) -> None:
        tracer = get_global_tracer()
        if tracer:
            tracer.blackbox_graph_summary = self.summary()

    def _hydrate_tracer_reports(self) -> None:
        tracer = get_global_tracer()
        if not tracer or not self.state.reports:
            return
        known = {report["id"] for report in tracer.vulnerability_reports}
        restored = [report for report in self.state.reports if report.get("id") not in known]
        tracer.vulnerability_reports.extend(restored)
        tracer._saved_vuln_ids.update(report["id"] for report in restored)

    def _intent(self, intent_id: str) -> Intent:
        return next(intent for intent in self.state.intents if intent.id == intent_id)
