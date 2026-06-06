#!/usr/bin/env python3
"""
Strix 智能体界面
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import litellm
from docker.errors import DockerException
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from strix.config import Config, apply_saved_config, save_current_config
from strix.config.config import resolve_llm_config
from strix.llm.utils import (
    deepseek_completion_kwargs,
    is_deepseek_model,
    is_qwen_model,
    qwen_completion_kwargs,
    resolve_strix_model,
)


apply_saved_config()

from strix.interface.cli import run_cli  # noqa: E402
from strix.interface.tui import run_tui  # noqa: E402
from strix.interface.utils import (  # noqa: E402
    assign_workspace_subdirs,
    build_final_stats_text,
    check_docker_connection,
    clone_repository,
    collect_local_sources,
    generate_run_name,
    image_exists,
    infer_target_type,
    process_pull_line,
    resolve_diff_scope_context,
    rewrite_localhost_targets,
    validate_config_file,
    validate_llm_response,
)
from strix.runtime.docker_runtime import HOST_GATEWAY_HOSTNAME  # noqa: E402
from strix.telemetry import posthog  # noqa: E402
from strix.telemetry.tracer import get_global_tracer  # noqa: E402


logging.getLogger().setLevel(logging.ERROR)


def validate_environment() -> None:  # noqa: PLR0912, PLR0915
    console = Console()
    missing_required_vars = []
    missing_optional_vars = []

    strix_llm = Config.get("strix_llm")
    uses_strix_models = strix_llm and strix_llm.startswith("strix/")
    uses_qwen_models = is_qwen_model(strix_llm)
    uses_deepseek_models = is_deepseek_model(strix_llm)

    if not strix_llm:
        missing_required_vars.append("STRIX_LLM")

    has_base_url = uses_strix_models or any(
        [
            Config.get("dashscope_api_base") if uses_qwen_models else None,
            Config.get("deepseek_api_base") if uses_deepseek_models else None,
            Config.get("llm_api_base"),
            Config.get("litellm_base_url"),
            Config.get("ollama_api_base"),
        ]
    )

    if uses_qwen_models:
        if not (Config.get("dashscope_api_key") or Config.get("llm_api_key")):
            missing_optional_vars.append("DASHSCOPE_API_KEY")
    elif uses_deepseek_models:
        if not (Config.get("deepseek_api_key") or Config.get("llm_api_key")):
            missing_optional_vars.append("DEEPSEEK_API_KEY")
    elif not Config.get("llm_api_key"):
        missing_optional_vars.append("LLM_API_KEY")

    if not has_base_url:
        missing_optional_vars.append("LLM_API_BASE")

    if not Config.get("perplexity_api_key"):
        missing_optional_vars.append("PERPLEXITY_API_KEY")

    if not Config.get("strix_reasoning_effort"):
        missing_optional_vars.append("STRIX_REASONING_EFFORT")

    if missing_required_vars:
        error_text = Text()
        error_text.append("缺少必需的环境变量", style="bold red")
        error_text.append("\n\n", style="white")

        for var in missing_required_vars:
            error_text.append(f"• {var}", style="bold yellow")
            error_text.append(" 未设置\n", style="white")

        if missing_optional_vars:
            error_text.append("\n可选环境变量：\n", style="dim white")
            for var in missing_optional_vars:
                error_text.append(f"• {var}", style="dim yellow")
                error_text.append(" 未设置\n", style="dim white")

        error_text.append("\n必需环境变量：\n", style="white")
        for var in missing_required_vars:
            if var == "STRIX_LLM":
                error_text.append("• ", style="white")
                error_text.append("STRIX_LLM", style="bold cyan")
                error_text.append(
                    " - LiteLLM 使用的模型名（例如 `qwen3.7-max`）\n",
                    style="white",
                )

        if missing_optional_vars:
            error_text.append("\n可选环境变量：\n", style="white")
            for var in missing_optional_vars:
                if var == "LLM_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("LLM_API_KEY", style="bold cyan")
                    error_text.append(
                        " - LLM 提供方的 API 密钥"
                        "（本地模型、Vertex AI、AWS 等场景可能不需要）\n",
                        style="white",
                    )
                elif var == "LLM_API_BASE":
                    error_text.append("• ", style="white")
                    error_text.append("LLM_API_BASE", style="bold cyan")
                    error_text.append(
                        " - 自定义 API Base URL（本地模型时使用，例如 Ollama、LM Studio）\n",
                        style="white",
                    )
                elif var == "DASHSCOPE_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("DASHSCOPE_API_KEY", style="bold cyan")
                    error_text.append(" - Qwen（阿里云百炼）模型的 API 密钥\n", style="white")
                elif var == "PERPLEXITY_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("PERPLEXITY_API_KEY", style="bold cyan")
                    error_text.append(
                        " - Perplexity AI 网页搜索的 API 密钥（启用实时检索）\n",
                        style="white",
                    )
                elif var == "STRIX_REASONING_EFFORT":
                    error_text.append("• ", style="white")
                    error_text.append("STRIX_REASONING_EFFORT", style="bold cyan")
                    error_text.append(
                        " - 推理强度：none、minimal、low、medium、high、xhigh "
                        "（默认：high）\n",
                        style="white",
                    )
                elif var == "DEEPSEEK_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("DEEPSEEK_API_KEY", style="bold cyan")
                    error_text.append(" - DeepSeek 模型的 API 密钥\n", style="white")

        error_text.append("\n示例配置：\n", style="white")
        error_text.append("export STRIX_LLM='deepseek-v4-pro'\n", style="dim white")

        if missing_optional_vars:
            for var in missing_optional_vars:
                if var == "LLM_API_KEY":
                    error_text.append(
                        "export LLM_API_KEY='your-api-key-here'  "
                        "# 本地模型、Vertex AI、AWS 等场景可能不需要\n",
                        style="dim white",
                    )
                elif var == "LLM_API_BASE":
                    error_text.append(
                        "export LLM_API_BASE='http://localhost:11434'  "
                        "# 仅本地模型需要\n",
                        style="dim white",
                    )
                elif var == "DASHSCOPE_API_KEY":
                    error_text.append(
                        "export DASHSCOPE_API_KEY='your-dashscope-api-key-here'\n",
                        style="dim white",
                    )
                elif var == "PERPLEXITY_API_KEY":
                    error_text.append(
                        "export PERPLEXITY_API_KEY='your-perplexity-key-here'\n", style="dim white"
                    )
                elif var == "STRIX_REASONING_EFFORT":
                    error_text.append(
                        "export STRIX_REASONING_EFFORT='high'\n",
                        style="dim white",
                    )
                elif var == "DEEPSEEK_API_KEY":
                    error_text.append(
                        "export DEEPSEEK_API_KEY='your-deepseek-key-here'\n",
                        style="dim white",
                    )

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )

        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)


def check_docker_installed() -> None:
    if shutil.which("docker") is None:
        console = Console()
        error_text = Text()
        error_text.append("未安装 Docker", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append("在你的 PATH 中未找到 `docker` 命令。\n", style="white")
        error_text.append("请安装 Docker，并确认 `docker` 命令可用。\n\n", style="white")

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )
        console.print("\n", panel, "\n")
        sys.exit(1)


async def warm_up_llm() -> None:
    console = Console()

    try:
        model_name, api_key, api_base = resolve_llm_config()
        litellm_model, _ = resolve_strix_model(model_name)
        litellm_model = litellm_model or model_name

        test_messages = [
            {"role": "system", "content": "你是一个乐于助人的助手。"},
            {"role": "user", "content": "只回复“OK”。"},
        ]

        llm_timeout = int(Config.get("llm_timeout") or "300")

        completion_kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": test_messages,
            "timeout": llm_timeout,
        }
        if api_key:
            completion_kwargs["api_key"] = api_key
        if api_base:
            completion_kwargs["api_base"] = api_base
        if is_qwen_model(model_name):
            completion_kwargs.update(qwen_completion_kwargs())
        elif is_deepseek_model(model_name):
            completion_kwargs.update(
                deepseek_completion_kwargs(Config.get("strix_reasoning_effort"))
            )

        response = litellm.completion(**completion_kwargs)

        validate_llm_response(response)

    except Exception as e:  # noqa: BLE001
        error_text = Text()
        error_text.append("LLM 连接失败", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append("无法连接到语言模型。\n", style="white")
        error_text.append("请检查你的配置后重试。\n", style="white")
        error_text.append(f"\n错误：{e}", style="dim white")

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )

        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("strix-agent")
    except Exception:  # noqa: BLE001
        return "unknown"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strix 多智能体网络安全渗透测试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # Web 应用渗透测试
  strix --target https://example.com

  # GitHub 仓库分析
  strix --target https://github.com/user/repo
  strix --target git@github.com:user/repo.git

  # 本地代码分析
  strix --target ./my-project

  # 域名渗透测试
  strix --target example.com

  # IP 地址渗透测试
  strix --target 192.168.1.42

  # 多目标（例如：白盒测试中的源码与已部署应用）
  strix --target https://github.com/user/repo --target https://example.com
  strix --target ./my-project --target https://staging.example.com --target https://prod.example.com

  # 自定义指令（直接填写）
  strix --target example.com --instruction "Focus on authentication vulnerabilities"

  # 自定义指令（来自文件）
  strix --target example.com --instruction-file ./instructions.txt
  strix --target https://app.com --instruction-file /path/to/detailed_instructions.md
        """,
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"strix {get_version()}",
    )

    parser.add_argument(
        "-t",
        "--target",
        type=str,
        required=False,
        action="append",
        help="要测试的目标（URL、仓库、本地目录、域名或 IP 地址）。"
        "可重复指定以进行多目标扫描。",
    )
    parser.add_argument(
        "--resume",
        type=str,
        help="恢复指定 run-name 的黑盒状态图扫描。",
    )
    parser.add_argument(
        "--instruction",
        type=str,
        help="渗透测试的自定义指令。可以指定要聚焦的漏洞类型"
        "（例如：`Focus on IDOR and XSS`）、测试方法"
        "（例如：`Perform thorough authentication testing`）、"
        "测试凭据（例如：`Use the following credentials to access the app: admin:password123`），"
        "或关注区域（例如：`Check login API endpoint for security issues`）。",
    )

    parser.add_argument(
        "--instruction-file",
        type=str,
        help="包含详细自定义指令的文件路径。"
        "当指令较长或较复杂并已保存到文件时使用此选项"
        "（例如：`--instruction-file ./detailed_instructions.txt`）。",
    )

    parser.add_argument(
        "-n",
        "--non-interactive",
        action="store_true",
        help=(
            "以非交互模式运行（不显示 TUI，完成后退出）。"
            "默认是带 TUI 的交互模式。"
        ),
    )

    parser.add_argument(
        "-m",
        "--scan-mode",
        type=str,
        choices=["quick", "standard", "deep"],
        default=None,
        help=(
            "扫描模式："
            "`quick` 用于快速 CI/CD 检查，"
            "`standard` 用于常规测试，"
            "`deep` 用于深入安全审查（默认）。"
            "默认值：`deep`。"
        ),
    )

    parser.add_argument(
        "--scope-mode",
        type=str,
        choices=["auto", "diff", "full"],
        default="auto",
        help=(
            "代码目标的范围模式："
            "`auto` 在 CI/无头运行中启用 PR diff-scope，"
            "`diff` 强制使用变更文件范围，"
            "`full` 关闭 diff-scope。"
        ),
    )

    parser.add_argument(
        "--diff-base",
        type=str,
        help=(
            "用于比较的目标分支或提交（例如：`origin/main`）。"
            "默认使用仓库的默认分支。"
        ),
    )

    parser.add_argument(
        "--config",
        type=str,
        help="自定义配置文件（JSON）路径，用于替代 `~/.strix/cli-config.json`。",
    )

    args = parser.parse_args()

    if args.instruction and args.instruction_file:
        parser.error(
            "不能同时指定 --instruction 和 --instruction-file，请二选一。"
        )

    if args.instruction_file:
        instruction_path = Path(args.instruction_file)
        try:
            with instruction_path.open(encoding="utf-8") as f:
                args.instruction = f.read().strip()
                if not args.instruction:
                    parser.error(f"指令文件 “{instruction_path}” 为空")
        except Exception as e:  # noqa: BLE001
            parser.error(f"读取指令文件“{instruction_path}”失败：{e}")

    if not args.target and not args.resume:
        parser.error("必须指定至少一个 --target，或使用 --resume <run-name>")
    if args.target and args.resume:
        parser.error("--target 与 --resume 不能同时使用")

    args.targets_info = []
    for target in args.target or []:
        try:
            target_type, target_dict = infer_target_type(target)

            if target_type == "local_code":
                display_target = target_dict.get("target_path", target)
            else:
                display_target = target

            args.targets_info.append(
                {"type": target_type, "details": target_dict, "original": display_target}
            )
        except ValueError:
            parser.error(f"无效目标“{target}”")

    if args.resume:
        snapshot_path = Path("strix_runs") / args.resume / "state_graph" / "snapshot.json"
        if not snapshot_path.exists():
            parser.error(f"未找到可恢复的状态图：{snapshot_path}")
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            args.targets_info = snapshot["targets"]
            args.scan_mode = args.scan_mode or snapshot.get("scan_mode", "deep")
        except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
            parser.error(f"无法读取状态图快照：{exc}")
    else:
        args.scan_mode = args.scan_mode or "deep"
        assign_workspace_subdirs(args.targets_info)
        rewrite_localhost_targets(args.targets_info, HOST_GATEWAY_HOSTNAME)

    return args


