# Sampling Config Format

The project config lives in `sampling_config.md` as a Markdown document with one fenced TOML block tagged `sampling-config`.

Required sections:

- `[host]`: execution mode. This project normally uses `mode = "ssh"` so the local control script can orchestrate sampling on the measurement host and pull TDMS output back after stop. `mode = "local"` is still supported for running directly on the measurement host.
- `[acquisition]`: sampling behavior, voltage range, timing, and read chunk size.
- `[channels]`: channel lists. Analog input channels are currently implemented by `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sample_runner.py`.
- `[tdms]`: TDMS output directory and filename.
- `[control]`: project-local state, stop, and log paths.

Important fields:

- `host.mode`: `ssh` for local orchestration of a remote measurement host, or `local` when running on the measurement host itself.
- `host.ssh_host`, `host.ssh_port`, `host.ssh_user`: SSH target used when `host.mode = "ssh"`.
- `host.remote_project_dir`: remote project directory where scripts/config are synced and sampling runs.
- `host.python`: Python executable on the remote measurement host.
- `acquisition.sample_rate_hz`: DAQ sample clock rate.
- `acquisition.voltage_min` and `acquisition.voltage_max`: analog input voltage range.
- `acquisition.mode`: `continuous` or `finite`.
- `acquisition.duration_seconds`: required for finite acquisition.
- `acquisition.samples_per_read`: samples per channel read from DAQmx on each loop.
- `channels.ai`: analog input channel names, such as `PXI1Slot5/ai0`.
- `tdms.filename`: output filename. It may include `{timestamp}`.
- `tdms.allow_overwrite`: whether an existing TDMS file may be replaced.

Use `references/ni-daq-channel-inventory.md` to verify valid slot and channel names.

Example:

```toml sampling-config
[host]
mode = "ssh"
ssh_host = "192.168.1.103"
ssh_port = 22
ssh_user = "admin"
remote_project_dir = "/home/admin/project1"
python = "python3"

[acquisition]
mode = "continuous"
sample_rate_hz = 100
samples_per_read = 100
voltage_min = -10.0
voltage_max = 10.0
terminal_config = "default"
duration_seconds = 0.0

[channels]
ai = ["PXI1Slot5/ai0"]
di = []
ao = []

[tdms]
output_dir = "data"
filename = "ni_daq_sample_{timestamp}.tdms"
allow_overwrite = false
group_name = "AnalogInput"

[control]
state_file = ".sampling_state.json"
stop_file = ".sampling_stop"
log_file = "data/sampling.log"
startup_check_seconds = 1.0
stop_timeout_seconds = 15.0
```

In SSH mode, `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py` rewrites the remote copy of `sampling_config.md` to `host.mode = "local"` before execution. The local copy remains in SSH mode and is the operator-facing source of truth.
