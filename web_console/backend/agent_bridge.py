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
        config_draft: dict | None = None,
    ) -> AgentBridgeReply:
        prompt = (
            self._build_resume_prompt(user_message, config_draft)
            if session_id
            else self._build_initial_prompt(user_message, transcript, config_draft)
        )

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

    def _format_config_draft_instructions(self, config_draft: dict | None) -> str:
        if not config_draft or not config_draft.get("active"):
            return ""
        draft_path = config_draft.get("draft_path", "")
        return textwrap.dedent(
            f"""
            当前处于网页配置修改模式：
            - 未确认草稿 Word 文档：{draft_path}
            - 正式配置文档 project1/sampling_config.docx 在用户确认修改前不能改动。
            - 本轮如果要修改采样配置，只能修改上面的草稿 Word 文档。
            - 修改后必须运行：python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py validate --config {draft_path}
            - 修改后必须把草稿中的完整 sampling-config TOML 配置发给用户确认。
            - 用户发送“确认修改”“修改完成”或点击确认按钮后，网页后端会保存草稿；你不要自行把草稿复制到正式配置。
            - 草稿确认前，不能启动采样；如果用户要求开始采样，提醒其先确认或放弃配置修改。
            """
        ).strip()

    def _build_initial_prompt(self, user_message: str, transcript: list[dict], config_draft: dict | None = None) -> str:
        transcript_text = self._format_recent_transcript(transcript)
        draft_instructions = self._format_config_draft_instructions(config_draft)
        return textwrap.dedent(
            f"""
            你现在是 /home/kangjs/workspace/project1 的网页后端 agent。

            这个网站只是带按钮的网页版 agent 对话框。按钮发送的也是自然语言；
            你必须按 project1 根目录 AGENTS.md 和
            .agents/skills/ni-daq-sampling-control/SKILL.md 的规则处理 NI DAQ 采样任务。

            重要约束：
            - 核心逻辑仍由 project1 和项目内 skill/脚本负责。
            - 网页上传的配置已经替换为 project1/sampling_config.docx。
            - 如果用户提出采样配置修改想法，网页后端会进入配置修改模式；
              有草稿路径时必须修改草稿 Word 文档，没有草稿路径时才以
              project1/sampling_config.docx 作为当前正式配置入口。
            - 不要把 sampling_config.md、对话草稿或记忆当作生效配置。
            - 每次修改配置文档后，先校验配置，再把完整采样配置发给用户确认；
              如果用户继续提出修改，继续从当前草稿 Word 文档修改并重发完整配置，
              直到用户确认没有问题。
            - 如果用户要求开始或停止采样，仍要遵守项目规则：先总结并要求确认；
              只有收到“确认启动”“确认开始”“确认停止”“确认终止”或“y”后才能执行。
            - 如果用户把修改配置和开始采样放在同一句话里，先完成配置确认闭环，
              再单独请求开始采样确认。
            - 不要修改 web_console 代码，除非用户明确要求开发网页本身。
            - 回复应面向网页用户，优先使用中文，清晰说明你做了什么、下一步需要什么。

            {draft_instructions}

            最近网页对话记录：
            {transcript_text or "(无)"}

            本轮用户消息：
            {user_message}
            """
        ).strip()

    def _build_resume_prompt(self, user_message: str, config_draft: dict | None = None) -> str:
        draft_instructions = self._format_config_draft_instructions(config_draft)
        return textwrap.dedent(
            f"""
            网页用户继续发送消息：
            {user_message}

            请继续遵守本会话此前的 project1 NI DAQ agent 规则，并用中文回复网页用户。
            {draft_instructions}
            如果本轮涉及采样配置修改，必须修改当前草稿 Word 文档；没有草稿时才读取正式 project1/sampling_config.docx。
            校验后发送完整采样配置给用户确认，并按用户反馈继续修改直到确认无误。
            """
        ).strip()