def display_completion_message(args: argparse.Namespace, results_path: Path) -> None:
    console = Console()
    tracer = get_global_tracer()

    scan_completed = False
    if tracer and tracer.scan_results:
        scan_completed = tracer.scan_results.get("scan_completed", False)

    completion_text = Text()
    if scan_completed:
        completion_text.append("渗透测试已完成", style="bold #22c55e")
    else:
        completion_text.append("会话已结束", style="bold #eab308")

    target_text = Text()
    target_text.append("目标", style="dim")
    target_text.append("  ")
    if len(args.targets_info) == 1:
        target_text.append(args.targets_info[0]["original"], style="bold white")
    else:
        target_text.append(f"{len(args.targets_info)} 个目标", style="bold white")
        for target_info in args.targets_info:
            target_text.append("\n        ")
            target_text.append(target_info["original"], style="white")

    stats_text = build_final_stats_text(tracer)

    panel_parts = [completion_text, "\n\n", target_text]

    if stats_text.plain:
        panel_parts.extend(["\n", stats_text])

    results_text = Text()
    results_text.append("\n")
    results_text.append("输出", style="dim")
    results_text.append("  ")
    results_text.append(str(results_path), style="#60a5fa")
    panel_parts.extend(["\n", results_text])

    panel_content = Text.assemble(*panel_parts)

    border_style = "#22c55e" if scan_completed else "#eab308"

    panel = Panel(
        panel_content,
        title="[bold white]STRIX",
        title_align="left",
        border_style=border_style,
        padding=(1, 2),
    )

    console.print("\n")
    console.print(panel)
    console.print()
    console.print("[#60a5fa]strix.ai[/]  [dim]·[/]  [#60a5fa]discord.gg/strix-ai[/]")
    console.print()


