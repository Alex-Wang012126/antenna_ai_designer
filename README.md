# Antenna AI Designer

这是一个由 LLM 提出候选参数、由可信 Python 代码驱动 Ansys HFSS、再由独立评测器裁决结果的迭代式天线设计项目。

当前版本支持单端口、嵌入式微带馈电的矩形贴片天线。LLM 不能提交 Python、PyAEDT 脚本或文件路径；几何、端口、边界、求解设置、扫频和结果读取都由项目内固定代码执行。

## 架构与流程

```text
用户需求
  -> LLM 提交一组完整候选参数
  -> Python: 参数校验 -> 建模 -> AEDT 校验 -> 求解 -> 读数 -> 保存
  -> LLM 查看该候选的完整实测结果
  -> LLM 决定修改参数，或调用 finalize_design 提前结束
  -> AntennaEvaluator 独立进行最终裁决
```

模型只会看到两个工具：

- `create_patch_antenna({...})`：提交一个完整候选。一次调用会由 Python 自动完成整条仿真流水线。
- `finalize_design({"summary": "..."})`：由模型主动结束候选生成。至少有一个候选完整仿真成功后才允许调用。

模型不参与 `validate_design`、`solve`、`get_result` 或 `export_design` 的调度。这些步骤不会再消耗模型轮次，也不会因模型调用上限而把最后一个候选停在未求解状态。

`--max-design-iterations` 表示允许提交并完整处理的候选数量，而不是模型调用次数。`--max-rounds` 仍可作为兼容别名。模型可以在认为当前候选已满足要求时提前调用 `finalize_design`；Python 不会根据指标替模型提前停止。达到迭代上限时，生成阶段自动结束，且不会额外补做一个隐藏候选或隐藏求解。

## 候选输入格式

`create_patch_antenna` 接收严格 JSON object。字段必须全部出现，不能包含额外字段。所有尺寸和频率均为 JSON number，单位由字段名固定，不允许传入 `"3.175mm"` 之类的字符串。

```json
{
  "center_frequency_ghz": 2.45,
  "substrate_material": "Rogers5880_custom",
  "substrate_permittivity": 2.2,
  "substrate_loss_tangent": 0.0009,
  "substrate_width_mm": 100.0,
  "substrate_length_mm": 100.0,
  "substrate_height_mm": 3.175,
  "patch_width_mm": 48.5,
  "patch_length_mm": 39.0,
  "feed_width_mm": 9.6,
  "inset_depth_mm": 13.5,
  "inset_gap_mm": 1.0,
  "air_margin_xy_mm": 40.0,
  "air_above_mm": 55.0,
  "air_below_mm": 25.0,
  "sweep_start_ghz": 2.2,
  "sweep_stop_ghz": 2.7,
  "sweep_points": 251
}
```

代码还会检查数值有限性、尺寸范围、槽深与槽隙、贴片和基板的相对尺寸、馈线可容纳性，以及中心频率是否严格位于扫频范围内。

`substrate_material` 是说明性标签。后端在工程内维护专用材料 `AntennaSubstrate`，以 `substrate_permittivity` 和 `substrate_loss_tangent` 为实际属性，不改写 AEDT 内置材料。

## 候选结果格式

每次模型工具调用都会收到协议版本 2 的结果。候选成功时，`data.candidate` 中包含每个 Python 阶段的结果、实测指标和落盘路径：

```json
{
  "protocol_version": 2,
  "tool": "create_patch_antenna",
  "success": true,
  "data": {
    "candidate": {
      "iteration": 1,
      "status": "completed",
      "success": true,
      "project_file": ".../candidate_001.aedt",
      "metrics_file": ".../candidate_001_metrics.json",
      "metrics": {
        "center_freq_ghz": 2.45,
        "s11_min_db": -21.0,
        "bandwidth_mhz": 120.0,
        "peak_gain_dbi": 5.2,
        "radiation_efficiency_percent": 92.0
      },
      "stages": {
        "build": {"success": true},
        "validation": {"success": true},
        "solve": {"success": true},
        "metrics": {"success": true},
        "save": {"success": true}
      }
    }
  },
  "iterations_used": 1,
  "remaining_design_iterations": 4,
  "remaining_solve_calls": 4,
  "latest_successful_iteration": 1
}
```

