# NI DAQ 可选采样配置参考

本文档记录测控主机上各 NI 设备端口可支持的采样相关配置，用作后续扩展 `sampling_config.docx`、`sampling-config-format.md`、配置校验逻辑和采样程序的依据。

- 测控主机：`admin@192.168.1.103`
- 探测日期：2026-05-20 UTC
- NI-DAQmx 驱动版本：`26.0.0`
- 探测方式：读取 NI-DAQmx `System.local()` 设备属性，并使用 `TaskMode.TASK_VERIFY` 做轻量配置验证
- 探测范围：只验证配置能力，没有启动采样任务，没有写入远端项目文件

当前采样 runner 只实现了模拟输入电压采样。本文档列出的是硬件和 NI-DAQmx 报告的可选能力，不代表当前程序已经全部实现。

## 当前通道清单是否足够

`ni-daq-channel-inventory.md` 只适合回答“有哪些设备、端口、通道”。它不足以作为完整采样配置依据，因为缺少以下信息：

- 模拟输入测量类型
- 模拟输入电压量程
- 模拟输入端子配置
- 模拟输出电压/电流通道划分
- 数字口是否支持静态读写或硬件定时
- 数字口硬件定时是否需要外部采样时钟源
- 有限采样、连续采样、硬件定时单点支持情况
- 最大/最小采样率
- 触发能力
- 计数器输入/输出能力

因此，后续配置扩展应同时参考 `ni-daq-channel-inventory.md` 和本文档。

## 建议抽象出的通用配置项

完整配置不应再只依赖当前的单个 `[acquisition]` 和 `[channels]`，而应能描述多个任务。建议后续支持以下维度。

### 通用任务字段

- `name`：任务名，用于日志和 TDMS 分组
- `type`：任务类型，可选 `ai`、`di`、`do`、`ao`、`ci`、`co`
- `channels`：物理通道、端口、数字线或计数器
- `timing_mode`：`static`、`finite`、`continuous`、`hw_timed_single_point`
- `sample_rate_hz`：硬件定时任务的采样率
- `samples_per_read`：输入任务每次读取样本数
- `samples_per_write`：输出任务每次写入样本数
- `duration_seconds`：有限采样时长
- `sample_clock_source`：采样时钟源，部分数字口硬件定时必须显式配置
- `trigger`：启动、参考、暂停或 arm-start 触发配置

### 数字口字段

- `line_grouping`：`chan_for_all_lines` 或 `chan_per_line`
- `initial_state`：数字输出初始状态
- `read_format`：布尔数组、端口整数值或逐线值

整端口路径，例如 `PXI1Slot3/port0`，只能使用 `chan_for_all_lines`。如果要逐线通道，应配置具体线名，例如 `PXI1Slot3/port0/line0`。

### 模拟输入字段

- `measurement`：至少应支持 `voltage`
- `terminal_config`：`default` 或设备支持的显式端子模式
- `voltage_min`、`voltage_max`
- `coupling`
- `custom_scale`

### 模拟输出字段

- `output_type`：`voltage` 或 `current`
- `value`：静态输出值
- `voltage_min`、`voltage_max`
- `current_min`、`current_max`

### 计数器字段

- `counter_mode`：计数器测量或输出类型
- `edge`
- `initial_count`
- `count_direction`
- `frequency`
- `duty_cycle`
- `high_time`、`low_time`
- `high_ticks`、`low_ticks`
- `source_terminal`
- `gate_terminal`
- `arm_start_trigger`
- `sample_clock_source`

## PXI1Slot3：PXIe-6612

定位：数字 I/O 和计数器板卡。没有模拟输入和模拟输出通道。

### 数字输入 DI

- 端口：
  - `PXI1Slot3/port0`，32 条线，`line0` 到 `line31`
  - `PXI1Slot3/port1`，8 条线，`line0` 到 `line7`
- 数字线：
  - `PXI1Slot3/port0/line0` 到 `PXI1Slot3/port0/line31`
  - `PXI1Slot3/port1/line0` 到 `PXI1Slot3/port1/line7`
