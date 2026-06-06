import re
from functools import cache
from typing import Any, ClassVar

from pygments.lexers import get_lexer_by_name
from pygments.styles import get_style_by_name
from rich.text import Text
from textual.widgets import Static

from .base_renderer import BaseToolRenderer
from .registry import register_tool_renderer


MAX_OUTPUT_LINES = 50
MAX_LINE_LENGTH = 200

STRIP_PATTERNS = [
    (
        r"\n?\[Command still running after [\d.]+s - showing output so far\.?"
        r"\s*(?:Use C-c to interrupt if needed\.)?\]"
    ),
    r"^\[Below is the output of the previous command\.\]\n?",
    r"^当前没有正在运行的命令。无法发送输入\.$",
        (
            r"^已有命令正在运行。请使用 is_input=true 向其发送输入，"
            r"或先中断它（例如使用 C-c）。$"
        ),
]


@cache
def _get_style_colors() -> dict[Any, str]:
    style = get_style_by_name("native")
    return {token: f"#{style_def['color']}" for token, style_def in style if style_def["color"]}


@register_tool_renderer
class TerminalRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "terminal_execute"
    css_classes: ClassVar[list[str]] = ["tool-call", "terminal-tool"]

    CONTROL_SEQUENCES: ClassVar[set[str]] = {
        "C-c",
        "C-d",
        "C-z",
        "C-a",
        "C-e",
        "C-k",
        "C-l",
        "C-u",
        "C-w",
        "C-r",
        "C-s",
        "C-t",
        "C-y",
        "^c",
        "^d",
        "^z",
        "^a",
        "^e",
        "^k",
        "^l",
        "^u",
        "^w",
        "^r",
        "^s",
        "^t",
        "^y",
    }
    SPECIAL_KEYS: ClassVar[set[str]] = {
        "Enter",
        "Escape",
        "Space",
        "Tab",
        "BTab",
        "BSpace",
        "DC",
        "IC",
        "Up",
        "Down",
        "Left",
        "Right",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "PgUp",
        "PgDn",
        "PPage",
        "NPage",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "F10",
        "F11",
        "F12",
    }

    @classmethod
    def _get_token_color(cls, token_type: Any) -> str | None:
        colors = _get_style_colors()
        while token_type:
            if token_type in colors:
                return colors[token_type]
            token_type = token_type.parent
        return None

    @classmethod
    def _highlight_bash(cls, code: str) -> Text:
        lexer = get_lexer_by_name("bash")
        text = Text()

        for token_type, token_value in lexer.get_tokens(code):
            if not token_value:
                continue
            color = cls._get_token_color(token_type)
            text.append(token_value, style=color)

        return text

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        args = tool_data.get("args", {})
        status = tool_data.get("status", "unknown")
        result = tool_data.get("result")

        command = args.get("command", "")
        is_input = args.get("is_input", False)

        content = cls._build_content(command, is_input, status, result)

        css_classes = cls.get_css_classes(status)
        return Static(content, classes=css_classes)

    @classmethod
    def _build_content(
        cls, command: str, is_input: bool, status: str, result: dict[str, Any] | str | None
    ) -> Text:
        text = Text()
        terminal_icon = ">_"

        if not command.strip():
            text.append(terminal_icon, style="dim")
            text.append(" ")
            text.append("正在获取日志...", style="dim")
            if result:
                cls._append_output(text, result, status, command)
            return text

        is_special = (
            command in cls.CONTROL_SEQUENCES
            or command in cls.SPECIAL_KEYS
            or command.startswith(("M-", "S-", "C-S-", "C-M-", "S-M-"))
        )

        text.append(terminal_icon, style="dim")
        text.append(" ")

        if is_special:
            text.append(command, style="#ef4444")
        elif is_input:
            text.append(">>>", style="#3b82f6")
            text.append(" ")
            text.append_text(cls._format_command(command))
        else:
            text.append("$", style="#22c55e")
            text.append(" ")
            text.append_text(cls._format_command(command))

        if result:
            cls._append_output(text, result, status, command)

        return text

    @classmethod
    def _clean_output(cls, output: str, command: str = "") -> str:
        cleaned = output

        for pattern in STRIP_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.MULTILINE)

        if cleaned.strip():
            lines = cleaned.splitlines()
            filtered_lines: list[str] = []
            for line in lines:
                if not filtered_lines and not line.strip():
                    continue
                if re.match(r"^\[STRIX_\d+\]\$\s*", line):
                    continue
                if command and line.strip() == command.strip():
                    continue
                if command and re.match(r"^[\$#>]\s*" + re.escape(command.strip()) + r"\s*$", line):
                    continue
                filtered_lines.append(line)

            while filtered_lines and re.match(r"^\[STRIX_\d+\]\$\s*", filtered_lines[-1]):
                filtered_lines.pop()

            cleaned = "\n".join(filtered_lines)

        return cleaned.strip()

    @classmethod
    def _append_output(
        cls, text: Text, result: dict[str, Any] | str, tool_status: str, command: str = ""
    ) -> None:
        if isinstance(result, str):
            if result.strip():
                text.append("\n")
                text.append_text(cls._format_output(result))
            return

        raw_output = result.get("content", "")
        output = cls._clean_output(raw_output, command)
        error = result.get("error")
        exit_code = result.get("exit_code")
        result_status = result.get("status", "")

        if error and not cls._is_status_message(error):
            text.append("\n")
            text.append("  错误：", style="bold #ef4444")
            text.append(cls._truncate_line(error), style="#ef4444")
            return

        if result_status == "running" or tool_status == "running":
            if output and output.strip():
                text.append("\n")
                formatted_output = cls._format_output(output)
                text.append_text(formatted_output)
            return

        if not output or not output.strip():
            if exit_code is not None and exit_code != 0:
                text.append("\n")
                text.append(f"  退出码 {exit_code}", style="dim #ef4444")
            return

        text.append("\n")
        formatted_output = cls._format_output(output)
        text.append_text(formatted_output)

        if exit_code is not None and exit_code != 0:
            text.append("\n")
            text.append(f"  退出码 {exit_code}", style="dim #ef4444")

    @classmethod
    def _is_status_message(cls, message: str) -> bool:
        status_patterns = [
            r"当前没有正在运行的命令",
            r"已有命令正在运行",
            r"无法发送输入",
            r"请使用 is_input=true",
            r"使用 C-c 中断",
            r"正在显示当前输出",
        ]
        return any(re.search(pattern, message) for pattern in status_patterns)

    @classmethod
    def _format_output(cls, output: str) -> Text:
        text = Text()
        lines = output.splitlines()
        total_lines = len(lines)

        head_count = MAX_OUTPUT_LINES // 2
        tail_count = MAX_OUTPUT_LINES - head_count - 1

        if total_lines <= MAX_OUTPUT_LINES:
            display_lines = lines
            truncated = False
            hidden_count = 0
        else:
            display_lines = lines[:head_count]
            truncated = True
            hidden_count = total_lines - head_count - tail_count

        for i, line in enumerate(display_lines):
            truncated_line = cls._truncate_line(line)
            text.append("  ")
            text.append(truncated_line, style="dim")
            if i < len(display_lines) - 1 or truncated:
                text.append("\n")

        if truncated:
            text.append(f"  ... 已截断 {hidden_count} 行 ...", style="dim italic")
            text.append("\n")
            tail_lines = lines[-tail_count:]
            for i, line in enumerate(tail_lines):
                truncated_line = cls._truncate_line(line)
                text.append("  ")
                text.append(truncated_line, style="dim")
                if i < len(tail_lines) - 1:
                    text.append("\n")

        return text

    @classmethod
    def _truncate_line(cls, line: str) -> str:
        clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if len(clean_line) > MAX_LINE_LENGTH:
            return line[: MAX_LINE_LENGTH - 3] + "..."
        return line

    @classmethod
    def _format_command(cls, command: str) -> Text:
        return cls._highlight_bash(command)
