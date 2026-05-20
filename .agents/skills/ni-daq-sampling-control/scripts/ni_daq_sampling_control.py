#!/usr/bin/env python3
"""Project-local NI DAQ sampling control entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shlex
import subprocess
import sys
import tempfile
import time
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


def find_project_root() -> Path:
    for parent in SCRIPT_PATH.parents:
        if (parent / "sampling_config.md").exists():
            return parent
    raise RuntimeError("Could not find project root containing sampling_config.md.")


PROJECT_ROOT = find_project_root()
DEFAULT_CONFIG = PROJECT_ROOT / "sampling_config.md"


class ConfigError(ValueError):
    pass


def _project_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _extract_toml(markdown: str) -> str:
    pattern = re.compile(r"```toml(?:\s+sampling-config)?\s*\n(.*?)\n```", re.S)
    matches = pattern.findall(markdown)
    if not matches:
        raise ConfigError("No fenced TOML sampling-config block found in sampling_config.md.")
    if len(matches) > 1:
        tagged = re.findall(r"```toml\s+sampling-config\s*\n(.*?)\n```", markdown, re.S)
        if len(tagged) == 1:
            return tagged[0]
        raise ConfigError("Multiple TOML blocks found; keep exactly one sampling-config block.")
    return matches[0]


def load_config(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    toml_text = _extract_toml(config_path.read_text(encoding="utf-8"))
    try:
        config = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    for section in ("host", "acquisition", "channels", "tdms", "control"):
        if section not in config:
            raise ConfigError(f"Missing required section [{section}].")

    host = config["host"]
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

    acquisition = config["acquisition"]
    sample_rate = acquisition.get("sample_rate_hz")
    if not isinstance(sample_rate, (int, float)) or sample_rate <= 0:
        raise ConfigError("acquisition.sample_rate_hz must be a positive number.")
    samples_per_read = acquisition.get("samples_per_read")
    if not isinstance(samples_per_read, int) or samples_per_read <= 0:
        raise ConfigError("acquisition.samples_per_read must be a positive integer.")
    if acquisition.get("voltage_min") >= acquisition.get("voltage_max"):
        raise ConfigError("acquisition.voltage_min must be lower than acquisition.voltage_max.")
    if acquisition.get("mode") not in {"continuous", "finite"}:
        raise ConfigError('acquisition.mode must be "continuous" or "finite".')
    if acquisition.get("mode") == "finite" and acquisition.get("duration_seconds", 0) <= 0:
        raise ConfigError("finite acquisition requires acquisition.duration_seconds > 0.")

    channels = config["channels"]
    ai_channels = channels.get("ai", [])
    if not isinstance(ai_channels, list) or not all(isinstance(ch, str) and ch for ch in ai_channels):
        raise ConfigError("channels.ai must be a list of non-empty strings.")
    if not ai_channels:
        raise ConfigError("At least one analog input channel is required in channels.ai.")
    if channels.get("di"):
        raise ConfigError("Digital input capture is not implemented yet; keep channels.di empty.")
    if channels.get("ao"):
        raise ConfigError("Analog output generation is not implemented yet; keep channels.ao empty.")

    tdms = config["tdms"]
    if not tdms.get("output_dir"):
        raise ConfigError("tdms.output_dir is required.")
    if not tdms.get("filename", "").endswith(".tdms"):
        raise ConfigError("tdms.filename must end with .tdms.")

    control = config["control"]
    for key in ("state_file", "stop_file", "log_file"):
        if not control.get(key):
            raise ConfigError(f"control.{key} is required.")


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


def remote_worker_config_text(config_path: Path) -> str:
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r"```toml(?:\s+sampling-config)?\s*\n(.*?)\n```", text, re.S)
    if not match:
        raise ConfigError("No fenced TOML sampling-config block found in sampling_config.md.")
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
    updated_toml, count = re.subn(r"(?ms)^\[host\]\n.*?(?=^\[|\Z)", host_block + "\n", toml_text, count=1)
    if count != 1:
        raise ConfigError("Could not rewrite [host] block for remote worker config.")
    remote_text = text[: match.start()] + "```toml sampling-config\n" + updated_toml.rstrip() + "\n```" + text[match.end() :]
    remote_text = remote_text.replace(
        "当前配置为 SSH 编排模式：本机控制脚本连接 `admin@192.168.1.103`，在 `/home/admin/project1` 运行采样，停止后将 TDMS 自动回传到本机 `data/`。",
        "当前文件是远端测量主机上的 worker 配置副本，使用 `host.mode = \"local\"` 直接运行采样。外部操作入口仍是本机项目根目录的 `sampling_config.md`。",
    )
    return remote_text


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

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as temp_config:
        temp_config.write(remote_worker_config_text(config_path))
        temp_config_path = Path(temp_config.name)
    try:
        config_result = run_checked([*scp_args(host), str(temp_config_path), f"{ssh_target(host)}:{remote_dir}/sampling_config.md"])
        if config_result.returncode != 0:
            print_completed(config_result)
            raise ConfigError("Failed to copy sampling_config.md to the measurement host.")
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


def prompt_confirm_start(config: dict[str, Any], paths: dict[str, Path]) -> bool:
    acquisition = config["acquisition"]
    channels = config["channels"]
    print("About to start NI DAQ sampling:")
    print(f"  mode: {acquisition['mode']}")
    print(f"  sample_rate_hz: {acquisition['sample_rate_hz']}")
    print(f"  voltage_range: {acquisition['voltage_min']} to {acquisition['voltage_max']} V")
    print(f"  ai_channels: {', '.join(channels['ai'])}")
    print(f"  output: {paths['output']}")
    answer = input("Press Enter to confirm start, or type n to cancel: ").strip().lower()
    return answer in {"", "y", "yes", "确认", "确认启动", "确认开始"}


def prompt_confirm_stop(state: dict[str, Any] | None) -> bool:
    print("About to stop NI DAQ sampling:")
    if state:
        print(f"  pid: {state.get('pid')}")
        print(f"  output: {state.get('output_path')}")
        print(f"  started_at: {state.get('started_at')}")
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
        "command": cmd,
    }
    write_state(paths["state"], state)
    log_handle.close()
    print("Sampling started")
    print(f"pid: {process.pid}")
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
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to sampling_config.md")
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
