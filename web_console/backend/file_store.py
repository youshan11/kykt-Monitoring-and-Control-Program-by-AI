from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

from . import config


def ensure_runtime_dirs() -> None:
    for path in (
        config.RUNTIME_ROOT,
        config.UPLOAD_ROOT,
        config.BACKUP_ROOT,
        config.DATA_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def append_message(role: str, content: str) -> dict:
    ensure_runtime_dirs()
    message = {"role": role, "content": content, "created_at": utc_now_iso()}
    with config.MESSAGES_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")
    return message


def read_messages(limit: int = 200) -> list[dict]:
    if not config.MESSAGES_FILE.exists():
        return []
    lines = config.MESSAGES_FILE.read_text(encoding="utf-8").splitlines()
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


def read_log(max_bytes: int = 200_000) -> str:
    if not config.LOG_FILE.exists():
        return ""
    size = config.LOG_FILE.stat().st_size
    with config.LOG_FILE.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    if size > max_bytes:
        return "[日志过长，仅显示末尾]\n" + text
    return text


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
