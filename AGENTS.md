# Project Agent Entry Point

This project exposes a stable natural-language interface for NI DAQ sampling.

Use the project skill for all NI DAQ sampling work:

`.agents/skills/ni-daq-sampling-control/SKILL.md`

Detailed agent instructions live at:

`.agents/skills/ni-daq-sampling-control/agents/AGENTS.md`

External operator-facing surfaces:

- Edit sampling settings in `sampling_config.docx`.
- Describe desired sampling-configuration changes in natural language; the agent must apply those changes to the local `sampling_config.docx` before treating the configuration as changed.
- Send natural-language commands such as `开始采样` or `停止采样`.
- Read returned TDMS files and logs from `data/`.

When the web console provides an active configuration draft path, configuration changes must edit that draft Word document instead of the formal `sampling_config.docx`. Outside draft mode, start from the local Word document `sampling_config.docx`. Do not make `sampling_config.md`, chat text, or inferred memory the source of truth. Edit the target Word document's single `sampling-config` TOML block, validate it, then send the complete current sampling configuration back to the user for confirmation. Continue applying the user's requested adjustments to the active target document and resending the complete configuration until the user confirms that the configuration is correct. Do not start sampling from a newly changed configuration until this confirmation loop is complete, the web console has saved the draft as the formal configuration when applicable, and the separate start confirmation is also received.

Before starting or stopping sampling, always ask for explicit user confirmation in the conversation. Do not run the start or stop control command until the user confirms with clear text such as `确认启动`, `确认开始`, `y`, `确认停止`, or `确认终止`. If the client sends an empty message immediately after the confirmation prompt, treat it as pressing Enter to confirm. If the client does not send empty messages, tell the user to reply with `y`.