- 静态读取：支持
- 硬件定时读取：支持
- 最大 DI 速率：`10000000.0 Hz`
- 触发能力：`pause`、`reference`、`start`
- 线分组验证结果：
  - 整端口 + `chan_for_all_lines`：支持
  - 整端口 + `chan_per_line`：不支持
  - 单条线 + `chan_for_all_lines`：支持
  - 单条线 + `chan_per_line`：支持

### 数字输出 DO

- 端口和数字线同 DI。
- 静态写入：支持
- 硬件定时写入：支持
- 最大 DO 速率：`10000000.0 Hz`
- 触发能力：`pause`、`start`
- 线分组验证结果：
  - 整端口 + `chan_for_all_lines`：支持
  - 整端口 + `chan_per_line`：不支持
  - 单条线 + `chan_for_all_lines`：支持
  - 单条线 + `chan_per_line`：支持

### 计数器输入 CI

- 通道：`PXI1Slot3/ctr0` 到 `PXI1Slot3/ctr7`
- 计数器位宽：32 bit
- 最大 timebase：`100000000.0 Hz`
- 支持采样时钟：是
- 采样模式：`finite`、`continuous`、`hw_timed_single_point`
- 触发能力：`pause`、`start`、`arm_start`
- NI-DAQmx 报告的测量类型：
  - `count_edges`
  - `frequency`
  - `period`
  - `pulse_width_digital_two_edge_separation`
  - `pulse_width_digital_semi_period`
  - `pulse_width_digital`
  - `position_angular_encoder`
  - `position_linear_encoder`
  - `pulse_freq`
  - `pulse_time`
  - `pulse_ticks`

### 计数器输出 CO

- 通道：
  - `PXI1Slot3/ctr0` 到 `PXI1Slot3/ctr7`
  - `PXI1Slot3/freqout`
- 计数器位宽：32 bit
- 最大 timebase：`100000000.0 Hz`
- 支持采样时钟：是
- 采样模式：`finite`、`continuous`、`hw_timed_single_point`
- 触发能力：`pause`、`start`、`arm_start`
- 输出类型：
  - `pulse_frequency`
  - `pulse_ticks`
  - `pulse_time`

### 常用时钟和路由端子

- PFI：`/PXI1Slot3/PFI0` 到 `/PXI1Slot3/PFI39`
- PXI 触发线：`/PXI1Slot3/PXI_Trig0` 到 `/PXI1Slot3/PXI_Trig7`
- Timebase：`/PXI1Slot3/20MHzTimebase`、`/PXI1Slot3/100MHzTimebase`、`/PXI1Slot3/100kHzTimebase`、`/PXI1Slot3/10MHzRefClock`
- 数字时钟：`/PXI1Slot3/di/SampleClock`、`/PXI1Slot3/do/SampleClock`
- 计数器端子：`/PXI1Slot3/Ctr0Source`、`/PXI1Slot3/Ctr0Gate`、`/PXI1Slot3/Ctr0InternalOutput` 等，`ctr0` 到 `ctr7` 均有对应端子

## PXI1Slot5：PXI-6133

定位：同步模拟输入板卡，同时提供静态或外部时钟驱动的 DIO，以及两个计数器。

### 模拟输入 AI

- 通道：`PXI1Slot5/ai0` 到 `PXI1Slot5/ai7`
- 电压量程：
  - `-1.25` 到 `1.25 V`
  - `-2.5` 到 `2.5 V`
  - `-5.0` 到 `5.0 V`
  - `-10.0` 到 `10.0 V`
- 电压输入端子配置验证结果：
  - `default`：支持
  - `diff`：支持
  - `rse`：不支持
  - `nrse`：不支持
  - `pseudo_diff`：不支持
