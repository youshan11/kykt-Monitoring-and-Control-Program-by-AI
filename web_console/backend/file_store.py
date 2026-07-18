from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shutil
import uuid
import zipfile
import xml.etree.ElementTree as ET

from . import config


CONVERSATION_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[a-f0-9]{8}$")


def ensure_runtime_dirs() -> None:
    for path in (
        config.RUNTIME_ROOT,
        config.UPLOAD_ROOT,
        config.BACKUP_ROOT,
        config.CONFIG_DRAFT_ROOT,
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
    for path in (
        config.RUNTIME_ROOT,
        config.UPLOAD_ROOT,
        config.BACKUP_ROOT,
        config.CONFIG_DRAFT_ROOT,
        config.CONVERSATIONS_ROOT,
        config.DATA_DIR,
    ):
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


def get_codex_session_id(conversation_id: str | None = None) -> str | None:
    conversation_id = conversation_id or get_active_conversation_id()
    if not conversation_id:
        return None
    meta = _read_meta(conversation_id)
    session_id = str(meta.get("codex_session_id", "")).strip()
    return session_id or None


def set_codex_session_id(conversation_id: str, session_id: str) -> dict:
    session_id = session_id.strip()
    if not session_id:
        raise ValueError("Codex session id 不能为空")
    meta = _read_meta(conversation_id)
    meta["codex_session_id"] = session_id
    meta["updated_at"] = utc_now_iso()
    _write_meta(conversation_id, meta)
    return meta


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


def _read_config_meta() -> dict:
    if not config.CONFIG_META_FILE.exists():
        return {}
    try:
        data = json.loads(config.CONFIG_META_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_config_meta(
    display_filename: str,
    source_path: Path | None = None,
    history_id: str | None = None,
    uploaded_at: str | None = None,
) -> None:
    meta = {
        "display_filename": display_filename,
        "active_config": str(config.CONFIG_DOCX),
        "selected_at": utc_now_iso(),
    }
    if source_path is not None:
        meta["source_path"] = str(source_path)
        meta["source_stored_name"] = source_path.name
    if history_id:
        meta["history_id"] = history_id
    if uploaded_at:
        meta["uploaded_at"] = uploaded_at
    config.CONFIG_META_FILE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_config_display_filename() -> str:
    display_filename = str(_read_config_meta().get("display_filename", "")).strip()
    return display_filename or config.CONFIG_DOCX.name


def _read_config_history_records() -> list[dict]:
    if not config.CONFIG_HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(config.CONFIG_HISTORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write_config_history_records(records: list[dict]) -> None:
    config.CONFIG_HISTORY_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _safe_docx_filename(filename: str | None, default_name: str) -> str:
    name = Path((filename or "").strip()).name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
    if not name:
        name = default_name
    if not name.lower().endswith(".docx"):
        name += ".docx"
    return name[:160]


def _read_config_draft_state_file() -> dict:
    if not config.CONFIG_DRAFT_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(config.CONFIG_DRAFT_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_config_draft_state(state: dict) -> None:
    config.CONFIG_DRAFT_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _clear_config_draft_state() -> None:
    try:
        config.CONFIG_DRAFT_STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def config_draft_status() -> dict:
    ensure_runtime_dirs_without_active()
    state = _read_config_draft_state_file()
    draft_path = Path(str(state.get("draft_path", ""))) if state.get("draft_path") else None
    if not draft_path or not draft_path.is_file():
        return {"active": False}
    try:
        draft_path.relative_to(config.CONFIG_DRAFT_ROOT)
    except ValueError:
        return {"active": False}
    stat = draft_path.stat()
    return {
        "active": True,
        "id": str(state.get("id", draft_path.stem)),
        "draft_path": str(draft_path),
        "base_config_path": str(state.get("base_config_path", config.CONFIG_DOCX)),
        "base_display_filename": str(state.get("base_display_filename", config.CONFIG_DOCX.name)),
        "conversation_id": str(state.get("conversation_id", "")) or None,
        "created_at": str(state.get("created_at", "")),
        "updated_at": str(state.get("updated_at", "")),
        "size": stat.st_size,
        "suggested_filename": str(state.get("suggested_filename", "")) or f"sampling_config_{timestamp()}.docx",
    }


def _word_tag_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _word_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        tag = _word_tag_name(node)
        if tag in {"t", "instrText"} and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


def _word_block_text(container: ET.Element) -> list[str]:
    blocks: list[str] = []
    for child in list(container):
        tag = _word_tag_name(child)
        if tag == "p":
            blocks.append(_word_paragraph_text(child))
        elif tag == "tbl":
            blocks.extend(_word_table_text(child))
        else:
            blocks.extend(_word_block_text(child))
    return blocks


def _word_table_text(table: ET.Element) -> list[str]:
    rows: list[str] = []
    for row in [child for child in list(table) if _word_tag_name(child) == "tr"]:
        cells: list[str] = []
        for cell in [child for child in list(row) if _word_tag_name(child) == "tc"]:
            cell_blocks = [block for block in _word_block_text(cell) if block.strip()]
            cells.append(" / ".join(cell_blocks))
        if cells:
            rows.append("\t".join(cells))
    return rows


def _docx_part_text(docx_file: zipfile.ZipFile, part_name: str) -> str:
    try:
        root = ET.fromstring(docx_file.read(part_name))
    except (KeyError, ET.ParseError):
        return ""
    body = next((node for node in root.iter() if _word_tag_name(node) == "body"), root)
    return "\n".join(_word_block_text(body)).strip()


def _docx_preview_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as docx_file:
            sections: list[str] = []
            main_text = _docx_part_text(docx_file, "word/document.xml")
            if main_text:
                sections.append(main_text)
            for part_name in sorted(docx_file.namelist()):
                if re.match(r"word/(?:header|footer|footnotes|endnotes)\d*\.xml$", part_name):
                    part_text = _docx_part_text(docx_file, part_name)
                    if part_text:
                        sections.append(part_text)
    except zipfile.BadZipFile as exc:
        raise ValueError("配置文档不是有效的 DOCX 文件") from exc

    content = "\n\n".join(sections).strip()
    if not content:
        return "(Word 文档没有可预览的文本内容)"
    return content


def config_preview() -> dict:
    draft = config_draft_status()
    if draft.get("active"):
        path = Path(draft["draft_path"])
        display_filename = draft.get("suggested_filename") or path.name
        is_draft = True
        title = f"草稿预览：{display_filename}"
    else:
        path = config.CONFIG_DOCX
        display_filename = _read_config_display_filename()
        is_draft = False
        title = f"当前配置：{display_filename}"

    if not path.is_file():
        raise FileNotFoundError("配置文档不存在")

    stat = path.stat()
    return {
        "title": title,
        "display_filename": display_filename,
        "source_path": str(path),
        "is_draft": is_draft,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "content": _docx_preview_text(path),
    }


def ensure_config_draft(conversation_id: str | None = None, requested_by: str = "") -> dict:
    ensure_runtime_dirs()
    current = config_draft_status()
    if current.get("active"):
        return {"created": False, "draft": current}
    if not config.CONFIG_DOCX.exists():
        raise FileNotFoundError("当前配置文档不存在")

    draft_id = f"{timestamp()}_{uuid.uuid4().hex[:8]}"
    draft_path = config.CONFIG_DRAFT_ROOT / f"{draft_id}.docx"
    shutil.copy2(config.CONFIG_DOCX, draft_path)
    now = utc_now_iso()
    state = {
        "active": True,
        "id": draft_id,
        "draft_path": str(draft_path),
        "base_config_path": str(config.CONFIG_DOCX),
        "base_display_filename": _read_config_display_filename(),
        "base_history_id": str(_read_config_meta().get("history_id", "")) or None,
        "conversation_id": conversation_id,
        "created_at": now,
        "updated_at": now,
        "requested_by": requested_by[:500],
        "suggested_filename": f"sampling_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
    }
    _write_config_draft_state(state)
    return {"created": True, "draft": config_draft_status()}


def _append_config_snapshot_from_path(path: Path, display_filename: str, source: str) -> dict:
    stamp = timestamp()
    stored_filename = _safe_docx_filename(display_filename, f"sampling_config_{stamp}.docx")
    stored_path = config.UPLOAD_ROOT / f"{stamp}_{uuid.uuid4().hex[:8]}_{stored_filename}"
    shutil.copy2(path, stored_path)
    stat = stored_path.stat()
    return _append_config_history(
        {
            "id": stored_path.name,
            "filename": stored_filename,
            "stored_name": stored_path.name,
            "stored_path": str(stored_path),
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "size": stat.st_size,
            "source": source,
        }
    )


def confirm_config_draft(display_filename: str | None = None) -> dict:
    ensure_runtime_dirs()
    draft = config_draft_status()
    if not draft.get("active"):
        raise FileNotFoundError("没有待确认的配置草稿")

    draft_path = Path(draft["draft_path"])
    final_filename = _safe_docx_filename(display_filename, draft.get("suggested_filename") or f"sampling_config_{timestamp()}.docx")

    previous_record = None
    backup_path = None
    if config.CONFIG_DOCX.exists():
        previous_record = _append_config_snapshot_from_path(
            config.CONFIG_DOCX,
            _read_config_display_filename(),
            "previous_active_before_agent_confirm",
        )
        backup_path = config.BACKUP_ROOT / f"sampling_config_{timestamp()}.docx"
        shutil.copy2(config.CONFIG_DOCX, backup_path)

    confirmed_record = _append_config_snapshot_from_path(
        draft_path,
        final_filename,
        "agent_draft_confirmed",
    )
    shutil.copy2(Path(confirmed_record["stored_path"]), config.CONFIG_DOCX)
    _write_config_meta(
        final_filename,
        source_path=Path(confirmed_record["stored_path"]),
        history_id=confirmed_record["id"],
        uploaded_at=confirmed_record["uploaded_at"],
    )

    try:
        draft_path.unlink()
    except FileNotFoundError:
        pass
    _clear_config_draft_state()

    return {
        "filename": final_filename,
        "active_config": str(config.CONFIG_DOCX),
        "history_id": confirmed_record["id"],
        "confirmed_record": confirmed_record,
        "previous_record": previous_record,
        "backup_path": str(backup_path) if backup_path else None,
    }


def discard_config_draft() -> dict:
    ensure_runtime_dirs_without_active()
    draft = config_draft_status()
    if not draft.get("active"):
        raise FileNotFoundError("没有待放弃的配置草稿")
    draft_path = Path(draft["draft_path"])
    try:
        draft_path.unlink()
    except FileNotFoundError:
        pass
    _clear_config_draft_state()
    return {"discarded": True, "draft": draft}


def _upload_timestamp_iso(path: Path) -> str:
    match = re.match(r"^(\d{8})_(\d{6})_", path.name)
    if match:
        try:
            dt = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _display_filename_from_upload(path: Path) -> str:
    match = re.match(r"^\d{8}_\d{6}_(?:[a-f0-9]{8}_)?(.+)$", path.name)
    return match.group(1) if match else path.name


def _normalize_config_history_record(record: dict, upload_path: Path | None = None) -> dict | None:
    stored_name = str(record.get("stored_name") or record.get("id") or "").strip()
    if not stored_name and upload_path is not None:
        stored_name = upload_path.name
    if not stored_name:
        return None
    if Path(stored_name).name != stored_name or not stored_name.lower().endswith(".docx"):
        return None
    path = upload_path or config.UPLOAD_ROOT / stored_name
    if not path.is_file():
        return None
    stat = path.stat()
    filename = str(record.get("filename") or _display_filename_from_upload(path)).strip() or path.name
    uploaded_at = str(record.get("uploaded_at") or _upload_timestamp_iso(path)).strip()
    normalized = {
        "id": stored_name,
        "filename": filename,
        "stored_name": stored_name,
        "stored_path": str(path),
        "uploaded_at": uploaded_at,
        "size": int(record.get("size") or stat.st_size),
    }
    if record.get("source"):
        normalized["source"] = str(record.get("source"))
    return normalized


def _append_config_history(record: dict) -> dict:
    records = _read_config_history_records()
    records = [item for item in records if item.get("stored_name") != record["stored_name"]]
    records.append(record)
    _write_config_history_records(records)
    return record


def list_config_history() -> list[dict]:
    ensure_runtime_dirs_without_active()
    active_meta = _read_config_meta()
    active_stored_name = str(active_meta.get("source_stored_name", "")).strip()
    by_stored_name: dict[str, dict] = {}

    for record in _read_config_history_records():
        normalized = _normalize_config_history_record(record)
        if normalized:
            by_stored_name[normalized["stored_name"]] = normalized

    for path in config.UPLOAD_ROOT.glob("*.docx") if config.UPLOAD_ROOT.exists() else []:
        normalized = _normalize_config_history_record({}, upload_path=path)
        if normalized and normalized["stored_name"] not in by_stored_name:
            by_stored_name[normalized["stored_name"]] = normalized

    history = list(by_stored_name.values())
    for record in history:
        record["active"] = bool(active_stored_name and record["stored_name"] == active_stored_name)
    history.sort(key=lambda item: item.get("uploaded_at", ""), reverse=True)
    return history


def _resolve_config_history_record(config_id: str) -> dict:
    config_id = config_id.strip()
    if Path(config_id).name != config_id or not config_id.lower().endswith(".docx"):
        raise ValueError("非法配置历史 ID")
    for record in list_config_history():
        if record["id"] == config_id:
            return record
    raise FileNotFoundError(config_id)


def select_config_history(config_id: str) -> dict:
    ensure_runtime_dirs()
    record = _resolve_config_history_record(config_id)
    source_path = Path(record["stored_path"])

    stamp = timestamp()
    backup_path = None
    if config.CONFIG_DOCX.exists():
        backup_path = config.BACKUP_ROOT / f"sampling_config_{stamp}.docx"
        shutil.copy2(config.CONFIG_DOCX, backup_path)

    shutil.copy2(source_path, config.CONFIG_DOCX)
    _write_config_meta(
        record["filename"],
        source_path=source_path,
        history_id=record["id"],
        uploaded_at=record["uploaded_at"],
    )

    return {
        **record,
        "backup_path": str(backup_path) if backup_path else None,
        "active_config": str(config.CONFIG_DOCX),
    }


def save_uploaded_config(filename: str, data: bytes) -> dict:
    ensure_runtime_dirs()
    if not filename.lower().endswith(".docx"):
        raise ValueError("只允许上传 .docx 文件")
    if not data:
        raise ValueError("上传文件为空")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError("上传文件超过大小限制")

    stamp = timestamp()
    uploaded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    display_filename = Path(filename).name
    upload_path = config.UPLOAD_ROOT / f"{stamp}_{uuid.uuid4().hex[:8]}_{display_filename}"
    upload_path.write_bytes(data)

    history_record = _append_config_history(
        {
            "id": upload_path.name,
            "filename": display_filename,
            "stored_name": upload_path.name,
            "stored_path": str(upload_path),
            "uploaded_at": uploaded_at,
            "size": len(data),
        }
    )

    backup_path = None
    if config.CONFIG_DOCX.exists():
        backup_path = config.BACKUP_ROOT / f"sampling_config_{stamp}.docx"
        shutil.copy2(config.CONFIG_DOCX, backup_path)

    shutil.copy2(upload_path, config.CONFIG_DOCX)
    _write_config_meta(
        display_filename,
        source_path=upload_path,
        history_id=history_record["id"],
        uploaded_at=uploaded_at,
    )

    return {
        "filename": display_filename,
        "uploaded_path": str(upload_path),
        "backup_path": str(backup_path) if backup_path else None,
        "active_config": str(config.CONFIG_DOCX),
        "history_id": history_record["id"],
        "uploaded_at": uploaded_at,
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
        "display_filename": _read_config_display_filename(),
        "active_history_id": str(_read_config_meta().get("history_id", "")).strip() or None,
        "config_draft": config_draft_status(),
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
