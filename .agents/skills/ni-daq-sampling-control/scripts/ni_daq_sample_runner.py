#!/usr/bin/env python3
"""NI DAQ schema-v2 task runner that writes TDMS input segments."""

from __future__ import annotations

import argparse
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    print("Python 3.11+ is required because this script uses tomllib.", file=sys.stderr)
    sys.exit(2)


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as docx_file:
        document_xml = docx_file.read("word/document.xml")
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


def config_document_text(config_path: Path) -> str:
    if config_path.suffix.lower() == ".docx":
        return docx_text(config_path)
    return config_path.read_text(encoding="utf-8")


def extract_config(config_path: Path) -> dict[str, Any]:
    text = config_document_text(config_path)
    match = re.search(r"```toml\s+sampling-config\s*\n(.*?)\n```", text, re.S)
    if not match:
        raise ValueError(f"No tagged TOML sampling-config block found in {config_path}")
    config = tomllib.loads(match.group(1))
    if config.get("schema_version") != 2:
        raise ValueError("schema_version must be 2")
    return config


def enabled_tasks(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [task for task in config.get("tasks", []) if task.get("enabled") is True]


def terminal_config_constant(constants: Any, name: str) -> Any | None:
    normalized = (name or "default").lower()
    mapping = {
        "default": None,
        "diff": constants.TerminalConfiguration.DIFF,
        "differential": constants.TerminalConfiguration.DIFF,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported terminal_config: {name}")
    return mapping[normalized]


def line_grouping_constant(constants: Any, name: str) -> Any:
    normalized = (name or "chan_for_all_lines").lower()
    mapping = {
        "chan_for_all_lines": constants.LineGrouping.CHAN_FOR_ALL_LINES,
        "chan_per_line": constants.LineGrouping.CHAN_PER_LINE,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported line_grouping: {name}")
    return mapping[normalized]


def edge_constant(constants: Any, name: str) -> Any:
    normalized = (name or "rising").lower()
    mapping = {
        "rising": constants.Edge.RISING,
        "falling": constants.Edge.FALLING,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported edge: {name}")
    return mapping[normalized]


def count_direction_constant(constants: Any, name: str) -> Any:
    normalized = (name or "up").lower()
    if normalized == "up":
        return constants.CountDirection.COUNT_UP
    if normalized == "down":
        return constants.CountDirection.COUNT_DOWN
    if normalized == "external":
        for attr in ("EXTERNALLY_CONTROLLED", "EXTERNAL_SOURCE"):
            if hasattr(constants.CountDirection, attr):
                return getattr(constants.CountDirection, attr)
    raise ValueError(f"Unsupported count_direction: {name}")


def idle_state_constant(constants: Any, name: str | None) -> Any:
    normalized = (name or "low").lower()
    mapping = {
        "low": constants.Level.LOW,
        "high": constants.Level.HIGH,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported idle_state: {name}")
    return mapping[normalized]


def channel_tdms_name(channel_name: str) -> str:
    return channel_name.replace("/", "_")


def task_group_name(task: dict[str, Any]) -> str:
    return str(task.get("tdms_group") or task["name"])


def physical_channels(task: dict[str, Any]) -> str:
    return ",".join(task["channels"])


def is_timed(task: dict[str, Any]) -> bool:
    return task.get("timing_mode") in {"finite", "continuous"}


def is_finite(task: dict[str, Any]) -> bool:
    return task.get("timing_mode") == "finite"


def normalize_channel_data(data: Any, channel_count: int, read_count: int) -> list[list[Any]]:
    if channel_count == 1:
        if isinstance(data, list):
            return [data]
        return [[data]]
    if isinstance(data, list) and data and all(isinstance(channel_data, list) for channel_data in data):
        return [list(channel_data) for channel_data in data]
    if isinstance(data, list) and len(data) == channel_count and read_count == 1:
        return [[value] for value in data]
    return [list(channel_data) for channel_data in data]


def tdms_array(np: Any, values: list[Any]) -> Any:
    if values and all(isinstance(value, bool) for value in values):
        return np.asarray(values, dtype=np.bool_)
    if values and all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return np.asarray(values, dtype=np.uint64)
    return np.asarray(values, dtype=np.float64)


def finite_total_samples(task_cfg: dict[str, Any]) -> int:
    return max(1, int(float(task_cfg["sample_rate_hz"]) * float(task_cfg["duration_seconds"])))


def configure_timing(task_obj: Any, task_cfg: dict[str, Any], constants: Any) -> None:
    if not is_timed(task_cfg):
        return
    sample_mode = constants.AcquisitionType.FINITE if is_finite(task_cfg) else constants.AcquisitionType.CONTINUOUS
    samps_per_chan = finite_total_samples(task_cfg) if is_finite(task_cfg) else int(task_cfg["samples_per_read"])
    kwargs = {
        "rate": float(task_cfg["sample_rate_hz"]),
        "sample_mode": sample_mode,
        "samps_per_chan": samps_per_chan,
    }
    if task_cfg.get("sample_clock_source"):
        kwargs["source"] = str(task_cfg["sample_clock_source"])
    task_obj.timing.cfg_samp_clk_timing(**kwargs)


def configure_ai(task_obj: Any, task_cfg: dict[str, Any], constants: Any) -> None:
    terminal_config = terminal_config_constant(constants, task_cfg.get("terminal_config", "default"))
    for channel in task_cfg["channels"]:
        kwargs = {
            "physical_channel": channel,
            "min_val": float(task_cfg["voltage_min"]),
            "max_val": float(task_cfg["voltage_max"]),
        }
        if terminal_config is not None:
            kwargs["terminal_config"] = terminal_config
        task_obj.ai_channels.add_ai_voltage_chan(**kwargs)
    configure_timing(task_obj, task_cfg, constants)


def configure_di(task_obj: Any, task_cfg: dict[str, Any], constants: Any) -> None:
    task_obj.di_channels.add_di_chan(
        physical_channels(task_cfg),
        line_grouping=line_grouping_constant(constants, task_cfg.get("line_grouping", "chan_for_all_lines")),
    )
    configure_timing(task_obj, task_cfg, constants)


def configure_do(task_obj: Any, task_cfg: dict[str, Any], constants: Any) -> None:
    task_obj.do_channels.add_do_chan(
        physical_channels(task_cfg),
        line_grouping=line_grouping_constant(constants, task_cfg.get("line_grouping", "chan_for_all_lines")),
    )
    task_obj.write(task_cfg.get("initial_state", False), auto_start=True)


def configure_ao(task_obj: Any, task_cfg: dict[str, Any]) -> None:
    output_type = task_cfg["output_type"]
    for channel in task_cfg["channels"]:
        if output_type == "voltage":
            task_obj.ao_channels.add_ao_voltage_chan(
                channel,
                min_val=float(task_cfg["voltage_min"]),
                max_val=float(task_cfg["voltage_max"]),
            )
        elif output_type == "current":
            task_obj.ao_channels.add_ao_current_chan(
                channel,
                min_val=float(task_cfg["current_min"]),
                max_val=float(task_cfg["current_max"]),
            )
        else:
            raise ValueError(f"Unsupported AO output_type: {output_type}")
    values = [float(task_cfg["value"])] * len(task_cfg["channels"])
    task_obj.write(values[0] if len(values) == 1 else values, auto_start=True)


def configure_ci(task_obj: Any, task_cfg: dict[str, Any], constants: Any) -> None:
    if task_cfg.get("counter_mode") != "count_edges":
        raise ValueError(f"Unsupported CI counter_mode: {task_cfg.get('counter_mode')}")
    for channel in task_cfg["channels"]:
        task_obj.ci_channels.add_ci_count_edges_chan(
            channel,
            edge=edge_constant(constants, task_cfg.get("edge", "rising")),
            initial_count=int(task_cfg.get("initial_count", 0)),
            count_direction=count_direction_constant(constants, task_cfg.get("count_direction", "up")),
        )
    configure_timing(task_obj, task_cfg, constants)


def configure_co(task_obj: Any, task_cfg: dict[str, Any], constants: Any) -> None:
    counter_mode = task_cfg["counter_mode"]
    for channel in task_cfg["channels"]:
        if counter_mode == "pulse_frequency":
            task_obj.co_channels.add_co_pulse_chan_freq(
                channel,
                idle_state=idle_state_constant(constants, task_cfg.get("idle_state")),
                initial_delay=float(task_cfg.get("initial_delay", 0.0)),
                freq=float(task_cfg["frequency"]),
                duty_cycle=float(task_cfg["duty_cycle"]),
            )
        elif counter_mode == "pulse_time":
            task_obj.co_channels.add_co_pulse_chan_time(
                channel,
                idle_state=idle_state_constant(constants, task_cfg.get("idle_state")),
                initial_delay=float(task_cfg.get("initial_delay", 0.0)),
                low_time=float(task_cfg["low_time"]),
                high_time=float(task_cfg["high_time"]),
            )
        elif counter_mode == "pulse_ticks":
            task_obj.co_channels.add_co_pulse_chan_ticks(
                channel,
                source_terminal=str(task_cfg.get("source_terminal", "")),
                idle_state=idle_state_constant(constants, task_cfg.get("idle_state")),
                initial_delay=int(task_cfg.get("initial_delay_ticks", 0)),
                low_ticks=int(task_cfg["low_ticks"]),
                high_ticks=int(task_cfg["high_ticks"]),
            )
        else:
            raise ValueError(f"Unsupported CO counter_mode: {counter_mode}")
    task_obj.timing.cfg_implicit_timing(sample_mode=constants.AcquisitionType.CONTINUOUS)


def task_is_input(task_cfg: dict[str, Any]) -> bool:
    return task_cfg["type"] in {"ai", "di", "ci"}


def task_records_timed_data(task_cfg: dict[str, Any]) -> bool:
    return task_is_input(task_cfg) and is_timed(task_cfg)


def static_input_value(task_obj: Any, task_cfg: dict[str, Any]) -> Any:
    return task_obj.read()


def run(config_path: Path, output_path: Path, stop_file: Path) -> int:
    try:
        import nidaqmx
        from nidaqmx import constants
        import numpy as np
        from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter
    except ModuleNotFoundError as exc:
        print(
            "Missing runtime dependency. Install NI-DAQmx Python support, numpy, and nptdms "
            f"on the measurement host. Missing module: {exc.name}",
            file=sys.stderr,
        )
        return 2

    config = extract_config(config_path)
    tasks = enabled_tasks(config)
    if not tasks:
        raise ValueError("No enabled tasks found in sampling_config.docx")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    input_states: list[dict[str, Any]] = []
    keepalive = False

    root = RootObject(
        properties={
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "config_path": str(config_path),
            "schema_version": config.get("schema_version"),
            "task_count": len(tasks),
        }
    )

    with ExitStack() as stack, TdmsWriter(str(output_path)) as writer:
        task_objects: list[Any] = []
        start_tasks: list[Any] = []
        tdms_groups: list[Any] = [root]

        for task_cfg in tasks:
            task_obj = stack.enter_context(nidaqmx.Task(new_task_name=task_cfg["name"]))
            task_objects.append(task_obj)
            task_type = task_cfg["type"]
            if task_type == "ai":
                configure_ai(task_obj, task_cfg, constants)
                start_tasks.append(task_obj)
            elif task_type == "di":
                configure_di(task_obj, task_cfg, constants)
                if is_timed(task_cfg):
                    start_tasks.append(task_obj)
            elif task_type == "do":
                configure_do(task_obj, task_cfg, constants)
                keepalive = True
            elif task_type == "ao":
                configure_ao(task_obj, task_cfg)
                keepalive = True
            elif task_type == "ci":
                configure_ci(task_obj, task_cfg, constants)
                if is_timed(task_cfg):
                    start_tasks.append(task_obj)
            elif task_type == "co":
                configure_co(task_obj, task_cfg, constants)
                start_tasks.append(task_obj)
                keepalive = True
            else:
                raise ValueError(f"Unsupported task type: {task_type}")

            if task_is_input(task_cfg):
                group_name = task_group_name(task_cfg)
                tdms_groups.append(
                    GroupObject(
                        group_name,
                        properties={
                            "task_name": task_cfg["name"],
                            "task_type": task_type,
                            "channel_count": len(task_cfg["channels"]),
                            "timing_mode": task_cfg["timing_mode"],
                        },
                    )
                )
                input_states.append(
                    {
                        "task": task_obj,
                        "config": task_cfg,
                        "samples_written": 0,
                        "total_samples": finite_total_samples(task_cfg) if is_finite(task_cfg) else None,
                        "done": False,
                    }
                )

        writer.write_segment(tdms_groups)

        for state in input_states:
            task_cfg = state["config"]
            if task_cfg.get("timing_mode") == "static":
                value = static_input_value(state["task"], task_cfg)
                objects = []
                channels = task_cfg["channels"]
                values_by_channel = normalize_channel_data(value, len(channels), 1)
                for channel_name, values in zip(channels, values_by_channel):
                    objects.append(
                        ChannelObject(
                            task_group_name(task_cfg),
                            channel_tdms_name(channel_name),
                            tdms_array(np, values),
                            properties={"physical_channel": channel_name},
                        )
                    )
                writer.write_segment(objects)
                state["samples_written"] = 1
                state["done"] = True

        for task_obj in start_tasks:
            task_obj.start()

        while True:
            if stop_file.exists():
                break

            active_timed_inputs = [state for state in input_states if task_records_timed_data(state["config"]) and not state["done"]]
            if not active_timed_inputs:
                if keepalive:
                    time.sleep(0.1)
                    continue
                break

            for state in active_timed_inputs:
                task_cfg = state["config"]
                samples_per_read = int(task_cfg["samples_per_read"])
                if is_finite(task_cfg):
                    remaining = int(state["total_samples"] or 0) - int(state["samples_written"])
                    if remaining <= 0:
                        state["done"] = True
                        continue
                    read_count = min(samples_per_read, remaining)
                else:
                    read_count = samples_per_read

                data = state["task"].read(number_of_samples_per_channel=read_count, timeout=10.0)
                channels = task_cfg["channels"]
                channel_data = normalize_channel_data(data, len(channels), read_count)
                objects = []
                for channel_name, values in zip(channels, channel_data):
                    objects.append(
                        ChannelObject(
                            task_group_name(task_cfg),
                            channel_tdms_name(channel_name),
                            tdms_array(np, values),
                            properties={"physical_channel": channel_name},
                        )
                    )
                writer.write_segment(objects)
                state["samples_written"] += read_count
                if is_finite(task_cfg) and state["samples_written"] >= int(state["total_samples"] or 0):
                    state["done"] = True

        for task_obj in reversed(task_objects):
            try:
                task_obj.stop()
            except Exception:
                pass

    print(f"TDMS written: {output_path}")
    for task_cfg in tasks:
        matching = [state for state in input_states if state["config"] is task_cfg]
        if matching:
            print(f"task_samples[{task_cfg['name']}]: {matching[0]['samples_written']}")
        else:
            print(f"task_executed[{task_cfg['name']}]: output")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NI DAQ schema-v2 tasks and write TDMS input data.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args.config, args.output, args.stop_file)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        time.sleep(0.1)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
