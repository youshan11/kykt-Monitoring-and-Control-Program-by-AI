from __future__ import annotations

from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import json
import mimetypes
import re
import subprocess
import threading

from . import config
from .agent_bridge import AgentBridgeError, CodexCliAgentBridge
from .file_store import (
    append_message,
    config_draft_status,
    config_status,
    confirm_config_draft,
    create_conversation,
    delete_conversation,
    discard_config_draft,
    ensure_config_draft,
    ensure_runtime_dirs,
    get_active_conversation_id,
    get_codex_session_id,
    list_config_history,
    list_conversations,
    list_tdms_files,
    read_messages,
    rename_conversation,
    resolve_tdms_download,
    save_uploaded_config,
    select_config_history,
    set_active_conversation,
    set_codex_session_id,
)



START_COMMAND_PATTERNS = (
    "开始采样",
    "采样开始",
    "启动采样",
    "开始采集",
    "启动采集",
    "开始记录",
    "开始执行",
    "确认启动",
    "确认开始",
)

CONFIG_CHANGE_EXPLICIT_HINTS = (
    "修改配置",
    "更改配置",
    "调整配置",
    "改配置",
    "修改word",
    "修改 word",
    "更改word",
)

CONFIG_CHANGE_TARGETS = (
    "word文档",
    "配置文档",
    "采样配置",
    "采集配置",
    "sampling_config",
    "sample_rate",
    "sample rate",
    "采样率",
    "采样频率",
    "采样时长",
    "duration",
    "通道",
    "channel",
    "tdms",
    "电压范围",
    "输出值",
    "计数器",
    "counter",
)

CONFIG_CHANGE_VERBS = ("改", "修改", "更改", "调整", "设置", "设为", "改成", "换成", "启用", "禁用")
DRAFT_CONFIRM_PATTERNS = ("确认修改", "修改完成", "保存修改", "确认保存", "保存配置", "配置确认", "确认配置")
DRAFT_DISCARD_PATTERNS = ("放弃修改", "取消修改", "撤销修改", "丢弃修改", "不要保存")


def _normalized_message(message: str) -> str:
    return re.sub(r"\s+", "", message.strip().lower())


def _is_start_intent(message: str) -> bool:
    normalized = _normalized_message(message)
    return any(pattern in normalized for pattern in START_COMMAND_PATTERNS)


def _is_config_change_intent(message: str) -> bool:
    normalized = _normalized_message(message)
    if any(pattern.lower().replace(" ", "") in normalized for pattern in CONFIG_CHANGE_EXPLICIT_HINTS):
        return True
    return any(verb in normalized for verb in CONFIG_CHANGE_VERBS) and any(
        target.lower().replace(" ", "") in normalized for target in CONFIG_CHANGE_TARGETS
    )


def _is_draft_confirm_intent(message: str) -> bool:
    normalized = _normalized_message(message)
    return any(pattern in normalized for pattern in DRAFT_CONFIRM_PATTERNS)


def _is_draft_discard_intent(message: str) -> bool:
    normalized = _normalized_message(message)
    return any(pattern in normalized for pattern in DRAFT_DISCARD_PATTERNS)


def _extract_config_filename(message: str) -> str | None:
    match = re.search(r"(?:命名为|保存为|文件名为|名称为)\s*[《\"']?([^，。；;\n\"'》]+)", message)
    if match:
        return match.group(1).strip()
    match = re.search(r"([\w\-.\u4e00-\u9fff]+\.docx)\b", message, re.I)
    if match:
        return match.group(1).strip()
    return None


