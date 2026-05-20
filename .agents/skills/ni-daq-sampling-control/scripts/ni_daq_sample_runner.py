#!/usr/bin/env python3
"""NI DAQ analog input runner that writes TDMS segments."""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    print("Python 3.11+ is required because this script uses tomllib.", file=sys.stderr)
    sys.exit(2)


def extract_config(config_path: Path) -> dict[str, Any]:
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r"```toml(?:\s+sampling-config)?\s*\n(.*?)\n```", text, re.S)
    if not match:
        raise ValueError(f"No TOML config block found in {config_path}")
    return tomllib.loads(match.group(1))


def terminal_config_constant(nidaqmx_constants: Any, name: str) -> Any | None:
    normalized = (name or "default").lower()
    mapping = {
        "default": None,
        "rse": nidaqmx_constants.TerminalConfiguration.RSE,
        "nrse": nidaqmx_constants.TerminalConfiguration.NRSE,
        "diff": nidaqmx_constants.TerminalConfiguration.DIFF,
        "differential": nidaqmx_constants.TerminalConfiguration.DIFF,
        "pseudo_diff": nidaqmx_constants.TerminalConfiguration.PSEUDO_DIFF,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported terminal_config: {name}")
    return mapping[normalized]


def normalize_read_data(data: Any, channel_count: int) -> list[list[float]]:
    if channel_count == 1:
        return [list(data)]
    return [list(channel_data) for channel_data in data]


def channel_tdms_name(channel_name: str) -> str:
    return channel_name.replace("/", "_")


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
    acquisition = config["acquisition"]
    channels = config["channels"]["ai"]
    tdms = config["tdms"]

    sample_rate = float(acquisition["sample_rate_hz"])
    samples_per_read = int(acquisition["samples_per_read"])
    voltage_min = float(acquisition["voltage_min"])
    voltage_max = float(acquisition["voltage_max"])
    finite = acquisition["mode"] == "finite"
    duration_seconds = float(acquisition.get("duration_seconds", 0.0))
    group_name = tdms.get("group_name", "AnalogInput")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    terminal_config = terminal_config_constant(constants, acquisition.get("terminal_config", "default"))
    sample_mode = constants.AcquisitionType.FINITE if finite else constants.AcquisitionType.CONTINUOUS
    total_samples = int(sample_rate * duration_seconds) if finite else None

    root = RootObject(
        properties={
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "config_path": str(config_path),
            "sample_rate_hz": sample_rate,
            "voltage_min": voltage_min,
            "voltage_max": voltage_max,
            "mode": acquisition["mode"],
        }
    )
    group = GroupObject(group_name, properties={"channel_count": len(channels)})

    with nidaqmx.Task() as task, TdmsWriter(str(output_path)) as writer:
        for channel in channels:
            kwargs = {
                "physical_channel": channel,
                "min_val": voltage_min,
                "max_val": voltage_max,
            }
            if terminal_config is not None:
                kwargs["terminal_config"] = terminal_config
            task.ai_channels.add_ai_voltage_chan(**kwargs)

        timing_kwargs = {
            "rate": sample_rate,
            "sample_mode": sample_mode,
            "samps_per_chan": samples_per_read,
        }
        task.timing.cfg_samp_clk_timing(**timing_kwargs)
        writer.write_segment([root, group])
        task.start()

        samples_written = 0
        while True:
            if stop_file.exists():
                break
            if finite and total_samples is not None:
                remaining = total_samples - samples_written
                if remaining <= 0:
                    break
                read_count = min(samples_per_read, remaining)
            else:
                read_count = samples_per_read

            data = task.read(number_of_samples_per_channel=read_count, timeout=10.0)
            channel_data = normalize_read_data(data, len(channels))
            objects = []
            for channel_name, values in zip(channels, channel_data):
                array = np.asarray(values, dtype=np.float64)
                objects.append(
                    ChannelObject(
                        group_name,
                        channel_tdms_name(channel_name),
                        array,
                        properties={"physical_channel": channel_name},
                    )
                )
            writer.write_segment(objects)
            samples_written += read_count

        task.stop()

    print(f"TDMS written: {output_path}")
    print(f"samples_per_channel: {samples_written}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NI DAQ acquisition and write TDMS.")
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
