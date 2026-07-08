# NI DAQ Sampling Configuration

本文件是 NI DAQ 采样与输出任务的操作员配置入口。

使用方法：

1. 只修改下面 `sampling-config` TOML 代码块中的配置。
2. 一个 `[[tasks]]` 表示一个 NI-DAQmx 任务。
3. 日常操作时，只把确实要运行的任务设置为 `enabled = true`。
4. 复制本文档后面的模板时，先改 `name`、`channels` 和采样/输出参数，再运行配置校验。
5. 开始或停止采样前，仍需要在对话中明确确认。

```toml sampling-config
schema_version = 2

[host]
# ssh: 本机通过 SSH 控制远端测量主机；local: 直接在测量主机运行。
mode = "ssh"
ssh_host = "192.168.1.103"
ssh_port = 22
ssh_user = "admin"
remote_project_dir = "/home/admin/project1"
python = "python3"

[tdms]
# {timestamp} 会自动替换为启动采样时的时间戳。
output_dir = "data"
filename = "ni_daq_sample_{timestamp}.tdms"
allow_overwrite = false

[control]
state_file = ".sampling_state.json"
stop_file = ".sampling_stop"
log_file = "data/sampling.log"
startup_check_seconds = 1.0
stop_timeout_seconds = 15.0

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
## 配置原则

- `[[tasks]]` 是最小执行单元。不要把 AI、DI、AO、计数器混在同一个任务里。
- `name` 必须唯一，建议只使用英文、数字和下划线。
- 输入任务建议设置 `tdms_group`，用于写入 TDMS 文件中的分组名。
- `enabled = true` 的任务会被执行；模板和备用任务应保持 `enabled = false` 或不要放入有效 TOML 配置块。
- `channels` 必须使用硬件清单中的完整通道名。
- 当前硬件清单见 `.agents/skills/ni-daq-sampling-control/references/ni-daq-channel-inventory.md`。
- 扩展能力和限制见 `.agents/skills/ni-daq-sampling-control/references/ni-daq-sampling-options.md`。

## 字段速查

### 通用字段

| 字段 | 说明 |
| --- | --- |
| `name` | 任务唯一名，用于日志、状态和 TDMS group。 |
| `enabled` | 是否执行该任务。 |
| `type` | 任务类型：`ai`、`di`、`do`、`ao`、`ci`、`co`。 |
| `channels` | 物理通道、端口、数字线或计数器通道。 |
| `tdms_group` | 输入数据写入 TDMS 时使用的 group 名。 |

### 时序字段

| 字段 | 说明 |
| --- | --- |
| `timing_mode` | `static`、`finite`、`continuous` 或 `hw_timed_single_point`。 |
| `sample_rate_hz` | 硬件定时任务的采样率。 |
| `samples_per_read` | 输入任务每次读取样本数。 |
| `samples_per_write` | 输出任务每次写入样本数。 |
| `duration_seconds` | `finite` 任务的采样时长。 |
| `sample_clock_source` | 外部或共享采样时钟源，例如 `/PXI1Slot5/ai/SampleClock`。 |

### 触发字段

触发配置写在对应任务下面。不是所有设备都支持所有触发类型，配置后必须校验。

```toml
[[tasks.triggers]]
kind = "start"
source = "/PXI1Slot5/PFI0"
edge = "rising"
```

可用触发类型建议限定为：

- `start`
- `reference`
- `pause`
- `arm_start`

## 常用任务模板

下面模板默认不属于有效配置。需要使用时，复制到上面的 `sampling-config` TOML 代码块中，并按现场接线修改字段。

### 模拟输入电压采样

适用于 `PXI1Slot5/ai0` 到 `PXI1Slot5/ai7`，以及 `PXI1Slot6/ai0` 到 `PXI1Slot6/ai7`。

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

注意：

- `terminal_config` 建议使用 `default` 或 `diff`。
- 电压范围应使用 `-1.25` 到 `1.25`、`-2.5` 到 `2.5`、`-5.0` 到 `5.0`、`-10.0` 到 `10.0`。
- `finite` 模式必须设置 `duration_seconds > 0`。

### 数字输入端口采样

适用于数字输入端口或数字线。

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

注意：

- 整端口路径如 `PXI1Slot3/port0` 必须使用 `line_grouping = "chan_for_all_lines"`。
- 单线路径如 `PXI1Slot3/port0/line0` 可以按需要使用 `chan_for_all_lines` 或 `chan_per_line`。
- `PXI1Slot5` 和 `PXI1Slot6` 的 DI 使用硬件定时时，必须设置 `sample_clock_source`。
- `PXI1Slot8` 的 DI 只适合 `static`。

### 数字输出静态写入

适用于设置数字端口或数字线的静态状态。

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

注意：

- `initial_state = 0` 表示所有线为低电平。
- 整端口路径必须使用 `chan_for_all_lines`。
- `PXI1Slot8` 的 DO 只适合 `static`。

### 模拟输出电压

适用于 `PXI1Slot8/ao0` 到 `PXI1Slot8/ao15`。

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

注意：

- `PXI1Slot8` 的模拟输出是静态输出，不应配置为 `finite` 或 `continuous`。
- `ao0` 到 `ao15` 是电压输出通道。

### 模拟输出电流

适用于 `PXI1Slot8/ao16` 到 `PXI1Slot8/ao31`。

```toml
[[tasks]]
name = "ao_slot8_current_16"
enabled = true
type = "ao"
channels = ["PXI1Slot8/ao16"]

