# Antenna AI Designer

这是一个由结构化任务合同定义题目、由 LLM 提出候选参数、由可信 Python 代码驱动 Ansys HFSS、再由独立评测器裁决结果的迭代式天线设计项目。

当前版本支持单端口、嵌入式微带馈电的矩形贴片天线。LLM 不能提交 Python、PyAEDT 脚本或文件路径；几何、端口、边界、求解设置、扫频和结果读取都由项目内固定代码执行。

## 架构与流程

```text
结构化 task_spec.json（权威）+ 自然语言说明（补充）
  -> LLM 提交一组完整候选参数
  -> Python: 参数校验 -> 建模 -> AEDT 校验 -> 求解 -> 读数 -> 保存
  -> LLM 查看该候选的完整实测结果
  -> LLM 决定修改参数，或调用 finalize_design 提前结束
  -> AntennaEvaluator 独立进行最终裁决
```

`tasks/inset_patch_2p45.json` 同时定义拓扑、只读固定参数、模型可调变量、HFSS 仿真控制、指标单位/语义、阈值和分值。自然语言不会被反向解析为阈值。

模型只会看到两个工具：

- `create_patch_antenna({...})`：提交一个完整候选。一次调用会由 Python 自动完成整条仿真流水线。
- `finalize_design({"summary": "..."})`：由模型主动结束候选生成。至少有一个候选完整仿真成功后才允许调用。

模型不参与 `validate_design`、`solve`、`get_result` 或 `export_design` 的调度。这些步骤不会再消耗模型轮次，也不会因模型调用上限而把最后一个候选停在未求解状态。

`--max-design-iterations` 表示允许提交并完整处理的候选数量，而不是模型调用次数。`--max-rounds` 仍可作为兼容别名。模型可以在认为当前候选已满足要求时提前调用 `finalize_design`；Python 不会根据指标替模型提前停止。达到迭代上限时，生成阶段自动结束，且不会额外补做一个隐藏候选或隐藏求解。

## 满分解校准

“存在满分解”不能只靠合理的目标值推断，必须由见证解验证。推荐的出题流程是：先冻结任务合同和 HFSS 版本/设置，再由人工、传统优化器或更大的离线求解预算找到至少一个候选；用与正式评测完全相同的 builder、求解和读数链路独立复算；最后把满分阈值放在见证解性能以内，并预留大于网格与数值波动的余量。参考设计用于证明可达性，不用于比较模型参数是否接近“标准答案”，也不应暴露给参评模型。

任务中的 `calibration` 明确记录校准状态。只有在同一固定环境中验证过满分见证解后，才能设为 `verified`；否则 `main.py` 会显示警告。当前 `inset_patch_2p45_v6` 仍是 `uncalibrated`，适合继续调题和收集结果，尚不适合直接作为正式排行榜题目。

## 固定量、约束与设计变量

- 固定评测环境：HFSS Setup、扫频、场数据保存、端口归一化、空气区域、边界、远场球、材料数值属性和资源预算。它们由评测端控制，模型只能读取。
- 题目约束：允许的拓扑、端口数、制造/尺寸边界和材料选择范围。它们定义模型可以搜索的设计空间。
- 设计变量：真正用于考察设计能力的尺寸和拓扑选择，例如贴片长宽、基板尺寸、馈线宽度、内嵌深度和槽隙。

当前题目固定为“居中内嵌微带馈电”，但并没有固定馈点深度：`feed_width_mm`、`inset_depth_mm` 和 `inset_gap_mm` 都由模型选择。横向偏置和馈电类型尚未开放。若要比较馈电方案，建议先分别实现并验证 `edge_microstrip`、`inset_microstrip`、`coax_probe` 等受信任 builder，再在混合题中开放离散 `feed_type`；不要直接允许模型提交任意 HFSS 几何或脚本。

## 扩展其他天线拓扑

