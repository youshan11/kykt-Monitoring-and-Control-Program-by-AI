# Project1 Web Console
# ssh -L 8765:127.0.0.1:8765 buaa-dev

本目录是 `project1` 的本地 Web 外壳。网页只负责上传配置、发送自然语言消息、展示 agent 回复、读取日志和下载 TDMS；NI DAQ 核心逻辑仍由项目根目录的 `AGENTS.md`、skill 和既有脚本负责。

## 启动

```bash
cd /home/kangjs/workspace/project1
python3 -m web_console.backend.app
```

默认地址：

```text
http://127.0.0.1:8765
```

## 行为

- 上传 `.docx` 会备份并替换 `sampling_config.docx`。
- 文本框输入会发送给服务器上的 Codex agent。
- 快捷按钮直接调用后端采样控制接口，不经过 agent：
  - 开始执行：准备启动确认，校验配置并检查当前状态。
  - 确认启动：执行 `ni_daq_sampling_control.py start --confirm-start`。
  - 停止执行：准备停止确认，检查当前状态。
  - 确认停止：执行 `ni_daq_sampling_control.py stop --confirm-stop`。
  - 查询状态：执行 `ni_daq_sampling_control.py status`。
- 文本框里的明确采样控制短语与快捷按钮等效，可交叉使用；其它自然语言仍按原方式交给 Codex agent 处理。
- TDMS 文件从 `data/*.tdms` 列表中下载。
- 对话记录保存在 `web_console/runtime/conversations/`，支持新建、切换、重命名和删除。

## 环境变量

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

每个网页对话会在 `meta.json` 中保存 `codex_session_id`，后续消息会优先使用 `codex exec resume` 复用该 Codex 会话。`WEB_CONSOLE_CODEX_HISTORY_LIMIT` 控制首次创建 Codex 会话时附带的最近网页历史条数。

默认配置会让网页端 Codex 以 `--dangerously-bypass-approvals-and-sandbox` 运行。原因是本地 DOCX 修改和硬件/SSH 控制需要稳定的文件与进程访问，而部分主机上的 bwrap 沙箱会在 DOCX 写入/解包时失败。

这会让 agent 以更高权限执行项目命令，只应在可信本机服务中使用。如果需要重新启用沙箱，可显式设置：

```bash
WEB_CONSOLE_CODEX_EXTRA_ARGS="-s workspace-write"
WEB_CONSOLE_CODEX_RESUME_EXTRA_ARGS="-s workspace-write"
```
