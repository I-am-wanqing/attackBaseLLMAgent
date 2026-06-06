from typing import Any

from strix.tools.registry import register_tool


def _validate_root_agent(agent_state: Any) -> dict[str, Any] | None:
    if agent_state and hasattr(agent_state, "parent_id") and agent_state.parent_id is not None:
        return {
            "success": False,
            "error": "finish_scan_wrong_agent",
            "message": "该工具只能由根/主智能体使用",
            "suggestion": "如果你是子智能体，请改用 agents_graph 工具中的 agent_finish",
        }
    return None


def _check_active_agents(agent_state: Any = None) -> dict[str, Any] | None:
    try:
        from strix.tools.agents_graph.agents_graph_actions import _agent_graph

        if agent_state and agent_state.agent_id:
            current_agent_id = agent_state.agent_id
        else:
            return None

        active_agents = []
        stopping_agents = []

        for agent_id, node in _agent_graph["nodes"].items():
            if agent_id == current_agent_id:
                continue

            status = node.get("status", "unknown")
            if status == "running":
                active_agents.append(
                    {
                        "id": agent_id,
                        "name": node.get("name", "未知"),
                        "task": node.get("task", "未知任务")[:300],
                        "status": status,
                    }
                )
            elif status == "stopping":
                stopping_agents.append(
                    {
                        "id": agent_id,
                        "name": node.get("name", "未知"),
                        "task": node.get("task", "未知任务")[:300],
                        "status": status,
                    }
                )

        if active_agents or stopping_agents:
            response: dict[str, Any] = {
                "success": False,
                "error": "agents_still_active",
                "message": "无法结束扫描：仍有智能体处于活动状态",
            }

            if active_agents:
                response["active_agents"] = active_agents

            if stopping_agents:
                response["stopping_agents"] = stopping_agents

            response["suggestions"] = [
                "使用 wait_for_message 等待所有智能体完成",
                "如果需要让智能体立即结束，可使用 send_message_to_agent",
                "检查 agent_status 查看当前智能体状态",
            ]

            response["total_active"] = len(active_agents) + len(stopping_agents)

            return response

    except ImportError:
        pass
    except Exception:
        import logging

        logging.exception("检查活动智能体时出错")

    return None


@register_tool(sandbox_execution=False)
def finish_scan(
    executive_summary: str,
    methodology: str,
    technical_analysis: str,
    recommendations: str,
    agent_state: Any = None,
) -> dict[str, Any]:
    validation_error = _validate_root_agent(agent_state)
    if validation_error:
        return validation_error

    active_agents_error = _check_active_agents(agent_state)
    if active_agents_error:
        return active_agents_error

    validation_errors = []

    if not executive_summary or not executive_summary.strip():
        validation_errors.append("执行摘要不能为空")
    if not methodology or not methodology.strip():
        validation_errors.append("方法论不能为空")
    if not technical_analysis or not technical_analysis.strip():
        validation_errors.append("技术分析不能为空")
    if not recommendations or not recommendations.strip():
        validation_errors.append("建议不能为空")

    if validation_errors:
        return {"success": False, "message": "校验失败", "errors": validation_errors}

    try:
        from strix.telemetry.tracer import get_global_tracer

        tracer = get_global_tracer()
        if tracer:
            tracer.update_scan_final_fields(
                executive_summary=executive_summary.strip(),
                methodology=methodology.strip(),
                technical_analysis=technical_analysis.strip(),
                recommendations=recommendations.strip(),
            )

            vulnerability_count = len(tracer.vulnerability_reports)

            return {
                "success": True,
                "scan_completed": True,
                "message": "扫描已成功完成",
                "vulnerabilities_found": vulnerability_count,
            }

        import logging

        logging.warning("当前 tracer 不可用 - 扫描结果未保存")

    except (ImportError, AttributeError) as e:
        return {"success": False, "message": f"完成扫描失败：{e!s}"}
    else:
        return {
            "success": True,
            "scan_completed": True,
            "message": "扫描已完成（未持久化）",
            "warning": "结果无法持久化 - tracer 不可用",
        }
