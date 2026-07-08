# Sampling Config Format

The project config lives in `sampling_config.docx` as a Word document. It must contain one fenced TOML block tagged `sampling-config`.

The current format is schema version 2. It describes DAQ work as a list of explicit NI-DAQmx tasks.

## Required Top-Level Fields

```toml
schema_version = 2
```

Required sections:

- `[host]`: execution mode. `mode = "ssh"` orchestrates a remote measurement host and copies TDMS output back after stop. `mode = "local"` runs directly on the measurement host.
- `[tdms]`: TDMS output directory and filename pattern.
- `[control]`: project-local state file, stop signal file, log file, and process timeouts.
- `[[tasks]]`: one or more DAQ tasks. At least one task must have `enabled = true`.

Use `references/ni-daq-channel-inventory.md` to verify channel names, and `references/ni-daq-sampling-options.md` for detailed board capabilities.

## Host Section

```toml
[host]
mode = "ssh"
ssh_host = "192.168.1.103"
ssh_port = 22
ssh_user = "admin"
remote_project_dir = "/home/admin/project1"
python = "python3"
```

Important fields:

- `host.mode`: `ssh` or `local`.
- `host.ssh_host`, `host.ssh_port`, `host.ssh_user`: SSH target used in SSH mode.
- `host.remote_project_dir`: remote project directory where scripts/config are synced and sampling runs.
- `host.python`: Python executable on the measurement host.

In SSH mode, the control script rewrites a remote worker copy of `sampling_config.docx` to `host.mode = "local"` before execution. The local copy remains the operator-facing source of truth.

## TDMS And Control Sections

```toml
[tdms]
output_dir = "data"
filename = "ni_daq_sample_{timestamp}.tdms"
allow_overwrite = false

[control]
state_file = ".sampling_state.json"
stop_file = ".sampling_stop"
log_file = "data/sampling.log"
startup_check_seconds = 1.0
stop_timeout_seconds = 15.0
```

`tdms.filename` must end with `.tdms` and may include `{timestamp}`. Input tasks write one TDMS group per task using `tdms_group` or the task `name`.

## Task Model

One `[[tasks]]` entry corresponds to one NI-DAQmx task: one I/O type, one channel set, one timing model, and one output TDMS group if it records data.

Common fields:

- `name`: unique task name. Use stable ASCII names for logs and state.
- `enabled`: boolean. Only enabled tasks are executed.
- `type`: one of `ai`, `di`, `do`, `ao`, `ci`, or `co`.
- `channels`: physical channels, ports, lines, or counters.
- `tdms_group`: TDMS group name for input tasks. Defaults to `name` when omitted.

Timing fields:

- `timing_mode`: `static`, `finite`, `continuous`, or `hw_timed_single_point`.
- `sample_rate_hz`: required for hardware-timed input tasks.
- `samples_per_read`: required for hardware-timed input tasks.
- `samples_per_write`: reserved for future hardware-timed output tasks.
- `duration_seconds`: required for `finite` tasks.
- `sample_clock_source`: required for board/task combinations that need an external or shared sample clock.

Trigger fields may be written as `[[tasks.triggers]]`, but the current runner does not execute triggered tasks yet. Enabled tasks with triggers are rejected during validation.

## Supported Runner Scope

The current runner supports these enabled task combinations:

- `ai`: voltage input on `PXI1Slot5` or `PXI1Slot6`, `finite` or `continuous`, TDMS output.
- `di`: digital input on `PXI1Slot3`, `PXI1Slot5`, `PXI1Slot6`, or `PXI1Slot8`; `PXI1Slot8` is static only; timed `PXI1Slot5`/`PXI1Slot6` DI requires `sample_clock_source`.
- `do`: static digital output.
- `ao`: static voltage/current output on `PXI1Slot8`.
- `ci`: count-edges counter input, static or sampled. Sampled CI requires `sample_clock_source`.
- `co`: continuous pulse output with `pulse_frequency`, `pulse_time`, or `pulse_ticks`.

The runner does not yet support:

- AI measurements other than voltage.
- Hardware-timed AO or DO waveforms.
- Triggered tasks.
- CI modes other than `count_edges`.
- Mixed-task hardware synchronization beyond explicit `sample_clock_source` configuration.

## Examples

### Analog Input Voltage

```toml
[[tasks]]
name = "ai_slot5_voltage"
enabled = true
type = "ai"
channels = ["PXI1Slot5/ai0"]
tdms_group = "ai_slot5_voltage"
measurement = "voltage"
terminal_config = "default"
voltage_min = -10.0
voltage_max = 10.0
coupling = "DC"
custom_scale = ""
timing_mode = "continuous"
sample_rate_hz = 200
samples_per_read = 200
duration_seconds = 0.0
sample_clock_source = ""
```

PXI-6133 AI constraints:

- Channels: `PXI1Slot5/ai0` through `PXI1Slot5/ai7`, or `PXI1Slot6/ai0` through `PXI1Slot6/ai7`.
- `terminal_config`: `default` or `diff`.
- Voltage ranges: ±1.25 V, ±2.5 V, ±5 V, or ±10 V.
- `timing_mode`: `finite` or `continuous`.

### Digital Input

```toml
[[tasks]]
name = "di_slot3_port0"
enabled = true
type = "di"
channels = ["PXI1Slot3/port0"]
tdms_group = "di_slot3_port0"
line_grouping = "chan_for_all_lines"
read_format = "port_uint32"
timing_mode = "finite"
sample_rate_hz = 1000
samples_per_read = 100
duration_seconds = 5.0
sample_clock_source = ""
```

Whole-port channels such as `PXI1Slot3/port0` must use `line_grouping = "chan_for_all_lines"`.

### Static Digital Output

```toml
[[tasks]]
name = "do_slot3_port0"
enabled = true
type = "do"
channels = ["PXI1Slot3/port0"]
line_grouping = "chan_for_all_lines"
write_format = "port_uint32"
initial_state = 0
timing_mode = "static"
```

### Static Analog Output

```toml
[[tasks]]
name = "ao_slot8_voltage_0"
enabled = true
type = "ao"
channels = ["PXI1Slot8/ao0"]
output_type = "voltage"
value = 0.0
voltage_min = -10.24
voltage_max = 10.24
timing_mode = "static"
```

`PXI1Slot8/ao0` through `ao15` are voltage outputs. `PXI1Slot8/ao16` through `ao31` are current outputs.

### Count-Edges Counter Input

```toml
[[tasks]]
name = "ci_slot3_count_edges"
enabled = true
type = "ci"
channels = ["PXI1Slot3/ctr0"]
tdms_group = "ci_slot3_count_edges"
counter_mode = "count_edges"
edge = "rising"
initial_count = 0
count_direction = "up"
source_terminal = "/PXI1Slot3/PFI0"
gate_terminal = ""
timing_mode = "continuous"
sample_rate_hz = 1000
samples_per_read = 100
sample_clock_source = "/PXI1Slot3/di/SampleClock"
```

### Continuous Counter Pulse Output

```toml
[[tasks]]
name = "co_slot3_pulse_freq"
enabled = true
type = "co"
channels = ["PXI1Slot3/ctr0"]
counter_mode = "pulse_frequency"
frequency = 1000.0
duty_cycle = 0.5
timing_mode = "continuous"
```
