from __future__ import annotations

from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import json
import mimetypes
import threading

from . import config
from .agent_bridge import AgentBridgeError, CodexCliAgentBridge
from .file_store import (
    append_message,
    config_status,
    ensure_runtime_dirs,
    list_tdms_files,
    read_log,
    read_messages,
    resolve_tdms_download,
    save_uploaded_config,
)


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
            self._send_json({"messages": read_messages()})
        elif path == "/api/status":
            self._send_json(config_status())
        elif path == "/api/log":
            params = parse_qs(parsed.query)
            max_bytes = int(params.get("max_bytes", ["200000"])[0])
            self._send_json({"log": read_log(max_bytes=max_bytes)})
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
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

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
            user_msg = append_message("user", message)
            transcript = read_messages()
            try:
                reply = agent_bridge.chat(message, transcript)
                agent_msg = append_message("agent", reply)
                self._send_json({"user_message": user_msg, "agent_message": agent_msg})
            except AgentBridgeError as exc:
                error_msg = append_message("system", f"agent 调用失败：{exc}")
                self._send_json(
                    {"user_message": user_msg, "error_message": error_msg},
                    status=HTTPStatus.BAD_GATEWAY,
                )
        finally:
            agent_lock.release()

    def _handle_upload_config(self) -> None:
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
        self._send_json({"upload": result, "message": msg})

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
            if 'name="config"' not in headers:
                continue
            filename = "sampling_config.docx"
            for segment in headers.split(";"):
                segment = segment.strip()
                if segment.startswith("filename="):
                    filename = segment.split("=", 1)[1].strip().strip('"') or filename
                    break
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