def _validate_config_document(config_path: str) -> tuple[bool, str]:
    script = config.PROJECT_ROOT / ".agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py"
    proc = subprocess.run(
        ["python3", str(script), "validate", "--config", config_path],
        cwd=config.PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    return proc.returncode == 0, output or "validate 没有输出"


agent_lock = threading.Lock()
agent_bridge = CodexCliAgentBridge()


class WebConsoleHandler(SimpleHTTPRequestHandler):
    server_version = "Project1WebConsole/0.1"

    def log_message(self, format: str, *args) -> None:
        print(f"[web_console] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_static(config.FRONTEND_ROOT / "index.html")
        elif path in ("/app.js", "/styles.css"):
            self._send_static(config.FRONTEND_ROOT / path.lstrip("/"))
        elif path == "/api/messages":
            self._send_json({"active_conversation_id": get_active_conversation_id(), "messages": read_messages()})
        elif path == "/api/conversations":
            self._send_json({"active_conversation_id": get_active_conversation_id(), "conversations": list_conversations()})
        elif path.startswith("/api/conversations/") and path.endswith("/messages"):
            conversation_id = path.split("/")[3]
            self._send_json({"conversation_id": conversation_id, "messages": read_messages(conversation_id)})
        elif path == "/api/status":
            self._send_json(config_status())
        elif path == "/api/config-history":
            self._send_json({"configs": list_config_history()})
        elif path == "/api/config-draft":
            self._send_json({"draft": config_draft_status()})
        elif path == "/api/tdms":
            self._send_json({"files": list_tdms_files()})
        elif path.startswith("/api/tdms/"):
            name = unquote(path.removeprefix("/api/tdms/"))
            self._send_tdms(name)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            self._handle_chat()
        elif parsed.path == "/api/upload-config":
            self._handle_upload_config()
        elif parsed.path == "/api/config-draft/confirm":
            self._handle_confirm_config_draft()
        elif parsed.path == "/api/config-draft/discard":
            self._handle_discard_config_draft()
        elif parsed.path.startswith("/api/config-history/") and parsed.path.endswith("/select"):
            config_id = unquote(parsed.path.removeprefix("/api/config-history/").removesuffix("/select"))
            self._handle_select_config_history(config_id)
        elif parsed.path == "/api/conversations":
            self._handle_create_conversation()
        elif parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/select"):
            conversation_id = parsed.path.split("/")[3]
            self._handle_select_conversation(conversation_id)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")


    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/conversations/"):
            conversation_id = parsed.path.split("/")[3]
            self._handle_rename_conversation(conversation_id)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/conversations/"):
            conversation_id = parsed.path.split("/")[3]
            self._handle_delete_conversation(conversation_id)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _handle_create_conversation(self) -> None:
        title = None
        if int(self.headers.get("Content-Length", "0") or "0") > 0:
            try:
                payload = self._read_json_body()
                title = str(payload.get("title", "")).strip() or None
            except ValueError:
                title = None
        conversation = create_conversation(title=title)
        self._send_json({"conversation": conversation, "conversations": list_conversations()})

    def _handle_select_conversation(self, conversation_id: str) -> None:
        try:
            conversation = set_active_conversation(conversation_id)
            self._send_json({"conversation": conversation, "messages": read_messages(conversation_id), "conversations": list_conversations()})
        except FileNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, "对话不存在")
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _handle_rename_conversation(self, conversation_id: str) -> None:
        try:
            payload = self._read_json_body()
            title = str(payload.get("title", ""))
            conversation = rename_conversation(conversation_id, title)
            self._send_json({"conversation": conversation, "conversations": list_conversations()})
        except FileNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, "对话不存在")
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _handle_delete_conversation(self, conversation_id: str) -> None:
        try:
            result = delete_conversation(conversation_id)
            self._send_json({**result, "messages": read_messages(), "conversations": list_conversations()})
        except FileNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, "对话不存在")
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _send_chat_system_result(self, conversation_id: str, user_msg: dict, content: str) -> None:
        system_msg = append_message("system", content, conversation_id=conversation_id)
        self._send_json(
            {
                "active_conversation_id": conversation_id,
                "user_message": user_msg,
                "system_message": system_msg,
                "status": config_status(),
                "configs": list_config_history(),
                "conversations": list_conversations(),
            }
        )

    def _handle_chat(self) -> None:
        try:
            payload = self._read_json_body()
            message = str(payload.get("message", "")).strip()
            if not message:
                raise ValueError("消息不能为空")
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        if not agent_lock.acquire(blocking=False):
            self._send_error(HTTPStatus.CONFLICT, "agent 正在处理上一条消息，请稍后再试")
            return

        try:
            conversation_id = get_active_conversation_id()
            user_msg = append_message("user", message, conversation_id=conversation_id)
            draft = config_draft_status()

            if _is_draft_discard_intent(message):
                if not draft.get("active"):
                    self._send_chat_system_result(conversation_id, user_msg, "当前没有待放弃的配置草稿。")
                    return
                result = discard_config_draft()
                self._send_chat_system_result(
                    conversation_id,
                    user_msg,
                    f"已放弃本次配置修改，正式配置仍为：{result['draft'].get('base_display_filename') or 'sampling_config.docx'}。",
                )
                return

            if _is_draft_confirm_intent(message):
                if not draft.get("active"):
                    self._send_chat_system_result(conversation_id, user_msg, "当前没有待确认的配置草稿。")
                    return
                ok, validate_output = _validate_config_document(draft["draft_path"])
                if not ok:
                    self._send_chat_system_result(
                        conversation_id,
                        user_msg,
                        "配置草稿校验失败，未保存为正式配置。请继续修改草稿后再确认。\n\n" + validate_output,
                    )
                    return
                try:
                    result = confirm_config_draft(_extract_config_filename(message))
                except (FileNotFoundError, ValueError) as exc:
                    self._send_chat_system_result(conversation_id, user_msg, str(exc))
                    return
                self._send_chat_system_result(
                    conversation_id,
                    user_msg,
                    f"配置修改已确认并保存为：{result['filename']}。新的 Word 文件已成为当前配置，原配置已进入配置历史。",
                )
                return

            if draft.get("active") and _is_start_intent(message):
                self._send_chat_system_result(
                    conversation_id,
                    user_msg,
                    "当前有未确认的配置草稿。请先发送“确认修改”保存草稿，或发送“放弃修改”取消草稿；在此之前不能启动采样。",
                )
                return

            if _is_config_change_intent(message):
                try:
                    draft_result = ensure_config_draft(conversation_id=conversation_id, requested_by=message)
                except FileNotFoundError as exc:
                    self._send_chat_system_result(conversation_id, user_msg, str(exc))
                    return
                draft = draft_result["draft"]
                if draft_result.get("created"):
                    append_message(
                        "system",
                        f"已进入配置修改模式。正式配置保持不变，agent 将修改草稿：{draft['draft_path']}。确认前不能启动采样。",
                        conversation_id=conversation_id,
                    )

            transcript = read_messages(conversation_id)
            try:
                # Draft edits need the current process arguments. Start a fresh Codex
                # session here so older sandboxed sessions are not resumed.
                session_id = None if draft.get("active") else get_codex_session_id(conversation_id)
                bridge_reply = agent_bridge.chat(message, transcript, session_id=session_id, config_draft=draft)
                if bridge_reply.session_id and bridge_reply.session_id != session_id:
                    set_codex_session_id(conversation_id, bridge_reply.session_id)
                agent_msg = append_message("agent", bridge_reply.content, conversation_id=conversation_id)
                self._send_json({"active_conversation_id": conversation_id, "codex_session_id": bridge_reply.session_id, "user_message": user_msg, "agent_message": agent_msg, "status": config_status(), "configs": list_config_history(), "conversations": list_conversations()})
            except AgentBridgeError as exc:
                error_msg = append_message("system", f"agent 调用失败：{exc}", conversation_id=conversation_id)
                self._send_json(
                    {"user_message": user_msg, "error_message": error_msg},
                    status=HTTPStatus.BAD_GATEWAY,
                )
        finally:
            agent_lock.release()

    def _handle_confirm_config_draft(self) -> None:
        name = None
        if int(self.headers.get("Content-Length", "0") or "0") > 0:
            try:
                payload = self._read_json_body()
                name = str(payload.get("filename", "")).strip() or None
            except ValueError:
                name = None
        draft = config_draft_status()
        if not draft.get("active"):
            self._send_error(HTTPStatus.BAD_REQUEST, "当前没有待确认的配置草稿")
            return
        ok, validate_output = _validate_config_document(draft["draft_path"])
        if not ok:
            self._send_error(HTTPStatus.BAD_REQUEST, "配置草稿校验失败，未保存为正式配置。\n\n" + validate_output)
            return
        try:
            result = confirm_config_draft(name)
        except (FileNotFoundError, ValueError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        msg = append_message("system", f"配置修改已确认并保存为：{result['filename']}。新的 Word 文件已成为当前配置，原配置已进入配置历史。")
        self._send_json({"config": result, "status": config_status(), "configs": list_config_history(), "message": msg, "conversations": list_conversations()})

    def _handle_discard_config_draft(self) -> None:
        try:
            result = discard_config_draft()
        except FileNotFoundError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        msg = append_message("system", "已放弃本次配置修改，正式配置未改变。")
        self._send_json({"draft": result, "status": config_status(), "configs": list_config_history(), "message": msg, "conversations": list_conversations()})

    def _handle_select_config_history(self, config_id: str) -> None:
        if config_draft_status().get("active"):
            self._send_error(HTTPStatus.CONFLICT, "当前有未确认的配置草稿，请先确认或放弃修改")
            return
        try:
            result = select_config_history(config_id)
        except FileNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, "配置历史不存在")
            return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        msg = append_message(
            "system",
            f"已切换当前配置文件：{result['filename']} -> {result['active_config']}",
        )
        self._send_json({
            "config": result,
            "status": config_status(),
            "configs": list_config_history(),
            "message": msg,
            "conversations": list_conversations(),
        })

    def _handle_upload_config(self) -> None:
        if config_draft_status().get("active"):
            self._send_error(HTTPStatus.CONFLICT, "当前有未确认的配置草稿，请先确认或放弃修改")
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            self._send_error(HTTPStatus.BAD_REQUEST, "上传内容为空")
            return
        if content_length > config.MAX_UPLOAD_BYTES + 4096:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "上传文件过大")
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type or "boundary=" not in content_type:
            self._send_error(HTTPStatus.BAD_REQUEST, "需要 multipart/form-data 上传")
            return

        body = self.rfile.read(content_length)
        try:
            filename, data = self._extract_multipart_file(content_type, body)
            result = save_uploaded_config(filename, data)
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        msg = append_message(
            "system",
            f"已上传并替换配置文件：{result['filename']} -> {result['active_config']}",
        )
        self._send_json({"upload": result, "message": msg, "conversations": list_conversations()})

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("请求体为空")
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 格式无效") from exc

    def _extract_multipart_file(self, content_type: str, body: bytes) -> tuple[str, bytes]:
        boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
        marker = ("--" + boundary).encode()
        for part in body.split(marker):
            if b"Content-Disposition:" not in part:
                continue
            header_blob, _, data = part.partition(b"\r\n\r\n")
            headers = header_blob.decode("utf-8", errors="replace")
            disposition = next(
                (line for line in headers.splitlines() if line.lower().startswith("content-disposition:")),
                "",
            )
            field_match = re.search(r'(?:^|;\s*)name="([^"]*)"', disposition)
            if not field_match or field_match.group(1) != "config":
                continue
            filename = "sampling_config.docx"
            filename_match = re.search(r'(?:^|;\s*)filename="([^"]*)"', disposition)
            if filename_match:
                filename = filename_match.group(1) or filename
            if data.endswith(b"\r\n"):
                data = data[:-2]
            return filename, data
        raise ValueError("未找到名为 config 的上传文件")

    def _send_static(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != config.FRONTEND_ROOT.resolve() or not resolved.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Static file not found")
            return
        data = resolved.read_bytes()
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_tdms(self, name: str) -> None:
        try:
            path = resolve_tdms_download(name)
        except FileNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, "TDMS 文件不存在")
            return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)


def main() -> None:
    ensure_runtime_dirs()
    server = ThreadingHTTPServer((config.HOST, config.PORT), WebConsoleHandler)
    print(f"Project1 Web Console: http://{config.HOST}:{config.PORT}")
    print(f"Project root: {config.PROJECT_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web console...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
