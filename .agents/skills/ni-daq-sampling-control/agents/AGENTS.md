# NI DAQ Sampling Agent Instructions

This agent controls NI DAQ data sampling and output tasks on a measurement host.

The external contract is intentionally small:

- `sampling_config.docx` is the operator-editable schema v2 configuration.
- Natural-language commands start, stop, validate, or inspect sampling.
- `data/` contains returned TDMS files and logs.

Treat Chinese commands such as `开始采样`, `采样开始`, `启动采样`, `开始采集`, `启动采集`, `停止采样`, `采样停止`, `结束采样`, `停止采集`, and `结束采集` as NI DAQ sampling control commands.

Before starting or stopping sampling, always ask for explicit user confirmation in the conversation. Do not run the start or stop control command until the user confirms with clear text such as `确认启动`, `确认开始`, `y`, `确认停止`, or `确认终止`. If the client sends an empty message immediately after the confirmation prompt, treat it as pressing Enter to confirm. If the client does not send empty messages, tell the user to reply with `y`.

The normal project entry points are:

- Configuration document: `sampling_config.docx`
- Control script: `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py`
- Sampling runner: `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sample_runner.py`
- Board inventory reference: `.agents/skills/ni-daq-sampling-control/references/ni-daq-channel-inventory.md`
- Config format reference: `.agents/skills/ni-daq-sampling-control/references/sampling-config-format.md`

`sampling_config.docx` must use `schema_version = 2` and `[[tasks]]`. One task is one NI-DAQmx task. Summaries before start should list enabled tasks with their `name`, `type`, `channels`, timing mode, and output/log paths.

Current runner support for enabled tasks is documented in `references/sampling-config-format.md`. Do not enable triggered tasks, non-voltage AI measurements, hardware-timed AO/DO waveforms, or CI modes other than `count_edges` until the runner supports them.

If `sampling_config.docx` is edited into non-standard or natural-language text, inspect the raw fenced block and infer the intended legal TOML setting only when the meaning is clear. Summarize the inferred change to the user and ask for confirmation before editing the config. After confirmation, rewrite only the affected lines into valid schema v2 TOML, rerun validation, and only then continue to start/stop/status workflow as appropriate. If the intent is ambiguous or unsafe, ask a concise clarification and do not change hardware state.

Use the SSH details from `sampling_config.docx`. If SSH mode is selected but connection details are missing, report the missing fields instead of inventing host, port, username, authentication, or remote paths.
