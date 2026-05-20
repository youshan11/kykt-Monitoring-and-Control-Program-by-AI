# NI DAQ Sampling Configuration

Edit this document before sending `开始采样` or `停止采样`.

The fenced TOML block below is read by `.agents/skills/ni-daq-sampling-control/scripts/ni_daq_sampling_control.py`.

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
sample_rate_hz = 200
samples_per_read = 200
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

## Notes

- 当前默认模拟输入通道使用 `PXI1Slot5/ai0`。
- 可用板卡与通道见 `.agents/skills/ni-daq-sampling-control/references/ni-daq-channel-inventory.md`。
- 当前配置为 SSH 编排模式：本机控制脚本连接 `admin@192.168.1.103`，在 `/home/admin/project1` 运行采样，停止后将 TDMS 自动回传到本机 `data/`。