- 耦合：`DC`
- 采样模式：`finite`、`continuous`
- 最小 AI 采样率：`0.0059604644775390625 Hz`
- 最大单通道 AI 采样率：`2500000.0 Hz`
- 最大多通道 AI 采样率：`2500000.0 Hz`
- 同步采样：支持
- 触发能力：`pause`、`reference`、`start`
- NI-DAQmx 报告的 AI 测量类型：
  - `current`
  - `resistance`
  - `strain_strain_gage`
  - `temperature_rtd`
  - `temperature_thermistor`
  - `temperature_thermocouple`
  - `voltage`
  - `voltage_custom_with_excitation`
  - `position_eddy_current_prox_probe`
  - `rosette_strain_gage`

当前程序只验证并实现了 `voltage`。其它传感器类型应先作为“硬件报告的候选项”记录，真正启用前需要单独实现和验证。

### 数字输入 DI

- 端口：`PXI1Slot5/port0`
- 数字线：`PXI1Slot5/port0/line0` 到 `PXI1Slot5/port0/line7`
- 静态读取：支持
- 硬件定时读取：支持，但必须显式设置 `sample_clock_source`
- 最大 DI 速率：`10000000.0 Hz`
- 触发能力：设备属性未报告 DI trigger usage
- 已验证可用采样时钟源：
  - `/PXI1Slot5/ai/SampleClock`
  - `/PXI1Slot5/PFI0`
  - `/PXI1Slot5/PXI_Trig0`
  - `/PXI1Slot5/Ctr0InternalOutput`
- 线分组验证结果：
  - 整端口 + `chan_for_all_lines`：支持
  - 整端口 + `chan_per_line`：不支持
  - 单条线 + `chan_for_all_lines`：支持
  - 单条线 + `chan_per_line`：支持

### 数字输出 DO

- 端口和数字线同 DI。
- 静态写入：支持
- 硬件定时写入：支持，但必须显式设置 `sample_clock_source`
- 最大 DO 速率：`10000000.0 Hz`
- 触发能力：设备属性未报告 DO trigger usage
- 已验证可用采样时钟源：
  - `/PXI1Slot5/ai/SampleClock`
  - `/PXI1Slot5/PFI0`
  - `/PXI1Slot5/PXI_Trig0`
  - `/PXI1Slot5/Ctr0InternalOutput`
- 线分组验证结果：
  - 整端口 + `chan_for_all_lines`：支持
  - 整端口 + `chan_per_line`：不支持
  - 单条线 + `chan_for_all_lines`：支持
  - 单条线 + `chan_per_line`：支持

### 计数器输入 CI

- 通道：`PXI1Slot5/ctr0`、`PXI1Slot5/ctr1`
- 计数器位宽：24 bit
- 最大 timebase：`20000000.0 Hz`
- 支持采样时钟：是
- 采样模式：`finite`、`continuous`、`hw_timed_single_point`
- 触发能力：`pause`
- 测量类型：
  - `count_edges`
  - `frequency`
  - `period`
  - `pulse_width_digital_semi_period`
  - `pulse_width_digital`

### 计数器输出 CO

- 通道：`PXI1Slot5/ctr0`、`PXI1Slot5/ctr1`、`PXI1Slot5/freqout`
- 计数器位宽：24 bit
- 最大 timebase：`20000000.0 Hz`
- 支持采样时钟：是
- 采样模式：`hw_timed_single_point`
- 触发能力：`pause`、`start`
- 输出类型：
  - `pulse_frequency`
  - `pulse_ticks`
  - `pulse_time`

### 常用时钟和路由端子

- PFI：`/PXI1Slot5/PFI0` 到 `/PXI1Slot5/PFI9`
- PXI 触发线：`/PXI1Slot5/PXI_Trig0` 到 `/PXI1Slot5/PXI_Trig5`，以及 `/PXI1Slot5/PXI_Trig7`
- AI 时钟和触发：`/PXI1Slot5/ai/SampleClock`、`/PXI1Slot5/ai/StartTrigger`、`/PXI1Slot5/ai/ReferenceTrigger`、`/PXI1Slot5/ai/PauseTrigger`、`/PXI1Slot5/ai/SampleClockTimebase`
- DIO 时钟：`/PXI1Slot5/di/SampleClock`、`/PXI1Slot5/do/SampleClock`
- 计数器端子：`/PXI1Slot5/Ctr0Out`、`/PXI1Slot5/Ctr0Gate`、`/PXI1Slot5/Ctr0Source`、`/PXI1Slot5/Ctr0InternalOutput`，`ctr1` 有对应端子

