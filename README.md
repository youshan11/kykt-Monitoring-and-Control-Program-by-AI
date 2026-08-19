# Project1 使用说明

本项目用于通过自然语言或命令行控制 NI DAQ 采集任务。项目的生效配置来自 `sampling_config.docx` 中唯一的 `toml sampling-config` 代码块；`sampling_config.md` 主要是同格式的说明和模板参考，不是默认生效配置。

## 项目结构

```text
project1/
├── AGENTS.md
├── README.md
├── sampling_config.docx
├── sampling_config.md
├── sampling_config.docx.bak_agent_ai_template
├── sampling_config.docx.bak_agent_do_template
├── 采集任务1.docx
├── data/
│   ├── *.tdms
│   └── sampling.log
├── web_console/
│   ├── README.md
│   ├── requirements.txt
│   ├── backend/
│   │   ├── app.py
│   │   ├── agent_bridge.py
│   │   ├── config.py
│   │   └── file_store.py
│   ├── frontend/
│   │   ├── index.html
│   │   ├── app.js
│   │   └── styles.css
│   └── runtime/
│       ├── conversations/
│       ├── uploads/
│       ├── backups/
│       ├── config_drafts/
│       └── *.json / *.jsonl / *.log
└── .agents/
    └── skills/
        └── ni-daq-sampling-control/
            ├── SKILL.md
            ├── scripts/
            │   ├── ni_daq_sampling_control.py
            │   └── ni_daq_sample_runner.py
            └── references/
                ├── sampling-config-format.md
                ├── ni-daq-channel-inventory.md
                └── ni-daq-sampling-options.md
```

主要职责：

- `sampling_config.docx`：操作员配置入口，必须包含一个 `toml sampling-config` 代码块。
- `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py`：采样控制入口，提供 `validate`、`start`、`stop`、`status`。
- `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sample_runner.py`：真正连接 NI-DAQmx、执行任务并写入 TDMS 的 runner，一般不直接手工调用。
- `web_console/`：本地网页控制台，提供配置上传/草稿确认、自然语言对话、快捷采样控制、TDMS 下载和对话历史。
- `data/`：默认 TDMS 输出目录和采样日志目录。
- `web_console/runtime/`：网页运行状态、对话、上传配置、配置草稿和备份目录。

## 环境要求

- 控制脚本需要 Python 3.11 或更新版本，因为使用了标准库 `tomllib`。
- `web_console` 本身只使用 Python 标准库，`web_console/requirements.txt` 当前没有额外依赖。
- 如果通过网页发送自然语言命令，需要本机能运行 `codex` CLI。
- 如果 `sampling_config.docx` 中 `[host].mode = "ssh"`，本机需要能通过 SSH 连接测量主机；脚本会把控制脚本和临时 worker 配置同步到远端运行。
- 测量主机需要安装 NI 驱动、NI-DAQmx Python 支持、`numpy` 和 `nptdms`。缺少这些依赖时，runner 会报错并退出。

当前模板配置中常见默认值：

```toml
[host]
mode = "ssh"
ssh_host = "192.168.1.103"
ssh_port = 22
ssh_user = "admin"
remote_project_dir = "/home/admin/project1"
python = "python3"
```

## 启动 Web 控制台

在项目根目录运行：

```bash
cd /home/kangjs/workspace/project1
python3 -m web_console.backend.app
```

默认监听地址：

```text
http://127.0.0.1:8765
```

如果从另一台机器访问，可先建立 SSH 端口转发：

```bash
ssh -L 8765:127.0.0.1:8765 buaa-dev
```

然后在本机浏览器打开：

```text
http://127.0.0.1:8765
```

可用环境变量覆盖默认行为：

```bash
WEB_CONSOLE_HOST=127.0.0.1
WEB_CONSOLE_PORT=8765
WEB_CONSOLE_CODEX_BIN=codex
WEB_CONSOLE_CODEX_TIMEOUT_SECONDS=600
WEB_CONSOLE_CODEX_HISTORY_LIMIT=6
WEB_CONSOLE_CODEX_MODEL=
WEB_CONSOLE_CODEX_REASONING_EFFORT=low
WEB_CONSOLE_CODEX_EXTRA_ARGS="--dangerously-bypass-approvals-and-sandbox"
WEB_CONSOLE_CODEX_RESUME_EXTRA_ARGS="--dangerously-bypass-approvals-and-sandbox"
```

网页默认把后端 Codex 以较高权限运行，这是为了稳定修改 DOCX 和执行硬件/SSH 控制命令。只应在可信本机环境使用。

## Web 控制台使用逻辑

网页左侧是 agent 对话和快捷采样按钮，右侧是 TDMS 文件、配置历史和对话记录。

常用流程：