任一阶段失败都会记录为该候选的最终状态，例如 `build_failed`、`validation_failed` 或 `solve_failed`。真实 AEDT 消息窗口中的近期错误也会附在建模或求解错误中，便于定位端口、网格和求解器问题。

## 最终评测与回退

候选生成结束后，`AntennaEvaluator` 独立读取 `run_manifest.json`，不会要求 LLM 自我判分。它从最新候选向前检查：

1. 跳过未完成完整流水线的候选。
2. 对完整候选按需求阈值评测。
3. 选择第一个通过评测的候选。
4. 若全部未通过，报告最新的完整候选及失败项，程序返回失败。

因此，最后一次修改即使建模或求解失败，或者实测性能变差，也不会覆盖此前保存的成功工程。

默认需求是中心频率 2.45 GHz、S11 不高于 -10 dB、-10 dB 带宽不小于 100 MHz、峰值增益不小于 3 dBi。评测器检查：

- 谐振频率与目标频率偏差不超过 0.05 GHz；需求未给目标频率时跳过。
- `s11_min_db` 不高于需求阈值；需求未给时默认 -10 dB。
- `bandwidth_mhz` 不低于需求阈值；需求未给时默认 50 MHz。
- `peak_gain_dbi` 不低于需求阈值；需求未给时默认 0 dBi。

`radiation_efficiency_percent` 会读取并报告，但当前不参与通过判定。

## 文件组织

每次运行 `main.py` 都会创建一个独立批次目录。该次运行产生的工程副本、求解目录、指标、对话和评测报告都保存在其中：

```text
hfss_projects/
  run_20260820_153000_123456/
    candidate_001.aedt
    candidate_001.aedtresults/
    candidate_001_metrics.json
    candidate_002.aedt
    candidate_002.aedtresults/
    candidate_002_metrics.json
    design_log.json
    run_manifest.json
    evaluation_report.json
```

参数格式校验即失败的候选不会创建 AEDT 文件，但仍会记录在 `run_manifest.json`。清单中的 `project_file` 是候选与实际工程副本之间的权威映射。

## 安装与配置

建议使用 Python 3.11：

```powershell
python -m pip install -r requirements.txt
```

可在 `.env` 中配置：

```dotenv
MODEL_API_KEY=your-key
MODEL_BASE_URL=https://api.apevon.ai/v1
MODEL_NAME=gpt-5.5
MAX_DESIGN_ITERATIONS=5
MAX_SOLVE_CALLS=5
AEDT_ROOT=C:\ANSYS Inc\ANSYS Student\v252\AnsysEM
AEDT_KEEP_OPEN=true
```

API key 也可以保留在 `job_gpt55.yaml` 的 `agents[0].kwargs.api_key` 中。配置优先级为环境变量或 `.env` 高于 YAML，再高于代码默认值。

`AEDT_KEEP_OPEN` 默认为 `true`。无论成功还是失败，程序都会保存当前项目并仅释放 Python 自动化连接，AEDT 项目和窗口保持打开供检查；设置为 `false` 才会关闭项目和桌面。

## 运行

先验证 AEDT Student 2025 R2 连接：

```powershell
python verify_min.py
```

运行真实设计。通常让求解预算不低于候选迭代上限：

```powershell
python main.py --requirements "设计一个中心频率 2.45 GHz 的微带贴片天线，S11 < -10 dB，-10 dB 带宽 > 100 MHz，峰值增益 > 3 dBi。" --max-design-iterations 10 --max-solve-calls 10
```

无需模型和 AEDT 的离线演示：

```powershell
python main.py --use-placeholder
```

运行离线测试：

```powershell
python -B -m unittest discover -s tests -v
```

## 主要文件

- `design_spec.py`：严格参数模型及 JSON Schema 字段定义
- `prompts.py`：LLM 行为协议和两个工具的 schema
- `model_client.py`：Responses API / Chat Completions API 适配
- `design_loop.py`：候选生成循环与 Python 完整仿真流水线
- `hfss_client.py`：可信 PyAEDT 建模、校验、求解、读数和工程副本管理
- `evaluator.py`：独立阈值评测与候选回退
- `main.py`：批次目录创建和两阶段流程入口

真实 AEDT 集成依赖本机许可证、安装版本和求解环境。离线测试不会启动 AEDT；修改建模代码后，应先运行离线测试，再用 `verify_min.py` 和一次低迭代真实任务验证。
