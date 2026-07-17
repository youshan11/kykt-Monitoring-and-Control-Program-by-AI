from pathlib import Path
import os
import shlex


PROJECT_ROOT = Path(os.environ.get("PROJECT1_ROOT", "/home/kangjs/workspace/project1")).resolve()
WEB_ROOT = PROJECT_ROOT / "web_console"
FRONTEND_ROOT = WEB_ROOT / "frontend"
RUNTIME_ROOT = WEB_ROOT / "runtime"
UPLOAD_ROOT = RUNTIME_ROOT / "uploads"
BACKUP_ROOT = RUNTIME_ROOT / "backups"

CONFIG_DOCX = PROJECT_ROOT / "sampling_config.docx"
DATA_DIR = PROJECT_ROOT / "data"
LOG_FILE = DATA_DIR / "sampling.log"
MESSAGES_FILE = RUNTIME_ROOT / "messages.jsonl"
CONVERSATIONS_ROOT = RUNTIME_ROOT / "conversations"
ACTIVE_CONVERSATION_FILE = RUNTIME_ROOT / "active_conversation.txt"
CONFIG_META_FILE = RUNTIME_ROOT / "config_meta.json"
CONFIG_HISTORY_FILE = RUNTIME_ROOT / "config_history.json"

HOST = os.environ.get("WEB_CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_CONSOLE_PORT", "8765"))
MAX_UPLOAD_BYTES = int(os.environ.get("WEB_CONSOLE_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

CODEX_BIN = os.environ.get("WEB_CONSOLE_CODEX_BIN", "codex")
CODEX_TIMEOUT_SECONDS = int(os.environ.get("WEB_CONSOLE_CODEX_TIMEOUT_SECONDS", "600"))
CODEX_HISTORY_LIMIT = int(os.environ.get("WEB_CONSOLE_CODEX_HISTORY_LIMIT", "6"))
CODEX_MODEL = os.environ.get("WEB_CONSOLE_CODEX_MODEL", "").strip()
CODEX_REASONING_EFFORT = os.environ.get("WEB_CONSOLE_CODEX_REASONING_EFFORT", "low").strip()

# Default stays reasonably constrained. Hardware/SSH deployments can override with:
# WEB_CONSOLE_CODEX_EXTRA_ARGS="--dangerously-bypass-approvals-and-sandbox"
CODEX_EXTRA_ARGS = shlex.split(os.environ.get("WEB_CONSOLE_CODEX_EXTRA_ARGS", "-s workspace-write"))
CODEX_RESUME_EXTRA_ARGS = shlex.split(os.environ.get("WEB_CONSOLE_CODEX_RESUME_EXTRA_ARGS", ""))


def _codex_model_args() -> list[str]:
    args: list[str] = []
    if CODEX_MODEL:
        args.extend(["-m", CODEX_MODEL])
    if CODEX_REASONING_EFFORT:
        args.extend(["-c", f"model_reasoning_effort={CODEX_REASONING_EFFORT}"])
    return args


def codex_exec_args() -> list[str]:
    return [*CODEX_EXTRA_ARGS, *_codex_model_args()]


def codex_resume_args() -> list[str]:
    return [*CODEX_RESUME_EXTRA_ARGS, *_codex_model_args()]
