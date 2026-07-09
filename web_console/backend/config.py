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

HOST = os.environ.get("WEB_CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_CONSOLE_PORT", "8765"))
MAX_UPLOAD_BYTES = int(os.environ.get("WEB_CONSOLE_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

CODEX_BIN = os.environ.get("WEB_CONSOLE_CODEX_BIN", "codex")
CODEX_TIMEOUT_SECONDS = int(os.environ.get("WEB_CONSOLE_CODEX_TIMEOUT_SECONDS", "600"))

# Default stays reasonably constrained. Hardware/SSH deployments can override with:
# WEB_CONSOLE_CODEX_EXTRA_ARGS="--dangerously-bypass-approvals-and-sandbox"
CODEX_EXTRA_ARGS = shlex.split(os.environ.get("WEB_CONSOLE_CODEX_EXTRA_ARGS", "-s workspace-write"))