## PXI1Slot6：PXI-6133

PXI1Slot6 和 PXI1Slot5 能力相同，通道和端子名前缀改为 `PXI1Slot6`。

### 模拟输入 AI

- 通道：`PXI1Slot6/ai0` 到 `PXI1Slot6/ai7`
- 电压量程：
  - `-1.25` 到 `1.25 V`
  - `-2.5` 到 `2.5 V`
  - `-5.0` 到 `5.0 V`
  - `-10.0` 到 `10.0 V`
- 电压输入端子配置验证结果：
  - `default`：支持
  - `diff`：支持
  - `rse`：不支持
  - `nrse`：不支持
  - `pseudo_diff`：不支持
- 耦合：`DC`
- 采样模式：`finite`、`continuous`
- 最小 AI 采样率：`0.0059604644775390625 Hz`
- 最大单通道 AI 采样率：`2500000.0 Hz`
- 最大多通道 AI 采样率：`2500000.0 Hz`
- 同步采样：支持
- 触发能力：`pause`、`reference`、`start`
- NI-DAQmx 报告的 AI 测量类型：
  - `current`
  - `resistance`
  - `strain_strain_gage`
  - `temperature_rtd`
  - `temperature_thermistor`
  - `temperature_thermocouple`
  - `voltage`
  - `voltage_custom_with_excitation`
  - `position_eddy_current_prox_probe`
  - `rosette_strain_gage`

### 数字输入 DI

- 端口：`PXI1Slot6/port0`
- 数字线：`PXI1Slot6/port0/line0` 到 `PXI1Slot6/port0/line7`
- 静态读取：支持
- 硬件定时读取：支持，但必须显式设置 `sample_clock_source`
- 最大 DI 速率：`10000000.0 Hz`
- 已验证可用采样时钟源：
  - `/PXI1Slot6/ai/SampleClock`
  - `/PXI1Slot6/PFI0`
  - `/PXI1Slot6/PXI_Trig0`
  - `/PXI1Slot6/Ctr0InternalOutput`

### 数字输出 DO

- 端口和数字线同 DI。
- 静态写入：支持
- 硬件定时写入：支持，但必须显式设置 `sample_clock_source`
- 最大 DO 速率：`10000000.0 Hz`
- 已验证可用采样时钟源：
  - `/PXI1Slot6/ai/SampleClock`
  - `/PXI1Slot6/PFI0`
  - `/PXI1Slot6/PXI_Trig0`
  - `/PXI1Slot6/Ctr0InternalOutput`

### 计数器输入 CI

- 通道：`PXI1Slot6/ctr0`、`PXI1Slot6/ctr1`
- 计数器位宽：24 bit
- 最大 timebase：`20000000.0 Hz`
- 支持采样时钟：是
- 采样模式：`finite`、`continuous`、`hw_timed_single_point`
- 触发能力：`pause`
- 测量类型：
  - `count_edges`
  - `frequency`
  - `period`
  - `pulse_width_digital_semi_period`
  - `pulse_width_digital`

### 计数器输出 CO

- 通道：`PXI1Slot6/ctr0`、`PXI1Slot6/ctr1`、`PXI1Slot6/freqout`
- 计数器位宽：24 bit
- 最大 timebase：`20000000.0 Hz`
- 支持采样时钟：是
- 采样模式：`hw_timed_single_point`
- 触发能力：`pause`、`start`
- 输出类型：
  - `pulse_frequency`
  - `pulse_ticks`
  - `pulse_time`

## PXI1Slot8：PXI-6704