def pull_docker_image() -> None:
    console = Console()
    client = check_docker_connection()

    if image_exists(client, Config.get("strix_image")):  # type: ignore[arg-type]
        return

    console.print()
    console.print(f"[dim]正在拉取镜像[/] {Config.get('strix_image')}")
    console.print("[dim yellow]这只会在首次运行时发生，可能需要几分钟...[/]")
    console.print()

    with console.status("[bold cyan]正在下载镜像层...", spinner="dots") as status:
        try:
            layers_info: dict[str, str] = {}
            last_update = ""

            for line in client.api.pull(Config.get("strix_image"), stream=True, decode=True):
                last_update = process_pull_line(line, layers_info, status, last_update)

        except DockerException as e:
            console.print()
            error_text = Text()
            error_text.append("镜像拉取失败", style="bold red")
            error_text.append("\n\n", style="white")
            error_text.append(f"无法下载：{Config.get('strix_image')}\n", style="white")
            error_text.append(str(e), style="dim red")

            panel = Panel(
                error_text,
                title="[bold white]STRIX",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
            console.print(panel, "\n")
            sys.exit(1)

    success_text = Text()
    success_text.append("Docker 镜像已就绪", style="#22c55e")
    console.print(success_text)
    console.print()


def apply_config_override(config_path: str) -> None:
    # 清理默认配置文件自动写入的环境变量
    # 这样它们就不会泄漏到自定义配置上下文中。
    for var_name in Config._applied_from_default:
        os.environ.pop(var_name, None)
    Config._applied_from_default = {}

    Config._config_file_override = validate_config_file(config_path)
    apply_saved_config(force=True)


def persist_config() -> None:
    if Config._config_file_override is None:
        save_current_config()


def main() -> None:  # noqa: PLR0912, PLR0915
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    args = parse_arguments()

    if args.config:
        apply_config_override(args.config)

    check_docker_installed()
    pull_docker_image()

    validate_environment()
    asyncio.run(warm_up_llm())

    persist_config()

    args.run_name = args.resume or generate_run_name(args.targets_info)

    for target_info in args.targets_info:
        if target_info["type"] == "repository":
            repo_url = target_info["details"]["target_repo"]
            dest_name = target_info["details"].get("workspace_subdir")
            cloned_path = clone_repository(repo_url, args.run_name, dest_name)
            target_info["details"]["cloned_repo_path"] = cloned_path

    args.local_sources = collect_local_sources(args.targets_info)
    try:
        diff_scope = resolve_diff_scope_context(
            local_sources=args.local_sources,
            scope_mode=args.scope_mode,
            diff_base=args.diff_base,
            non_interactive=args.non_interactive,
        )
    except ValueError as e:
        console = Console()
        error_text = Text()
        error_text.append("diff scope 解析失败", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append(str(e), style="white")

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )
        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)

    args.diff_scope = diff_scope.metadata
    if diff_scope.instruction_block:
        if args.instruction:
            args.instruction = f"{diff_scope.instruction_block}\n\n{args.instruction}"
        else:
            args.instruction = diff_scope.instruction_block

    is_whitebox = bool(args.local_sources)

    posthog.start(
        model=Config.get("strix_llm"),
        scan_mode=args.scan_mode,
        is_whitebox=is_whitebox,
        interactive=not args.non_interactive,
        has_instructions=bool(args.instruction),
    )

    exit_reason = "user_exit"
    try:
        if args.non_interactive:
            asyncio.run(run_cli(args))
        else:
            asyncio.run(run_tui(args))
    except KeyboardInterrupt:
        exit_reason = "interrupted"
    except Exception as e:
        exit_reason = "error"
        posthog.error("unhandled_exception", str(e))
        raise
    finally:
        tracer = get_global_tracer()
        if tracer:
            posthog.end(tracer, exit_reason=exit_reason)

    results_path = Path("strix_runs") / args.run_name
    display_completion_message(args, results_path)

    if args.non_interactive:
        tracer = get_global_tracer()
        if tracer and tracer.vulnerability_reports:
            sys.exit(2)


if __name__ == "__main__":
    main()