当前后端只注册了内嵌馈电矩形贴片。增加一种可公平评测的新天线，至少需要同步增加：

1. 拓扑 ID、模型可调参数 schema 和几何约束。
2. 只接受结构化参数的可信 PyAEDT builder。
3. 端口、边界、材料、网格和求解设置校验。
4. 与题型匹配的指标读取器，例如圆极化的轴比、MIMO 的 S 参数矩阵/隔离度/ECC。
5. 带明确单位、数值表示、测量频率/方向和评分方式的任务模板。
6. 对应的离线协议测试与少量真实 AEDT 基准工程。

这样可以区分“模型不会设计”和“当前工具根本无法表达该设计”。

## 候选输入格式

`create_patch_antenna` 接收严格 JSON object。字段必须全部出现，不能包含额外字段。模型只提交八个物理几何量，单位均为 mm，由任务 schema 固定，不允许传入 `"5mm"` 之类的字符串。

```json
{
  "substrate_width_mm": 100.0,
  "substrate_length_mm": 85.0,
  "substrate_height_mm": 5.0,
  "patch_width_mm": 58.0,
  "patch_length_mm": 38.4,
  "feed_width_mm": 12.0,
  "inset_depth_mm": 10.5,
  "inset_gap_mm": 1.4
}
```

代码还会按任务中的变量边界检查数值有限性、槽深与槽隙、贴片和基板的相对尺寸及馈线可容纳性。

材料、介电常数、损耗角、端口阻抗、空气盒、Setup、扫频和远场球都位于 `simulation_control` / `fixed_parameters`，模型可读取但不能通过工具修改。后端在工程内维护专用材料 `AntennaSubstrate`，不改写 AEDT 内置材料。所有候选复用同一个 AEDT 工程窗口；每次先将当前工程另存为独立的 `candidate_NNN.aedt`，再在原 design 内替换几何、端口、边界和远场设置。固定的 `Setup1/Sweep1/EfficiencySweep` 在候选之间保留并复用；几何变化会使旧解自动失效，下一次求解会重新计算。避免删除并重建已求解 Sweep，可绕过 AEDT 2025.2 保留旧 Sweep 名称并自动追加随机后缀的问题。

求解使用两个职责分离的扫频：`Sweep1` 是 2.30–2.60 GHz、301 点的插值扫频，仅用于 S11、谐振和带宽；`EfficiencySweep` 是 2.40–2.50 GHz、11 点的离散扫频，同时启用 `save_fields` 与 `save_rad_fields`，以 10 MHz 间隔保存计算天线参数所需的求解场和辐射场。总体效率始终在目标频率 2.45 GHz ±25 MHz 内计算，不再随实测谐振移动；失谐候选仍能得到合法数值，但会因目标频段内失配而自然降低。

## 候选结果格式

每次模型工具调用都会收到协议版本 3 的结果。候选成功时，`data.candidate` 中包含每个 Python 阶段的结果、带单位的设计参数/实测指标和落盘路径：

```json
{
  "protocol_version": 3,
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
        "s11_min_db": -22.0,
        "bandwidth_mhz": 120.0,
        "peak_gain_dbi": 8.0,
        "total_efficiency_mean_percent": 90.0
      },
      "measured_metrics": {
        "peak_gain": {
          "value": 8.0,
          "unit": "dBi",
          "value_representation": "10_log10_power_gain_relative_to_isotropic"
        }
      },
      "stages": {
        "build": {"success": true},
        "validation": {"success": true},
        "solve": {"success": true},
        "save": {"success": true},
        "metrics": {"success": true}
      }
    }
  },
  "iterations_used": 1,
  "remaining_design_iterations": 4,
  "remaining_solve_calls": 4,
  "latest_successful_iteration": 1
}
```

