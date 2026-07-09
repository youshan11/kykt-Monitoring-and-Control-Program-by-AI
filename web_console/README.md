# Project1 Web Console

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
- 快捷按钮也是发送自然语言：
  - 开始执行：`开始采样`
  - 确认启动：`确认启动`
  - 停止执行：`停止采样`
  - 确认停止：`确认停止`
  - 查询状态：`查看当前采样状态`
- TDMS 文件从 `data/*.tdms` 列表中下载。
- 采集日志读取 `data/sampling.log`。

## 环境变量

```bash
WEB_CONSOLE_HOST=127.0.0.1
WEB_CONSOLE_PORT=8765
WEB_CONSOLE_CODEX_BIN=codex
WEB_CONSOLE_CODEX_TIMEOUT_SECONDS=600
WEB_CONSOLE_CODEX_EXTRA_ARGS="-s workspace-write"
```

如果实际硬件控制或 SSH 在 Codex 沙箱内不可用，可在受控内网环境下显式改为：

```bash
WEB_CONSOLE_CODEX_EXTRA_ARGS="--dangerously-bypass-approvals-and-sandbox"
```

这会让 agent 以更高权限执行项目命令，只应在可信本机服务中使用。
