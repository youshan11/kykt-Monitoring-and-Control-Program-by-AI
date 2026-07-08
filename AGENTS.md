# Project Agent Entry Point

This project exposes a stable natural-language interface for NI DAQ sampling.

Use the project skill for all NI DAQ sampling work:

`.agents/skills/ni-daq-sampling-control/SKILL.md`

Detailed agent instructions live at:

`.agents/skills/ni-daq-sampling-control/agents/AGENTS.md`

External operator-facing surfaces:

- Edit sampling settings in `sampling_config.docx`.
- Send natural-language commands such as `开始采样` or `停止采样`.
- Read returned TDMS files and logs from `data/`.

Before starting or stopping sampling, always ask for explicit user confirmation in the conversation. Do not run the start or stop control command until the user confirms with clear text such as `确认启动`, `确认开始`, `y`, `确认停止`, or `确认终止`. If the client sends an empty message immediately after the confirmation prompt, treat it as pressing Enter to confirm. If the client does not send empty messages, tell the user to reply with `y`.