任一阶段失败都会记录为该候选的最终状态，例如 `build_failed`、`validation_failed` 或 `solve_failed`。求解完成后会先保存工程检查点，再读取指标。候选谐振落在固定扫频边缘、导致完整效率窗口无法计算时，候选仍记为 `completed_with_metric_penalty`，缺失目标由评测器计零分，模型可以据此继续调整；只有已经具备有效覆盖范围却仍读不到 HFSS 报告数据时，才视为后端致命错误并立即停止。断开时不会在已不可靠的自动化上下文中重复保存。真实 AEDT 消息窗口中的近期错误和 Python traceback 会附在建模错误中，便于定位端口、网格和求解器问题。

## 最终评测与候选选择

候选生成结束后，`AntennaEvaluator` 独立读取 `run_manifest.json` 和结构化任务，不会解析自然语言或要求 LLM 自我判分：

1. 找出所有完成建模、校验、求解、保存和读数的候选。
2. 使用同一结构化任务和评分函数独立计算每个完整成功候选的总分，并选择总分最高者。
3. 总分完全相同时选择迭代编号较大的候选；失败候选不参与排名，也不会覆盖此前的可评分结果。
4. 按任务中的目标语义计算每项通过状态和连续分数。

因此，模型可以在有限轮次内尝试较激进的改动；后续候选即使性能退化或流水线失败，也不会覆盖此前已经得到的更优可评分结果。

默认任务总分 100，评测器检查：

- 谐振频率：线性 GHz，目标 2.45 GHz，容差 0.025 GHz，15 分。
- 最小 S11：`20 log10(|S11|)`，单位 dB，越负越好，20 分。
- 连续 -10 dB 带宽：线性 MHz，25 分。
- HFSS PeakGain：对数 dBi，不是线性增益或 realized gain，20 分。
- 目标频率 2.45 GHz ±25 MHz 固定窗口内的单端口平均总体效率：把离散 `EfficiencySweep` 的 `RadiationEfficiency` 插值到 1 MHz 的 `Sweep1` 网格，再计算 `RadiationEfficiency × (1-|S11|²)`；结果是线性百分比，不是 dB，20 分。窗口不跟随实际谐振点，因此失谐会通过目标频段内的失配损耗反映在分数中。HFSS `RadiationEfficiency` 按线性比值读取，即使数值误差使其略大于 1；逐频点总体效率在评分前裁剪到 `[0, 1]`，并禁止外推。

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
    task_spec.json
    resource_usage.json
    design_log.json
    run_manifest.json
    evaluation_report.json
    manual_verification.json
```

`task_spec.json` 是该次运行实际采用的只读合同快照；`resource_usage.json` 记录逐次模型延迟、API 返回的 token usage、候选/求解次数和求解耗时。`candidate_NNN_metrics.json` 同时保存带单位的设计参数、结构化测量值和后端原始指标。`manual_verification.json` 给出选中工程、AEDT 对象名、固定控制和逐项核验步骤，方便在 AEDT 中人工复现报告。

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
python main.py --task-spec tasks/inset_patch_2p45.json --max-design-iterations 10 --max-solve-calls 10
```

可用 `--description-supplement "..."`（兼容别名 `--requirements`）附加自然语言说明；它不会改变结构化阈值或评分。

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
- `task_spec.py`：结构化任务校验、只读控制解析、指标结构化和评分
- `tasks/*.json`：可人工阅读并可复现的评测任务合同
- `prompts.py`：LLM 行为协议和两个工具的 schema
- `model_client.py`：Responses API / Chat Completions API 适配
- `design_loop.py`：候选生成循环与 Python 完整仿真流水线
- `hfss_client.py`：可信 PyAEDT 建模、校验、求解、读数和工程副本管理
- `evaluator.py`：独立结构化评分与最后完整成功候选选择
- `main.py`：批次目录创建和两阶段流程入口

真实 AEDT 集成依赖本机许可证、安装版本和求解环境。离线测试不会启动 AEDT；修改建模代码后，应先运行离线测试，再用 `verify_min.py` 和一次低迭代真实任务验证。
