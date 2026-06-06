import asyncio
from types import SimpleNamespace

from strix.tools.executor import execute_tool_with_validation


def test_reason_role_cannot_execute_scanning_tools() -> None:
    state = SimpleNamespace(context={"blackbox_graph_role": "reason"})

    result = asyncio.run(execute_tool_with_validation("terminal_execute", state))

    assert "not allowed for black-box graph role 'reason'" in result


def test_worker_role_cannot_create_reports() -> None:
    state = SimpleNamespace(context={"blackbox_graph_role": "worker"})

    result = asyncio.run(execute_tool_with_validation("create_vulnerability_report", state))

    assert "not allowed for black-box graph role 'worker'" in result
