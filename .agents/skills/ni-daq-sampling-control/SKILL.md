---
name: ni-daq-sampling-control
description: Control NI DAQ data sampling workflows from natural-language commands. Use when the user asks to start, stop, configure, generate, validate, or inspect NI DAQ sampling, including Chinese commands such as 开始采样, 采样开始, 启动采样, 开始采集, 启动采集, 停止采样, 采样停止, 结束采样, 停止采集, or 结束采集. The skill reads project sampling configuration documents, uses the board inventory reference, manages sampling processes, and verifies TDMS output files.
---

# NI DAQ Sampling Control

## Command Intent

Treat these as start commands:

- `开始采样`
- `采样开始`
- `启动采样`
- `开始采集`
- `启动采集`
- `开始记录`

Treat these as stop commands:

- `停止采样`
- `采样停止`
- `结束采样`
- `停止采集`
- `结束采集`
- `停止记录`

If the user command is ambiguous, ask one concise clarification before touching hardware or sampling processes.

## Required Confirmation

Starting and stopping sampling are hardware/process control actions. Always ask for explicit confirmation before running either action.

For start commands, summarize the active config and ask the user to confirm. Accept `确认启动`, `确认开始`, or `y`. If the client sends an empty message immediately after the prompt, treat it as pressing Enter to confirm. Do not run `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py start --confirm-start` until the user confirms.

For stop commands, summarize the recorded active process if available and ask the user to confirm. Accept `确认停止`, `确认终止`, or `y`. If the client sends an empty message immediately after the prompt, treat it as pressing Enter to confirm. Do not run `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py stop --confirm-stop` until the user confirms.

Validation and status commands do not require confirmation.

## Project Files

Use these project-local files:

- `sampling_config.md`: user-editable sampling configuration document.
- `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py`: control entry point for `start`, `stop`, `status`, and `validate`.
- `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sample_runner.py`: acquisition process that talks to NI DAQ and writes TDMS.
- `.sampling_state.json`: runtime state file created by the control script.
- `.sampling_stop`: graceful shutdown signal file.
- `data/`: default TDMS output and log directory.

Use these references only when needed:

- `.agents/skills/ni-daq-sampling-control/references/sampling-config-format.md`: configuration format and field meanings.
- `.agents/skills/ni-daq-sampling-control/references/ni-daq-channel-inventory.md`: known board, channel, and target-host inventory exported from the measurement host.

## Start Workflow

When the user asks to start sampling:

1. Read `sampling_config.md`.
2. If the fenced TOML block is invalid because the user edited it in natural language or a non-standard format, infer the intended settings from the raw text when the meaning is clear. Summarize the inferred normalized config changes and ask the user to confirm before editing the file. After confirmation, rewrite only the affected lines into valid TOML, then continue. If the intent is ambiguous, ask one concise clarification and do not edit or start sampling.
3. Validate the config with `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py validate`.
4. Check channel names against `.agents/skills/ni-daq-sampling-control/references/ni-daq-channel-inventory.md` when changing or debugging channels.
5. Ask for explicit user confirmation.
6. Start sampling with `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py start --confirm-start` only after confirmation.
7. Report the process ID, config path, expected TDMS path, and log path.

Do not start sampling if required fields are missing, if the TDMS output file already exists and overwrite is disabled, or if SSH/remote execution is requested but not configured.

## Config Normalization

Operators may edit `sampling_config.md` informally, for example replacing `sample_rate_hz = 200` with text such as `采样率设置为200hz`. Treat this as an editable operator surface, not an immediate hard failure.

When the TOML cannot be parsed:

1. Read the raw fenced block and infer the intended standard TOML fields only when the mapping is clear.
2. Tell the user exactly what you inferred and what legal TOML change you will make.
3. Get explicit confirmation before editing `sampling_config.md`.
4. Use `apply_patch` to convert the non-standard text into valid TOML while preserving unrelated settings.
5. Run `validate` again before any start command.

Do not invent missing host details, channel names, timing values, or unsupported acquisition modes. If a non-standard edit conflicts with existing settings or cannot be mapped confidently, ask a clarification and stop before changing hardware state.

## Stop Workflow

When the user asks to stop sampling:

1. Run `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py status` to inspect the recorded active process.
2. Ask for explicit user confirmation.
3. Run `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py stop --confirm-stop` only after confirmation.
4. Wait for the runner to exit gracefully.
5. Verify whether the expected TDMS file exists.
6. Report the final TDMS path, file size if available, and any warnings from the control script.

If no active sampling process is recorded, report that no running sampling process was found.

## Status And Validation

Use:

- `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py status` to inspect current state.
- `python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py validate` after editing `sampling_config.md`.

## Implementation Rules

Prefer updating the reusable scripts over generating one-off sampling programs.

Use project-local state and output files. Do not kill unrelated processes. Only stop a process recorded in `.sampling_state.json` for this project.

If `nidaqmx`, `nptdms`, `numpy`, NI drivers, or device connectivity are missing, report the missing dependency or hardware condition and do not claim that sampling is running.

Use the SSH details from `sampling_config.md`. If SSH mode is selected but connection details are missing, report the missing fields instead of inventing host, port, username, authentication, remote paths, or Python environment details.
