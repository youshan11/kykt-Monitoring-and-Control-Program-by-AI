from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap

from . import config


SESSION_ID_RE = re.compile(r"session id:\s*([0-9a-fA-F-]{36})")


class AgentBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentBridgeReply:
    content: str
    session_id: str | None = None


class CodexCliAgentBridge:
    def __init__(self) -> None:
        self.project_root = config.PROJECT_ROOT

    def chat(
        self,
        user_message: str,
        transcript: list[dict],
        session_id: str | None = None,
    ) -> AgentBridgeReply:
        prompt = self._build_resume_prompt(user_message) if session_id else self._build_initial_prompt(user_message, transcript)

        with tempfile.NamedTemporaryFile(
            mode="w+", encoding="utf-8", suffix=".txt", dir=config.RUNTIME_ROOT, delete=False
        ) as output_file:
            output_path = Path(output_file.name)

        cmd = self._build_resume_command(session_id, output_path) if session_id else self._build_initial_command(output_path)
        proc = self._run_codex(cmd, prompt)

        reply = output_path.read_text(encoding="utf-8", errors="replace").strip()
        try:
            output_path.unlink()
        except OSError:
            pass

        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "未知错误"
            if reply:
                detail = f"{reply}\n\n{detail}"
            raise AgentBridgeError(detail)

        parsed_session_id = session_id or self._parse_session_id(proc.stdout) or self._parse_session_id(proc.stderr)
        return AgentBridgeReply(
            content=reply or proc.stdout.strip() or "(agent 没有返回文本)",
            session_id=parsed_session_id,
        )

    def _build_initial_command(self, output_path: Path) -> list[str]:
        return [
            config.CODEX_BIN,
            "exec",
            "-C",
            str(self.project_root),
            *config.codex_exec_args(),
            "-o",
            str(output_path),
            "-",
        ]

    def _build_resume_command(self, session_id: str, output_path: Path) -> list[str]:
        return [
            config.CODEX_BIN,
            "exec",
            "resume",
            *config.codex_resume_args(),
            "-o",
            str(output_path),
            session_id,
            "-",
        ]

    def _run_codex(self, cmd: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                input=prompt,
                text=True,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=config.CODEX_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentBridgeError("agent 执行超时") from exc
        except OSError as exc:
            raise AgentBridgeError(f"无法启动 agent: {exc}") from exc

    def _parse_session_id(self, stdout: str) -> str | None:
        match = SESSION_ID_RE.search(stdout or "")
        return match.group(1) if match else None

    def _format_recent_transcript(self, transcript: list[dict]) -> str:
        recent = transcript[-max(0, config.CODEX_HISTORY_LIMIT) :]
        return "\n".join(
            f"{item.get('role', 'unknown')}: {item.get('content', '')}" for item in recent
        )

    def _build_initial_prompt(self, user_message: str, transcript: list[dict]) -> str:
        transcript_text = self._format_recent_transcript(transcript)
        return textwrap.dedent(
            f"""
            你现在是 /home/kangjs/workspace/project1 的网页后端 agent。

            这个网站只是带按钮的网页版 agent 对话框。按钮发送的也是自然语言；
            你必须按 project1 根目录 AGENTS.md 和
            .agents/skills/ni-daq-sampling-control/SKILL.md 的规则处理 NI DAQ 采样任务。

            重要约束：
            - 核心逻辑仍由 project1 和项目内 skill/脚本负责。
            - 网页上传的配置已经替换为 project1/sampling_config.docx。
            - 如果用户要求开始或停止采样，仍要遵守项目规则：先总结并要求确认；
              只有收到“确认启动”“确认开始”“确认停止”“确认终止”或“y”后才能执行。
            - 不要修改 web_console 代码，除非用户明确要求开发网页本身。
            - 回复应面向网页用户，优先使用中文，清晰说明你做了什么、下一步需要什么。

            最近网页对话记录：
            {transcript_text or "(无)"}

            本轮用户消息：
            {user_message}
            """
        ).strip()

    def _build_resume_prompt(self, user_message: str) -> str:
        return textwrap.dedent(
            f"""
            网页用户继续发送消息：
            {user_message}

            请继续遵守本会话此前的 project1 NI DAQ agent 规则，并用中文回复网页用户。
            """
        ).strip()
