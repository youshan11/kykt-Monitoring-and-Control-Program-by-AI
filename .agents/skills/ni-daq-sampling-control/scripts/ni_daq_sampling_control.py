#!/usr/bin/env python3
"""Project-local NI DAQ sampling control entry point."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import signal
import shlex
import subprocess
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    print("Python 3.11+ is required because this script uses tomllib.", file=sys.stderr)
    sys.exit(2)


SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
SKILL_SCRIPT_RELATIVE_DIR = Path(".agents/skills/ni-daq-sampling-control/scripts")

AI_VOLTAGE_RANGES = {(-1.25, 1.25), (-2.5, 2.5), (-5.0, 5.0), (-10.0, 10.0)}
TERMINAL_CONFIGS = {"default", "diff", "differential"}
TIMED_MODES = {"finite", "continuous"}
TIMING_MODES = {"static", "finite", "continuous", "hw_timed_single_point"}
TASK_TYPES = {"ai", "di", "do", "ao", "ci", "co"}
LINE_GROUPINGS = {"chan_for_all_lines", "chan_per_line"}
EDGES = {"rising", "falling"}
COUNT_DIRECTIONS = {"up", "down", "external"}
CO_MODES = {"pulse_frequency", "pulse_time", "pulse_ticks"}


def find_project_root() -> Path:
    for parent in SCRIPT_PATH.parents:
        if (parent / "sampling_config.docx").exists() or (parent / "sampling_config.md").exists():
            return parent
    raise RuntimeError("Could not find project root containing sampling_config.docx.")


PROJECT_ROOT = find_project_root()
DEFAULT_CONFIG = PROJECT_ROOT / "sampling_config.docx"
if not DEFAULT_CONFIG.exists():
    DEFAULT_CONFIG = PROJECT_ROOT / "sampling_config.md"


class ConfigError(ValueError):
    pass


def _project_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as docx_file:
            document_xml = docx_file.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ConfigError(f"Invalid DOCX config file: {path}") from exc
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = ET.fromstring(document_xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:body/w:p", namespace):
        parts: list[str] = []
        for node in paragraph.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "t" and node.text:
                parts.append(node.text)
            elif tag == "tab":
                parts.append("\t")
            elif tag == "br":
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _config_document_text(config_path: Path) -> str:
    if config_path.suffix.lower() == ".docx":
        return _docx_text(config_path)
    return config_path.read_text(encoding="utf-8")


def _extract_toml(document_text: str, source_name: str = "config document") -> str:
    tagged = re.findall(r"```toml\s+sampling-config\s*\n(.*?)\n```", document_text, re.S)
    if len(tagged) == 1:
        return tagged[0]
    if len(tagged) > 1:
        raise ConfigError("Multiple tagged sampling-config TOML blocks found; keep exactly one.")
    matches = re.findall(r"```toml\s*\n(.*?)\n```", document_text, re.S)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ConfigError(f"No fenced TOML sampling-config block found in {source_name}.")
    raise ConfigError("Multiple TOML blocks found; tag the active one as sampling-config.")


def load_config(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    toml_text = _extract_toml(_config_document_text(config_path), str(config_path))
    try:
        config = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    validate_config(config)
    return config


def _require_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    section = config.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"Missing required section [{name}].")
    return section


def _require_string(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label}.{key} must be a non-empty string.")
    return value


def _require_bool(mapping: dict[str, Any], key: str, label: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{label}.{key} must be true or false.")
    return value


def _require_number(mapping: dict[str, Any], key: str, label: str, *, positive: bool = False) -> float | int:
    value = mapping.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"{label}.{key} must be a number.")
    if positive and value <= 0:
        raise ConfigError(f"{label}.{key} must be positive.")
    return value


def _require_int(mapping: dict[str, Any], key: str, label: str, *, positive: bool = False) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{label}.{key} must be an integer.")
    if positive and value <= 0:
        raise ConfigError(f"{label}.{key} must be a positive integer.")
    return value


def _channels(task: dict[str, Any], label: str) -> list[str]:
    channels = task.get("channels")
    if not isinstance(channels, list) or not channels or not all(isinstance(ch, str) and ch for ch in channels):
        raise ConfigError(f"{label}.channels must be a non-empty list of strings.")
    return channels


def _slot(channel: str) -> str | None:
    match = re.match(r"^(PXI1Slot\d+)/", channel)
    return match.group(1) if match else None


def _is_ai_channel(channel: str) -> bool:
    return re.match(r"^PXI1Slot[56]/ai[0-7]$", channel) is not None


def _is_di_do_channel(channel: str) -> bool:
    return re.match(r"^PXI1Slot3/port0(?:/line(?:[0-9]|[12][0-9]|3[01]))?$", channel) is not None or re.match(
        r"^PXI1Slot3/port1(?:/line[0-7])?$", channel
    ) is not None or re.match(r"^PXI1Slot[568]/port0(?:/line[0-7])?$", channel) is not None


def _is_whole_port(channel: str) -> bool:
    return re.match(r"^PXI1Slot\d+/port\d+$", channel) is not None


def _is_ao_channel(channel: str) -> bool:
    match = re.match(r"^PXI1Slot8/ao(\d+)$", channel)
    return bool(match and 0 <= int(match.group(1)) <= 31)


def _ao_index(channel: str) -> int:
    match = re.match(r"^PXI1Slot8/ao(\d+)$", channel)
    if not match:
        raise ConfigError(f"Invalid AO channel: {channel}")
    return int(match.group(1))


def _is_ci_co_channel(channel: str) -> bool:
    match = re.match(r"^PXI1Slot(3|5|6)/ctr(\d+)$", channel)
    if not match:
        return False
    slot = match.group(1)
    index = int(match.group(2))
    return index <= 7 if slot == "3" else index <= 1


def _validate_timed_input(task: dict[str, Any], label: str) -> None:
    timing_mode = task.get("timing_mode")
    if timing_mode not in TIMED_MODES:
        raise ConfigError(f'{label}.timing_mode must be "finite" or "continuous".')
    _require_number(task, "sample_rate_hz", label, positive=True)
    _require_int(task, "samples_per_read", label, positive=True)
    if timing_mode == "finite":
        _require_number(task, "duration_seconds", label, positive=True)


def _validate_common_task(task: dict[str, Any], index: int) -> tuple[str, bool, str]:
    label = f"tasks[{index}]"
    name = _require_string(task, "name", label)
    enabled = _require_bool(task, "enabled", label)
    task_type = _require_string(task, "type", label)
    if task_type not in TASK_TYPES:
        raise ConfigError(f"{label}.type must be one of: " + ", ".join(sorted(TASK_TYPES)))
    _channels(task, label)
    timing_mode = task.get("timing_mode")
    if not isinstance(timing_mode, str) or timing_mode not in TIMING_MODES:
        raise ConfigError(f"{label}.timing_mode must be one of: " + ", ".join(sorted(TIMING_MODES)))
    triggers = task.get("triggers", [])
    if triggers:
        if not isinstance(triggers, list) or not all(isinstance(trigger, dict) for trigger in triggers):
            raise ConfigError(f"{label}.triggers must be a list of trigger tables.")
        for trigger_index, trigger in enumerate(triggers):
            trigger_label = f"{label}.triggers[{trigger_index}]"
            kind = _require_string(trigger, "kind", trigger_label)
            if kind not in {"start", "reference", "pause", "arm_start"}:
                raise ConfigError(f"{trigger_label}.kind is not supported: {kind}")
            _require_string(trigger, "source", trigger_label)
        if enabled:
            raise ConfigError(f"{label} uses triggers, but triggered tasks are not implemented yet.")
    return name, enabled, task_type


def _validate_ai_task(task: dict[str, Any], label: str, *, enabled: bool) -> None:
    channels = _channels(task, label)
    invalid = [channel for channel in channels if not _is_ai_channel(channel)]
    if invalid:
        raise ConfigError(f"{label}.channels contains unsupported AI channel(s): " + ", ".join(invalid))
    if task.get("measurement") != "voltage":
        raise ConfigError(f'{label}.measurement must be "voltage"; other AI measurements are not implemented yet.')
    terminal = str(task.get("terminal_config", "default")).lower()
    if terminal not in TERMINAL_CONFIGS:
        raise ConfigError(f'{label}.terminal_config must be "default" or "diff" for PXI-6133 AI.')
    voltage_min = float(_require_number(task, "voltage_min", label))
    voltage_max = float(_require_number(task, "voltage_max", label))
    if (voltage_min, voltage_max) not in AI_VOLTAGE_RANGES:
        raise ConfigError(f"{label} voltage range must be one of ±1.25 V, ±2.5 V, ±5 V, or ±10 V.")
    _validate_timed_input(task, label)


def _validate_di_task(task: dict[str, Any], label: str, *, enabled: bool) -> None:
    channels = _channels(task, label)
    invalid = [channel for channel in channels if not _is_di_do_channel(channel)]
    if invalid:
        raise ConfigError(f"{label}.channels contains unsupported DI channel(s): " + ", ".join(invalid))
    grouping = task.get("line_grouping", "chan_for_all_lines")
    if grouping not in LINE_GROUPINGS:
        raise ConfigError(f"{label}.line_grouping must be chan_for_all_lines or chan_per_line.")
    if any(_is_whole_port(channel) for channel in channels) and grouping != "chan_for_all_lines":
        raise ConfigError(f"{label} uses whole-port channels and must set line_grouping = \"chan_for_all_lines\".")
    timing_mode = task.get("timing_mode")
    if timing_mode == "static":
        return
    _validate_timed_input(task, label)
    slots = {_slot(channel) for channel in channels}
    if "PXI1Slot8" in slots:
        raise ConfigError(f"{label}: PXI1Slot8 DI supports static reads only.")
    if {"PXI1Slot5", "PXI1Slot6"} & slots and not task.get("sample_clock_source"):
        raise ConfigError(f"{label}: timed PXI1Slot5/PXI1Slot6 DI requires sample_clock_source.")


def _validate_do_task(task: dict[str, Any], label: str, *, enabled: bool) -> None:
    channels = _channels(task, label)
    invalid = [channel for channel in channels if not _is_di_do_channel(channel)]
    if invalid:
        raise ConfigError(f"{label}.channels contains unsupported DO channel(s): " + ", ".join(invalid))
    if task.get("timing_mode") != "static":
        raise ConfigError(f"{label}: runner currently supports static DO only.")
    grouping = task.get("line_grouping", "chan_for_all_lines")
    if grouping not in LINE_GROUPINGS:
        raise ConfigError(f"{label}.line_grouping must be chan_for_all_lines or chan_per_line.")
    if any(_is_whole_port(channel) for channel in channels) and grouping != "chan_for_all_lines":
        raise ConfigError(f"{label} uses whole-port channels and must set line_grouping = \"chan_for_all_lines\".")
    state = task.get("initial_state")
    if not isinstance(state, (bool, int, list)) or isinstance(state, str):
        raise ConfigError(f"{label}.initial_state must be a boolean, integer, or list.")


def _validate_ao_task(task: dict[str, Any], label: str, *, enabled: bool) -> None:
    channels = _channels(task, label)
    invalid = [channel for channel in channels if not _is_ao_channel(channel)]
    if invalid:
        raise ConfigError(f"{label}.channels contains unsupported AO channel(s): " + ", ".join(invalid))
    if task.get("timing_mode") != "static":
        raise ConfigError(f"{label}: PXI1Slot8 AO is supported as static output only.")
    output_type = task.get("output_type")
    value = float(_require_number(task, "value", label))
    if output_type == "voltage":
        if any(_ao_index(channel) > 15 for channel in channels):
            raise ConfigError(f"{label}: PXI1Slot8 voltage output uses ao0 through ao15.")
        voltage_min = float(_require_number(task, "voltage_min", label))
        voltage_max = float(_require_number(task, "voltage_max", label))
        if voltage_min < -10.24 or voltage_max > 10.24 or voltage_min >= voltage_max:
            raise ConfigError(f"{label}: voltage range must be within -10.24 V to 10.24 V.")
        if not voltage_min <= value <= voltage_max:
            raise ConfigError(f"{label}.value must be inside voltage_min/voltage_max.")
    elif output_type == "current":
        if any(_ao_index(channel) < 16 for channel in channels):
            raise ConfigError(f"{label}: PXI1Slot8 current output uses ao16 through ao31.")
        current_min = float(_require_number(task, "current_min", label))
        current_max = float(_require_number(task, "current_max", label))
        if current_min < 0.0 or current_max > 0.0204 or current_min >= current_max:
            raise ConfigError(f"{label}: current range must be within 0.0 A to 0.0204 A.")
        if not current_min <= value <= current_max:
            raise ConfigError(f"{label}.value must be inside current_min/current_max.")
    else:
        raise ConfigError(f'{label}.output_type must be "voltage" or "current".')


def _validate_ci_task(task: dict[str, Any], label: str, *, enabled: bool) -> None:
    channels = _channels(task, label)
    invalid = [channel for channel in channels if not _is_ci_co_channel(channel)]
    if invalid:
        raise ConfigError(f"{label}.channels contains unsupported CI channel(s): " + ", ".join(invalid))
    if task.get("counter_mode") != "count_edges":
        raise ConfigError(f'{label}.counter_mode must be "count_edges"; other CI modes are not implemented yet.')
    if task.get("edge", "rising") not in EDGES:
        raise ConfigError(f"{label}.edge must be rising or falling.")
    if task.get("count_direction", "up") not in COUNT_DIRECTIONS:
        raise ConfigError(f"{label}.count_direction must be up, down, or external.")
    _require_int(task, "initial_count", label)
    timing_mode = task.get("timing_mode")
    if timing_mode == "static":
        return
    _validate_timed_input(task, label)
    if not task.get("sample_clock_source"):
        raise ConfigError(f"{label}: sampled CI requires sample_clock_source.")


def _validate_co_task(task: dict[str, Any], label: str, *, enabled: bool) -> None:
    channels = _channels(task, label)
    invalid = [channel for channel in channels if not _is_ci_co_channel(channel)]
    if invalid:
        raise ConfigError(f"{label}.channels contains unsupported CO channel(s): " + ", ".join(invalid))
    if task.get("timing_mode") != "continuous":
        raise ConfigError(f"{label}: runner currently supports continuous CO pulse output only.")
    counter_mode = task.get("counter_mode")
    if counter_mode not in CO_MODES:
        raise ConfigError(f"{label}.counter_mode must be one of: " + ", ".join(sorted(CO_MODES)))
    if counter_mode == "pulse_frequency":
        _require_number(task, "frequency", label, positive=True)
        duty_cycle = float(_require_number(task, "duty_cycle", label, positive=True))
        if duty_cycle > 1.0:
            raise ConfigError(f"{label}.duty_cycle must be <= 1.0.")
    elif counter_mode == "pulse_time":
        _require_number(task, "high_time", label, positive=True)
        _require_number(task, "low_time", label, positive=True)
    elif counter_mode == "pulse_ticks":
        _require_int(task, "high_ticks", label, positive=True)
        _require_int(task, "low_ticks", label, positive=True)


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 2:
        raise ConfigError("schema_version must be 2. Legacy [acquisition]/[channels] config is no longer supported.")

    host = _require_section(config, "host")
    if host.get("mode") not in {"local", "ssh"}:
        raise ConfigError('host.mode must be "local" or "ssh".')
    if host.get("mode") == "ssh":
        missing = [name for name in ("ssh_host", "ssh_user", "remote_project_dir") if not host.get(name)]
        if missing:
            raise ConfigError("SSH mode is selected but these fields are missing: " + ", ".join(missing))
        ssh_port = host.get("ssh_port", 22)
        if not isinstance(ssh_port, int) or ssh_port <= 0:
            raise ConfigError("host.ssh_port must be a positive integer.")
        if not host.get("python"):
            raise ConfigError("host.python is required for SSH mode.")

    tdms = _require_section(config, "tdms")
    if not tdms.get("output_dir"):
        raise ConfigError("tdms.output_dir is required.")
    if not tdms.get("filename", "").endswith(".tdms"):
        raise ConfigError("tdms.filename must end with .tdms.")
    if not isinstance(tdms.get("allow_overwrite", False), bool):
        raise ConfigError("tdms.allow_overwrite must be true or false.")

    control = _require_section(config, "control")
    for key in ("state_file", "stop_file", "log_file"):
        if not control.get(key):
            raise ConfigError(f"control.{key} is required.")
    _require_number(control, "startup_check_seconds", "control")
    _require_number(control, "stop_timeout_seconds", "control", positive=True)

    tasks = config.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ConfigError("At least one [[tasks]] entry is required.")

    seen_names: set[str] = set()
    enabled_count = 0
    validators = {
        "ai": _validate_ai_task,
        "di": _validate_di_task,
        "do": _validate_do_task,
        "ao": _validate_ao_task,
        "ci": _validate_ci_task,
        "co": _validate_co_task,
    }
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ConfigError(f"tasks[{index}] must be a table.")
        name, enabled, task_type = _validate_common_task(task, index)
        label = f"tasks[{index}] ({name})"
        if name in seen_names:
            raise ConfigError(f"Duplicate task name: {name}")
        seen_names.add(name)
        validators[task_type](task, label, enabled=enabled)
        if enabled:
            enabled_count += 1
    if enabled_count == 0:
        raise ConfigError("At least one task must have enabled = true.")


def enabled_tasks(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [task for task in config.get("tasks", []) if task.get("enabled") is True]


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def paths_from_config(config: dict[str, Any]) -> dict[str, Path]:
    tdms = config["tdms"]
    control = config["control"]
    filename = tdms["filename"].format(timestamp=timestamp())
    return {
        "output": _project_path(tdms["output_dir"]) / filename,
        "state": _project_path(control["state_file"]),
        "stop": _project_path(control["stop_file"]),
        "log": _project_path(control["log_file"]),
    }


def pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "invalid", "state_file": str(state_path)}


def write_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def active_state(state_path: Path) -> dict[str, Any] | None:
    state = read_state(state_path)
    if not state or state.get("status") != "running":
        return None
    pid = state.get("pid")
    if isinstance(pid, int) and pid_running(pid):
        return state
    state["status"] = "stale"
    state["stopped_at"] = datetime.now().isoformat(timespec="seconds")
    write_state(state_path, state)
    return None


def tail_text(path: Path, limit: int = 3000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()[-limit:]
    return data.decode("utf-8", errors="replace").strip()


def ssh_target(host: dict[str, Any]) -> str:
    return f"{host['ssh_user']}@{host['ssh_host']}"


def ssh_args(host: dict[str, Any]) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-p",
        str(host.get("ssh_port", 22)),
        ssh_target(host),
    ]


def scp_args(host: dict[str, Any]) -> list[str]:
    return [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-P",
        str(host.get("ssh_port", 22)),
    ]


def run_checked(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def print_completed(process: subprocess.CompletedProcess[str]) -> None:
    if process.stdout:
        print(process.stdout, end="")
    if process.stderr:
        print(process.stderr, end="", file=sys.stderr)


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _docx_document_xml(text: str) -> str:
    paragraphs: list[str] = []
    for line in text.splitlines():
        escaped = _xml_escape(line)
        paragraphs.append(
            '<w:p><w:r><w:t xml:space="preserve">'
            + escaped
            + '</w:t></w:r></w:p>'
        )
    body = "".join(paragraphs) + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>'


def _docx_bytes_from_text(text: str) -> bytes:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as docx_file:
        docx_file.writestr("[Content_Types].xml", content_types)
        docx_file.writestr("_rels/.rels", rels)
        docx_file.writestr("word/document.xml", _docx_document_xml(text))
    return data.getvalue()


def remote_worker_config_text(config_path: Path) -> str:
    text = _config_document_text(config_path)
    match = re.search(r"```toml\s+sampling-config\s*\n(.*?)\n```", text, re.S)
    if not match:
        raise ConfigError(f"No tagged sampling-config TOML block found in {config_path}.")
    toml_text = match.group(1)
    host_block = (
        '[host]\n'
        'mode = "local"\n'
        'ssh_host = ""\n'
        'ssh_port = 22\n'
        'ssh_user = ""\n'
        'remote_project_dir = ""\n'
        'python = "python3"\n'
    )
    updated_toml, count = re.subn(r"(?ms)^\[host\]\n.*?(?=^\[|^\[\[|\Z)", host_block + "\n", toml_text, count=1)
    if count != 1:
        raise ConfigError("Could not rewrite [host] block for remote worker config.")
    return text[: match.start()] + "```toml sampling-config\n" + updated_toml.rstrip() + "\n```" + text[match.end() :]


def remote_script_dir(host: dict[str, Any]) -> str:
    remote_dir = host["remote_project_dir"].rstrip("/")
    return f"{remote_dir}/{SKILL_SCRIPT_RELATIVE_DIR.as_posix()}"


def sync_remote_scripts(config: dict[str, Any]) -> None:
    host = config["host"]
    remote_dir = host["remote_project_dir"].rstrip("/")
    script_dir = remote_script_dir(host)
    mkdir_cmd = f"mkdir -p {shlex.quote(script_dir)} {shlex.quote(remote_dir + '/data')}"
    mkdir_result = run_checked([*ssh_args(host), mkdir_cmd])
    if mkdir_result.returncode != 0:
        print_completed(mkdir_result)
        raise ConfigError("Failed to create remote project directories.")

    files = [
        str(SCRIPT_DIR / "ni_daq_sampling_control.py"),
        str(SCRIPT_DIR / "ni_daq_sample_runner.py"),
    ]
    scripts_result = run_checked([*scp_args(host), *files, f"{ssh_target(host)}:{script_dir}/"])
    if scripts_result.returncode != 0:
        print_completed(scripts_result)
        raise ConfigError("Failed to copy scripts to the measurement host.")


def sync_remote_project(config_path: Path, config: dict[str, Any]) -> None:
    sync_remote_scripts(config)
    host = config["host"]
    remote_dir = host["remote_project_dir"].rstrip("/")

    remote_config_name = "sampling_config.docx" if config_path.suffix.lower() == ".docx" else "sampling_config.md"
    worker_text = remote_worker_config_text(config_path)
    if config_path.suffix.lower() == ".docx":
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as temp_config:
            temp_config.write(_docx_bytes_from_text(worker_text))
            temp_config_path = Path(temp_config.name)
    else:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as temp_config:
            temp_config.write(worker_text)
            temp_config_path = Path(temp_config.name)
    try:
        config_result = run_checked([*scp_args(host), str(temp_config_path), f"{ssh_target(host)}:{remote_dir}/{remote_config_name}"])
        if config_result.returncode != 0:
            print_completed(config_result)
            raise ConfigError(f"Failed to copy {remote_config_name} to the measurement host.")
    finally:
        temp_config_path.unlink(missing_ok=True)


def run_remote_control(
    config_path: Path,
    config: dict[str, Any],
    command: str,
    extra_args: list[str] | None = None,
    *,
    sync_first: bool = True,
) -> subprocess.CompletedProcess[str]:
    if sync_first:
        sync_remote_project(config_path, config)
    else:
        sync_remote_scripts(config)
    host = config["host"]
    remote_dir = host["remote_project_dir"].rstrip("/")
    python = shlex.quote(host.get("python", "python3"))
    remote_control_script = f"{remote_script_dir(host)}/ni_daq_sampling_control.py"
    remote_command = (
        f"cd {shlex.quote(remote_dir)} && "
        f"{python} {shlex.quote(remote_control_script)} {shlex.quote(command)}"
    )
    if extra_args:
        remote_command += " " + " ".join(shlex.quote(arg) for arg in extra_args)
    return run_checked([*ssh_args(host), remote_command])


def parse_output_path(text: str) -> Path | None:
    for line in text.splitlines():
        match = re.match(r"output:\s*(.+\.tdms)\s*$", line)
        if match:
            return Path(match.group(1))
    return None


def pull_remote_output(config: dict[str, Any], remote_output: Path) -> Path:
    host = config["host"]
    local_dir = _project_path(config["tdms"]["output_dir"])
    local_dir.mkdir(parents=True, exist_ok=True)
    local_output = local_dir / remote_output.name
    source = f"{ssh_target(host)}:{remote_output}"
    result = run_checked([*scp_args(host), source, str(local_output)])
    if result.returncode != 0:
        print_completed(result)
        raise ConfigError(f"Failed to copy TDMS output back from measurement host: {remote_output}")
    return local_output


def task_line(task: dict[str, Any]) -> str:
    channels = ", ".join(task.get("channels", []))
    timing = task.get("timing_mode")
    bits = [f"{task.get('name')} ({task.get('type')})", channels, f"timing={timing}"]
    if task.get("sample_rate_hz"):
        bits.append(f"sample_rate_hz={task.get('sample_rate_hz')}")
    if task.get("tdms_group"):
        bits.append(f"tdms_group={task.get('tdms_group')}")
    return "; ".join(bits)


def prompt_confirm_start(config: dict[str, Any], paths: dict[str, Path]) -> bool:
    print("About to start NI DAQ tasks:")
    for task in enabled_tasks(config):
        print(f"  - {task_line(task)}")
    print(f"  output: {paths['output']}")
    print(f"  log: {paths['log']}")
    answer = input("Press Enter to confirm start, or type n to cancel: ").strip().lower()
    return answer in {"", "y", "yes", "确认", "确认启动", "确认开始"}


def prompt_confirm_stop(state: dict[str, Any] | None) -> bool:
    print("About to stop NI DAQ sampling:")
    if state:
        print(f"  pid: {state.get('pid')}")
        print(f"  output: {state.get('output_path')}")
        print(f"  started_at: {state.get('started_at')}")
        task_names = state.get("tasks")
        if task_names:
            print(f"  tasks: {', '.join(task_names)}")
    else:
        print("  no running state is currently recorded")
    answer = input("Press Enter to confirm stop, or type n to cancel: ").strip().lower()
    return answer in {"", "y", "yes", "确认", "确认停止", "确认终止"}


def command_validate(args: argparse.Namespace) -> int:
    config_path = _project_path(args.config)
    config = load_config(config_path)
    paths = paths_from_config(config)
    print("Config OK")
    print(f"config: {config_path}")
    print(f"schema_version: {config.get('schema_version')}")
    print("enabled_tasks:")
    for task in enabled_tasks(config):
        print(f"  - {task_line(task)}")
    print(f"output example: {paths['output']}")
    print(f"state: {paths['state']}")
    print(f"log: {paths['log']}")
    if config["host"].get("mode") == "ssh":
        print("Remote validation:")
        result = run_remote_control(config_path, config, "validate")
        print_completed(result)
        return result.returncode
    return 0


def command_start(args: argparse.Namespace) -> int:
    config_path = _project_path(args.config)
    config = load_config(config_path)
    paths = paths_from_config(config)
    if not args.confirm_start and not args.prompt_confirm_start:
        raise ConfigError("Starting sampling requires --confirm-start after explicit user confirmation.")
    if args.prompt_confirm_start and not prompt_confirm_start(config, paths):
        print("Start cancelled.")
        return 1
    if config["host"].get("mode") == "ssh":
        result = run_remote_control(config_path, config, "start", ["--confirm-start"])
        print_completed(result)
        return result.returncode
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["log"].parent.mkdir(parents=True, exist_ok=True)

    if active_state(paths["state"]):
        raise ConfigError(f"Sampling is already running according to {paths['state']}.")
    if paths["output"].exists() and not config["tdms"].get("allow_overwrite", False):
        raise ConfigError(f"Output file already exists and overwrite is disabled: {paths['output']}")
    if paths["stop"].exists():
        paths["stop"].unlink()

    runner = SCRIPT_DIR / "ni_daq_sample_runner.py"
    cmd = [
        sys.executable,
        str(runner),
        "--config",
        str(config_path),
        "--output",
        str(paths["output"]),
        "--stop-file",
        str(paths["stop"]),
    ]
    log_handle = paths["log"].open("ab")
    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    startup_check = float(config["control"].get("startup_check_seconds", 1.0))
    time.sleep(max(0.0, startup_check))
    if process.poll() is not None:
        log_handle.close()
        print(tail_text(paths["log"]), file=sys.stderr)
        raise ConfigError(f"Sampling runner exited during startup with code {process.returncode}.")

    state = {
        "status": "running",
        "pid": process.pid,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(config_path),
        "output_path": str(paths["output"]),
        "stop_file": str(paths["stop"]),
        "log_file": str(paths["log"]),
        "tasks": [task["name"] for task in enabled_tasks(config)],
        "command": cmd,
    }
    write_state(paths["state"], state)
    log_handle.close()
    print("Sampling started")
    print(f"pid: {process.pid}")
    print(f"tasks: {', '.join(state['tasks'])}")
    print(f"output: {paths['output']}")
    print(f"log: {paths['log']}")
    print(f"state: {paths['state']}")
    return 0


def command_stop(args: argparse.Namespace) -> int:
    config_path = _project_path(args.config)
    config = load_config(config_path)
    paths = paths_from_config(config)
    state_path = paths["state"]
    state = read_state(state_path)
    if not args.confirm_stop and not args.prompt_confirm_stop:
        raise ConfigError("Stopping sampling requires --confirm-stop after explicit user confirmation.")
    if args.prompt_confirm_stop and not prompt_confirm_stop(state):
        print("Stop cancelled.")
        return 1
    if config["host"].get("mode") == "ssh":
        result = run_remote_control(config_path, config, "stop", ["--confirm-stop"], sync_first=False)
        print_completed(result)
        if result.returncode != 0:
            return result.returncode
        remote_output = parse_output_path(result.stdout or "")
        if not remote_output:
            print("warning: remote stop output did not include a TDMS output path")
            return 0
        local_output = pull_remote_output(config, remote_output)
        print(f"local_copy: {local_output}")
        print(f"local_copy_size_bytes: {local_output.stat().st_size}")
        return 0
    if not state or state.get("status") != "running":
        print("No running sampling process was found.")
        return 0
    pid = state.get("pid")
    output_path = Path(state.get("output_path", paths["output"]))
    stop_path = Path(state.get("stop_file", paths["stop"]))
    timeout = float(config["control"].get("stop_timeout_seconds", 15.0))

    if not isinstance(pid, int) or not pid_running(pid):
        state["status"] = "stale"
        state["stopped_at"] = datetime.now().isoformat(timespec="seconds")
        write_state(state_path, state)
        print("Recorded sampling process is not running.")
        return 0

    stop_path.write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_running(pid):
            break
        time.sleep(0.25)

    if pid_running(pid):
        os.kill(pid, signal.SIGTERM)
        state["status"] = "terminated"
        warning = f"Process {pid} did not stop within {timeout} seconds and was terminated."
    else:
        state["status"] = "stopped"
        warning = ""

    state["stopped_at"] = datetime.now().isoformat(timespec="seconds")
    write_state(state_path, state)
    print("Sampling stopped")
    print(f"output: {output_path}")
    if output_path.exists():
        print(f"output_size_bytes: {output_path.stat().st_size}")
    else:
        print("warning: expected TDMS output file was not found")
    if warning:
        print(f"warning: {warning}")
    print(f"log: {state.get('log_file', paths['log'])}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    config_path = _project_path(args.config)
    config = load_config(config_path)
    if config["host"].get("mode") == "ssh":
        result = run_remote_control(config_path, config, "status", sync_first=False)
        print_completed(result)
        return result.returncode
    state_path = paths_from_config(config)["state"]
    state = read_state(state_path)
    if not state:
        print("No sampling state file found.")
        return 0
    pid = state.get("pid")
    running = isinstance(pid, int) and pid_running(pid)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"process_running: {running}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control NI DAQ sampling for this project.")
    parser.add_argument("command", choices=("start", "stop", "status", "validate"))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to sampling_config.docx")
    parser.add_argument("--confirm-start", action="store_true", help="Required for start after user confirmation")
    parser.add_argument("--confirm-stop", action="store_true", help="Required for stop after user confirmation")
    parser.add_argument("--prompt-confirm-start", action="store_true", help="Prompt in terminal; Enter confirms start")
    parser.add_argument("--prompt-confirm-stop", action="store_true", help="Prompt in terminal; Enter confirms stop")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            return command_start(args)
        if args.command == "stop":
            return command_stop(args)
        if args.command == "status":
            return command_status(args)
        if args.command == "validate":
            return command_validate(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
