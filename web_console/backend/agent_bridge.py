from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import textwrap

from . import config


class AgentBridgeError(RuntimeError):
    pass


class CodexCliAgentBridge:
    def __init__(self) -> None:
        self.project_root = config.PROJECT_ROOT

    def chat(self, user_message: str, transcript: list[dict]) -> str:
        prompt = self._build_prompt(user_message, transcript)

        with tempfile.NamedTemporaryFile(
            mode="w+", encoding="utf-8", suffix=".txt", dir=config.RUNTIME_ROOT, delete=False
        ) as output_file:
            output_path = Path(output_file.name)

        cmd = [
            config.CODEX_BIN,
            "exec",
            "-C",
            str(self.project_root),
            *config.CODEX_EXTRA_ARGS,
            "-o",
            str(output_path),
            "-",
        ]

        try:
            proc = subprocess.run(
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

        return reply or proc.stdout.strip() or "(agent 没有返回文本)"

    def _build_prompt(self, user_message: str, transcript: list[dict]) -> str:
        recent = transcript[-20:]
        transcript_text = "\n".join(
            f"{item.get('role', 'unknown')}: {item.get('content', '')}" for item in recent
        )

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