1. 上传或确认当前 `sampling_config.docx`。
2. 点击“预览 DOCX”检查当前配置。
3. 如需修改配置，在输入框用自然语言描述修改。后端会先创建 `web_console/runtime/config_drafts/` 下的 DOCX 草稿，让 agent 只修改草稿。
4. agent 修改草稿后会运行配置校验，并返回完整 `sampling-config` TOML 供确认。
5. 确认无误后点击“确认修改文件”或发送“确认修改/修改完成”，后端才会把草稿保存为正式 `sampling_config.docx`，同时写入配置历史和备份。
6. 点击“开始执行”或发送“开始采样”。后端先校验正式配置并创建启动确认。
7. 点击“确认启动”或发送“确认启动/确认开始/y”，后端才会执行采样启动命令。
8. 点击“停止执行”后，再点击“确认停止”或发送“确认停止/确认终止/y”，后端才会停止采样。
9. 采样产生的 TDMS 文件会出现在右侧列表，也可在 `data/` 中直接查看。

重要限制：

- 有未确认配置草稿时不能启动采样，也不能切换配置历史或上传新配置。
- 开始和停止采样都需要二次确认。
- 配置修改必须以 DOCX 内的 `sampling-config` TOML 块为准，不能把聊天记录或 `sampling_config.md` 当作生效配置。

## 命令行控制

控制入口：

```bash
cd /home/kangjs/workspace/project1
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py --help
```

校验配置：

```bash
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py validate
```

查询状态：

```bash
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py status
```

交互式启动，终端提示后按 Enter 确认：

```bash
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py start --prompt-confirm-start
```

已在外部完成确认后启动：

```bash
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py start --confirm-start
```

交互式停止，终端提示后按 Enter 确认：

```bash
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py stop --prompt-confirm-stop
```

已在外部完成确认后停止：

```bash
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py stop --confirm-stop
```

指定其它配置文档：

```bash
python3 .agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py validate --config /path/to/sampling_config.docx
```

`start` 和 `stop` 如果没有确认参数会直接拒绝执行。

## 采样控制内部逻辑

`ni_daq_sampling_control.py` 的工作流程：

1. 定位项目根目录。
2. 从 `sampling_config.docx` 抽取唯一 `toml sampling-config` 代码块。
3. 解析 TOML 并校验 `schema_version = 2`、`[host]`、`[tdms]`、`[control]` 和 `[[tasks]]`。
4. 根据 `enabled = true` 的任务生成输出路径、状态文件路径、停止信号文件路径和日志路径。
5. `host.mode = "local"` 时，在本机启动 `ni_daq_sample_runner.py`。
6. `host.mode = "ssh"` 时，把脚本和 worker 配置同步到远端主机，在远端以 local 模式执行；停止时从远端拉回 TDMS 文件。
7. 运行状态写入 `.sampling_state.json`，停止信号写入 `.sampling_stop`，日志写入 `data/sampling.log`。

`ni_daq_sample_runner.py` 的工作流程：

1. 导入 `nidaqmx`、`numpy`、`nptdms`。
2. 按 enabled tasks 创建 NI-DAQmx task。
3. 配置 AI/DI/DO/AO/CI/CO 通道、时序和输出参数。
4. 对输入任务写 TDMS group/channel；对静态输出任务保持输出状态；对连续任务循环读取，直到 finite 任务完成或发现 stop 文件。
5. 退出前停止所有 NI-DAQmx task，并输出每个任务的样本数或执行结果。

## 配置文件要点

`sampling_config.docx` 的 TOML 必须包含：

```toml
schema_version = 2

[host]

[tdms]

[control]

[[tasks]]
```

至少一个 `[[tasks]]` 需要 `enabled = true`。当前 runner 支持的 enabled task 范围：

- `ai`：`PXI1Slot5` 或 `PXI1Slot6` 的电压输入，`finite` 或 `continuous`。
- `di`：支持的数字输入；`PXI1Slot8` 仅静态；`PXI1Slot5/6` 的硬件定时 DI 需要 `sample_clock_source`。
- `do`：静态数字输出。
- `ao`：`PXI1Slot8` 静态电压/电流输出。
- `ci`：`count_edges` 计数器输入，静态或采样。
- `co`：连续脉冲输出。

当前不支持已启用的触发任务、非电压 AI、硬件定时 AO/DO 波形、`count_edges` 以外的 CI 模式。

更完整字段说明见：

```text
.agents/skills/ni-daq-sampling-control/references/sampling-config-format.md
.agents/skills/ni-daq-sampling-control/references/ni-daq-channel-inventory.md
.agents/skills/ni-daq-sampling-control/references/ni-daq-sampling-options.md
```

## 输出和运行状态

- TDMS 输出：`data/*.tdms`
- 采样日志：`data/sampling.log`
- 本地采样状态：`.sampling_state.json`
- 本地停止信号：`.sampling_stop`
- 网页对话：`web_console/runtime/conversations/`
- 网页上传配置：`web_console/runtime/uploads/`
- 网页配置备份：`web_console/runtime/backups/`
- 网页未确认草稿：`web_console/runtime/config_drafts/`