定位：静态模拟输出板卡，同时提供静态数字 I/O。没有 AI 和计数器。

### 模拟输出 AO

- AO 采样时钟：不支持
- AO 采样模式：设备未报告 finite 或 continuous
- 触发能力：无
- 电压输出范围：`-10.24` 到 `10.24 V`
- 电流输出范围：`0.0` 到 `0.0204 A`
- 电压输出通道：
  - `PXI1Slot8/ao0` 到 `PXI1Slot8/ao15`
- 电流输出通道：
  - `PXI1Slot8/ao16` 到 `PXI1Slot8/ao31`
- 验证结果：
  - `ao0` 到 `ao15` 支持电压输出，不支持电流输出
  - `ao16` 到 `ao31` 支持电流输出，不支持电压输出

PXI1Slot8 的 AO 应配置为 `static` 或 on-demand 输出，不应配置为有限或连续波形输出。

### 数字输入 DI

- 端口：`PXI1Slot8/port0`
- 数字线：`PXI1Slot8/port0/line0` 到 `PXI1Slot8/port0/line7`
- 静态读取：支持
- 硬件定时读取：不支持
- 触发能力：无
- 线分组验证结果：
  - 整端口 + `chan_for_all_lines`：支持
  - 整端口 + `chan_per_line`：不支持
  - 单条线 + `chan_for_all_lines`：支持
  - 单条线 + `chan_per_line`：支持

### 数字输出 DO

- 端口和数字线同 DI。
- 静态写入：支持
- 硬件定时写入：不支持
- 触发能力：无
- 线分组验证结果：
  - 整端口 + `chan_for_all_lines`：支持
  - 整端口 + `chan_per_line`：不支持
  - 单条线 + `chan_for_all_lines`：支持
  - 单条线 + `chan_per_line`：支持

## 当前 TOML 任务结构

`sampling_config.docx` 现在使用 `schema_version = 2` 和 `[[tasks]]` 描述任务，不再使用旧的 `[acquisition]` 与 `[channels]` 结构。每个 `[[tasks]]` 对应一个 NI-DAQmx 任务。

示例：

```toml
[[tasks]]
name = "ai_slot5"
enabled = true
type = "ai"
measurement = "voltage"
channels = ["PXI1Slot5/ai0"]
timing_mode = "continuous"
sample_rate_hz = 200
samples_per_read = 200
terminal_config = "diff"
voltage_min = -10.0
voltage_max = 10.0

[[tasks]]
name = "di_slot3"
enabled = true
type = "di"
channels = ["PXI1Slot3/port0"]
line_grouping = "chan_for_all_lines"
timing_mode = "finite"
sample_rate_hz = 1000
samples_per_read = 100

[[tasks]]
name = "ao_slot8_voltage"
enabled = true
type = "ao"
output_type = "voltage"
channels = ["PXI1Slot8/ao0"]
timing_mode = "static"
value = 0.0
voltage_min = -10.24
voltage_max = 10.24

[[tasks]]
name = "counter_slot3"
enabled = true
type = "ci"
counter_mode = "count_edges"
channels = ["PXI1Slot3/ctr0"]
timing_mode = "continuous"
sample_clock_source = "/PXI1Slot3/di/SampleClock"
```

## 实现约束

- 在 runner 真正支持某类任务前，不应只放宽配置校验。
- 配置校验应同时检查通道名和能力约束。
- PXI-6133 AI 的 `terminal_config` 只能允许 `default` 或 `diff`。
- PXI-6133 AI 电压量程只能允许 ±1.25 V、±2.5 V、±5 V、±10 V。
- PXI-6133 DIO 使用硬件定时时必须配置 `sample_clock_source`。
- PXIe-6612 DIO 支持硬件定时。
- PXI-6704 AO 只能作为静态输出。
- PXI-6704 的 `ao0` 到 `ao15` 是电压输出，`ao16` 到 `ao31` 是电流输出。
- 整数字端口必须使用 `chan_for_all_lines`。
