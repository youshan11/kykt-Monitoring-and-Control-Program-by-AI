from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shutil
import uuid

from . import config


CONVERSATION_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[a-f0-9]{8}$")


def ensure_runtime_dirs() -> None:
    for path in (
        config.RUNTIME_ROOT,
        config.UPLOAD_ROOT,
        config.BACKUP_ROOT,
        config.CONVERSATIONS_ROOT,
        config.DATA_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
    migrate_legacy_messages()
    ensure_active_conversation()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _conversation_id() -> str:
    return f"{timestamp()}_{uuid.uuid4().hex[:8]}"


def _conversation_dir(conversation_id: str) -> Path:
    if not CONVERSATION_ID_RE.match(conversation_id):
        raise ValueError("非法对话 ID")
    return config.CONVERSATIONS_ROOT / conversation_id


def _messages_path(conversation_id: str) -> Path:
    return _conversation_dir(conversation_id) / "messages.jsonl"


def _meta_path(conversation_id: str) -> Path:
    return _conversation_dir(conversation_id) / "meta.json"


def _read_meta(conversation_id: str) -> dict:
    path = _meta_path(conversation_id)
    if not path.exists():
        return {
            "id": conversation_id,
            "title": conversation_id,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    data.setdefault("id", conversation_id)
    data.setdefault("title", conversation_id)
    data.setdefault("created_at", utc_now_iso())
    data.setdefault("updated_at", data["created_at"])
    return data


def _write_meta(conversation_id: str, meta: dict) -> None:
    path = _meta_path(conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def migrate_legacy_messages() -> None:
    if not config.MESSAGES_FILE.exists() or config.ACTIVE_CONVERSATION_FILE.exists():
        return
    if not config.MESSAGES_FILE.read_text(encoding="utf-8", errors="replace").strip():
        return
    conversation_id = _conversation_id()
    conv_dir = _conversation_dir(conversation_id)
    conv_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config.MESSAGES_FILE, conv_dir / "messages.jsonl")
    now = utc_now_iso()
    _write_meta(
        conversation_id,
        {
            "id": conversation_id,
            "title": "迁移的旧对话",
            "created_at": now,
            "updated_at": now,
        },
    )
    config.ACTIVE_CONVERSATION_FILE.write_text(conversation_id + "\n", encoding="utf-8")


def ensure_active_conversation() -> str:
    active = get_active_conversation_id(create_if_missing=False)
    if active:
        return active
    return create_conversation(title="新对话")["id"]


def get_active_conversation_id(create_if_missing: bool = True) -> str | None:
    if config.ACTIVE_CONVERSATION_FILE.exists():
        conversation_id = config.ACTIVE_CONVERSATION_FILE.read_text(encoding="utf-8").strip()
        if conversation_id:
            try:
                if _messages_path(conversation_id).exists():
                    return conversation_id
            except ValueError:
                pass
    if not create_if_missing:
        return None
    return ensure_active_conversation()


def set_active_conversation(conversation_id: str) -> dict:
    conv_dir = _conversation_dir(conversation_id)
    if not conv_dir.exists():
        raise FileNotFoundError(conversation_id)
    config.ACTIVE_CONVERSATION_FILE.write_text(conversation_id + "\n", encoding="utf-8")
    return _read_meta(conversation_id)


def create_conversation(title: str | None = None) -> dict:
    ensure_runtime_dirs_without_active()
    conversation_id = _conversation_id()
    now = utc_now_iso()
    conv_dir = _conversation_dir(conversation_id)
    conv_dir.mkdir(parents=True, exist_ok=False)
    (conv_dir / "messages.jsonl").write_text("", encoding="utf-8")
    meta = {
        "id": conversation_id,
        "title": (title or f"新对话 {datetime.now().strftime('%Y-%m-%d %H:%M')}").strip(),
        "created_at": now,
        "updated_at": now,
    }
    _write_meta(conversation_id, meta)
    config.ACTIVE_CONVERSATION_FILE.write_text(conversation_id + "\n", encoding="utf-8")
    return meta


def ensure_runtime_dirs_without_active() -> None:
    for path in (config.RUNTIME_ROOT, config.UPLOAD_ROOT, config.BACKUP_ROOT, config.CONVERSATIONS_ROOT, config.DATA_DIR):
        path.mkdir(parents=True, exist_ok=True)


def list_conversations() -> list[dict]:
    ensure_runtime_dirs_without_active()
    active_id = get_active_conversation_id(create_if_missing=False)
    conversations: list[dict] = []
    for path in config.CONVERSATIONS_ROOT.iterdir() if config.CONVERSATIONS_ROOT.exists() else []:
        if not path.is_dir():
            continue
        try:
            meta = _read_meta(path.name)
        except ValueError:
            continue
        meta["active"] = path.name == active_id
        try:
            meta["message_count"] = len(read_messages(path.name, limit=1000000))
        except ValueError:
            meta["message_count"] = 0
        conversations.append(meta)
    conversations.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return conversations


def rename_conversation(conversation_id: str, title: str) -> dict:
    title = title.strip()
    if not title:
        raise ValueError("对话名称不能为空")
    meta = _read_meta(conversation_id)
    meta["title"] = title[:80]
    meta["updated_at"] = utc_now_iso()
    _write_meta(conversation_id, meta)
    meta["active"] = get_active_conversation_id(create_if_missing=False) == conversation_id
    return meta


def delete_conversation(conversation_id: str) -> dict:
    conv_dir = _conversation_dir(conversation_id)
    if not conv_dir.exists():
        raise FileNotFoundError(conversation_id)
    was_active = get_active_conversation_id(create_if_missing=False) == conversation_id
    shutil.rmtree(conv_dir)
    if was_active:
        remaining = list_conversations()
        if remaining:
            set_active_conversation(remaining[0]["id"])
        else:
            create_conversation(title="新对话")
    return {"deleted": conversation_id, "active_conversation_id": get_active_conversation_id()}


def append_message(role: str, content: str, conversation_id: str | None = None) -> dict:
    ensure_runtime_dirs()
    conversation_id = conversation_id or get_active_conversation_id()
    if not conversation_id:
        conversation_id = create_conversation(title="新对话")["id"]
    message = {"role": role, "content": content, "created_at": utc_now_iso()}
    path = _messages_path(conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")
    meta = _read_meta(conversation_id)
    if role == "user" and meta.get("title", "").startswith("新对话"):
        meta["title"] = content.strip().replace("\n", " ")[:30] or meta["title"]
    meta["updated_at"] = message["created_at"]
    _write_meta(conversation_id, meta)
    return message


def read_messages(conversation_id: str | None = None, limit: int = 200) -> list[dict]:
    ensure_runtime_dirs_without_active()
    conversation_id = conversation_id or get_active_conversation_id()
    if not conversation_id:
        return []
    path = _messages_path(conversation_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    messages: list[dict] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def save_uploaded_config(filename: str, data: bytes) -> dict:
    ensure_runtime_dirs()
    if not filename.lower().endswith(".docx"):
        raise ValueError("只允许上传 .docx 文件")
    if not data:
        raise ValueError("上传文件为空")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError("上传文件超过大小限制")

    stamp = timestamp()
    upload_path = config.UPLOAD_ROOT / f"{stamp}_{Path(filename).name}"
    upload_path.write_bytes(data)

    backup_path = None
    if config.CONFIG_DOCX.exists():
        backup_path = config.BACKUP_ROOT / f"sampling_config_{stamp}.docx"
        shutil.copy2(config.CONFIG_DOCX, backup_path)

    shutil.copy2(upload_path, config.CONFIG_DOCX)

    return {
        "filename": Path(filename).name,
        "uploaded_path": str(upload_path),
        "backup_path": str(backup_path) if backup_path else None,
        "active_config": str(config.CONFIG_DOCX),
        "size": len(data),
    }


@dataclass(frozen=True)
class TdmsFile:
    name: str
    size: int
    modified_at: str


def list_tdms_files() -> list[dict]:
    if not config.DATA_DIR.exists():
        return []
    files: list[TdmsFile] = []
    for path in config.DATA_DIR.glob("*.tdms"):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            TdmsFile(
                name=path.name,
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
                    timespec="seconds"
                ),
            )
        )
    files.sort(key=lambda item: item.modified_at, reverse=True)
    return [item.__dict__ for item in files]


def resolve_tdms_download(name: str) -> Path:
    if Path(name).name != name or not name.lower().endswith(".tdms"):
        raise ValueError("非法 TDMS 文件名")
    path = (config.DATA_DIR / name).resolve()
    if path.parent != config.DATA_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return path


def config_status() -> dict:
    stat = config.CONFIG_DOCX.stat() if config.CONFIG_DOCX.exists() else None
    return {
        "project_root": str(config.PROJECT_ROOT),
        "active_config": str(config.CONFIG_DOCX),
        "config_exists": stat is not None,
        "config_size": stat.st_size if stat else 0,
        "config_modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        )
        if stat
        else None,
        "data_dir": str(config.DATA_DIR),
        "log_file": str(config.LOG_FILE),
    }