output_type = "current"
value = 0.004
current_min = 0.0
current_max = 0.0204

timing_mode = "static"
```

注意：

- `value = 0.004` 表示 4 mA。
- `ao16` 到 `ao31` 是电流输出通道。

### 计数器输入：边沿计数

适用于 `PXI1Slot3/ctr0` 到 `PXI1Slot3/ctr7`，以及 `PXI1Slot5/ctr0`、`PXI1Slot5/ctr1`、`PXI1Slot6/ctr0`、`PXI1Slot6/ctr1`。

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

注意：

- `source_terminal` 应按实际接线修改。
- 使用采样时钟读取计数值时，必须设置可用的 `sample_clock_source`。

### 计数器输入：频率测量

```toml
[[tasks]]
name = "ci_slot3_frequency"
enabled = true
type = "ci"
channels = ["PXI1Slot3/ctr0"]
tdms_group = "ci_slot3_frequency"

counter_mode = "frequency"
edge = "rising"
source_terminal = "/PXI1Slot3/PFI0"

timing_mode = "continuous"
sample_rate_hz = 1000
samples_per_read = 100
sample_clock_source = "/PXI1Slot3/di/SampleClock"
```

### 计数器输出：脉冲频率

适用于输出连续脉冲。

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

### 计数器输出：脉冲时间

```toml
[[tasks]]
name = "co_slot3_pulse_time"
enabled = true
type = "co"
channels = ["PXI1Slot3/ctr0"]

counter_mode = "pulse_time"
high_time = 0.001
low_time = 0.001

timing_mode = "continuous"
```

### 计数器输出：脉冲 ticks

```toml
[[tasks]]
name = "co_slot3_pulse_ticks"
enabled = true
type = "co"
channels = ["PXI1Slot3/ctr0"]

counter_mode = "pulse_ticks"
high_ticks = 100
low_ticks = 100
source_terminal = "/PXI1Slot3/100kHzTimebase"

timing_mode = "continuous"
```

##原来的默认配置
```
# 当前默认任务：PXI1Slot5 的模拟电压输入。
name = "ai_slot5_voltage"
enabled = true
type = "ai"
channels = ["PXI1Slot5/ai0"]
tdms_group = "ai_slot5_voltage"

# 当前 runner 优先支持 voltage。其他传感器类型需要脚本实现后再启用。
measurement = "voltage"

# PXI-6133 建议使用 default 或 diff。
terminal_config = "default"

# PXI-6133 支持 ±1.25 V、±2.5 V、±5 V、±10 V。
voltage_min = -10.0
voltage_max = 10.0
coupling = "DC"
custom_scale = ""

# timing_mode:
# - continuous: 持续采样，直到停止。
# - finite: 采样 duration_seconds 秒后结束。
# - static: 静态读写，不使用采样时钟。
# - hw_timed_single_point: 硬件定时单点，主要用于支持该模式的计数器任务。
timing_mode = "continuous"
sample_rate_hz = 200
samples_per_read = 200
duration_seconds = 0.0
sample_clock_source = ""
```

## 常见限制

- `PXI1Slot5` 和 `PXI1Slot6` 是 PXI-6133，AI 通道为 `ai0` 到 `ai7`。
- PXI-6133 AI 的 `terminal_config` 建议只使用 `default` 或 `diff`。
- PXI-6133 AI 的电压量程应使用 ±1.25 V、±2.5 V、±5 V 或 ±10 V。
- `PXI1Slot3` 是 PXIe-6612，适合数字 I/O 和计数器任务。
- `PXI1Slot8` 是 PXI-6704，适合静态 AO 和静态 DIO。
- `PXI1Slot8/ao0` 到 `ao15` 是电压输出，`ao16` 到 `ao31` 是电流输出。
- 整数字端口必须使用 `line_grouping = "chan_for_all_lines"`。
- 新任务类型只有在 runner 和控制脚本实现后才能真正执行。
