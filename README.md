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

题目清单通过 `task_batch.json` 声明。`--task-batch` 会按列表顺序加载启用的题目；每个题目拥有独立的 `task_id`、结果目录、任务快照和评测报告。`--task-batch` 与 `--model-batch` 可以组合使用，每个题目-模型组合都获得独立的候选与求解预算。

拓扑专属校验、参数解析、模型工具名称和提示词约束存放在 `frameworks/` 中，并由任务 JSON 的 `topology.id` 自动发现和选择。`task_spec.py` 只保留任务公共合同、指标通用结构和评分调度；新增题型不会改变现有题目的字段集合、约束或仿真控制。

当前题目清单包含三道题：原内嵌馈电 2.45 GHz 贴片、同轴馈电 1.9/2.45 GHz 双频贴片、单馈同轴圆极化 GPS L1 贴片。两道新增题目均为 `uncalibrated`，其 `full_score_threshold` / `full_score_tolerance` 是校准前占位线，不能用于正式排行榜。

含满分线的 objective 使用三段计分：零分线到合格线贡献 0–60%，合格线到满分线贡献 60–100%。未提供满分线的旧 objective 保持原单段计分。注入模型的任务 JSON 会删除所有满分线字段，避免向模型泄露见证解性能；评测报告则保留完整校准信息并记录 `engineering_passed`、`benchmark_score`、`weakest_objective`、`geometric_mean_score` 和 `best_score_trajectory`。

模型只会看到两个工具：

- `create_patch_antenna({...})`：提交一个完整候选。一次调用会由 Python 自动完成整条仿真流水线。
- `finalize_design({"summary": "..."})`：由模型主动结束候选生成。至少有一个候选完整仿真成功后才允许调用。

模型不参与 `validate_design`、`solve`、`get_result` 或 `export_design` 的调度。这些步骤不会再消耗模型轮次，也不会因模型调用上限而把最后一个候选停在未求解状态。

`--max-iterations` 表示允许提交并完整处理的候选数量，而不是模型调用次数。每一轮候选都统一包含建模、求解和读指标；不再单独限制模型调用次数或求解次数。模型可以在认为当前候选已满足要求时提前调用 `finalize_design`；Python 不会根据指标替模型提前停止。达到迭代上限时，生成阶段自动结束，且不会额外补做一个隐藏候选或隐藏求解。

## 满分解校准

“存在满分解”不能只靠合理的目标值推断，必须由见证解验证。推荐的出题流程是：先冻结任务合同和 HFSS 版本/设置，再由人工、传统优化器或更大的离线求解预算找到至少一个候选；用与正式评测完全相同的 builder、求解和读数链路独立复算；最后把满分阈值放在见证解性能以内，并预留大于网格与数值波动的余量。参考设计用于证明可达性，不用于比较模型参数是否接近“标准答案”，也不应暴露给参评模型。

任务中的 `calibration` 明确记录校准状态。只有在同一固定环境中验证过满分见证解后，才能设为 `verified`；否则 `main.py` 会显示警告。对`uncalibrated`状态的题目，应该继续调整和收集结果，尚不适合直接作为正式排行榜题目。

## 固定量、约束与设计变量

- 固定评测环境：HFSS Setup、扫频、场数据保存、端口归一化、空气区域、边界、远场球、材料数值属性和资源预算。它们由评测端控制，模型只能读取。
- 题目约束：允许的拓扑、端口数、制造/尺寸边界和材料选择范围。它们定义模型可以搜索的设计空间。
- 设计变量：真正用于考察设计能力的尺寸和拓扑选择，例如贴片长宽、基板尺寸、馈线宽度、内嵌深度和槽隙。

当前题目固定为“居中内嵌微带馈电”，但并没有固定馈点深度：`feed_width_mm`、`inset_depth_mm` 和 `inset_gap_mm` 都由模型选择。横向偏置和馈电类型尚未开放。若要比较馈电方案，建议先分别实现并验证 `edge_microstrip`、`inset_microstrip`、`coax_probe` 等受信任 builder，再在混合题中开放离散 `feed_type`；不要直接允许模型提交任意 HFSS 几何或脚本。

## 扩展其他天线拓扑

当前后端只注册了内嵌馈电矩形贴片。增加一种可公平评测的新天线，至少需要同步增加：

1. `frameworks/` 下的拓扑框架：拓扑 ID、模型可调参数 schema、几何约束、仿真控制校验和设计参数解析。
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
  "remaining_iterations": 4,
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

普通运行会创建一个 `run_*` 根目录；启用多题时，每个任务在其中拥有独立子目录。该任务产生的工程副本、求解目录、指标、对话和评测报告都保存在其子目录中：

```text
hfss_projects/
  run_20260820_153000_123456/
    run_results.json
    01_dual_band_patch_1p9_2p45_v1/
      candidate_001.aedt
      candidate_001_metrics.json
      task_spec.json
      resource_usage.json
      design_log.json
      run_manifest.json
      evaluation_report.json
      manual_verification.json
    02_cp_patch_gps_1p575_v1/
      ...
```

`run_results.json` 是本次多任务运行的根索引，记录每个任务的结果目录、得分和关键报告路径。`task_spec.json` 是该任务实际采用的只读合同快照；`resource_usage.json` 记录逐次模型延迟、API 返回的 token usage、候选/求解次数和求解耗时。`candidate_NNN_metrics.json` 同时保存带单位的设计参数、结构化测量值和后端原始指标。`manual_verification.json` 给出选中工程、AEDT 对象名、固定控制和逐项核验步骤，方便在 AEDT 中人工复现报告。真实 AEDT 模式会关闭 PyAEDT 的控制台与临时文件日志；PyAEDT 消息保留在图形模式 AEDT 的 Message Manager 中。

多模型模式会在同一个 `batch_*` 根目录下先按模型、再按任务建立隔离子目录：

```text
hfss_projects/
  batch_20260825_160000_123456/
    model_batch.json
    batch_results.json
    batch_results_<task_id>.json
    01_gpt55/
      01_dual_band_patch_1p9_2p45_v1/
        candidate_001.aedt
        run_manifest.json
        evaluation_report.json
        resource_usage.json
        ...
      02_cp_patch_gps_1p575_v1/
        ...
    02_claude_opus_48/
      ...
```

`batch_results.json` 汇总任务清单和每个任务的独立报告路径；`batch_results_<task_id>.json` 汇总该任务下模型名称、配置文件、API 类型、得分排名、选中候选和各结果文件路径，不保存 API key。批次严格串行运行；每个模型结束后关闭其 AEDT 桌面，再启动下一个模型，避免活动工程和求解状态相互污染。某一个模型-任务组合发生 API 或 AEDT 异常时会在其子目录写入 `run_error.json`，随后继续运行清单中的下一项。

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
MAX_ITERATIONS=5
AEDT_ROOT=C:\ANSYS Inc\ANSYS Student\v252\AnsysEM
AEDT_KEEP_OPEN=true
```

API key 也可以保留在 `job_gpt55.yaml` 的 `agents[0].kwargs.api_key` 中。单模型默认模式的配置优先级为环境变量或 `.env` 高于 YAML，再高于代码默认值；批次模式则以每一项显式引用的 YAML 为准，避免全局 `MODEL_*` 环境变量把所有批次项覆盖成同一个模型。

多模型清单位于 `model_batch.json`，按 `models` 数组顺序执行：

```json
{
  "schema_version": 1,
  "models": [
    {
      "id": "gpt55",
      "config_file": "job_gpt55.yaml",
      "api_style": "responses",
      "enabled": true
    },
    {
      "id": "claude_opus_48",
      "config_file": "job_claude_opus_48.yaml",
      "api_style": "anthropic_messages",
      "enabled": true
    }
  ]
}
```

`api_style` 支持 `responses`、`chat_completions` 和 `anthropic_messages`。该字段可省略，框架会根据 YAML 中的 `llm_import_path` 推断；显式填写更便于审计。`id` 只用于结果子目录和汇总，不会发送给模型。批次 JSON 只引用 YAML 文件，不应复制其中的 API key。

本项目的 `.gitignore` 会忽略 `job_*.yaml`，避免新增的本地 API key 配置被意外提交。已经被 Git 跟踪的旧配置不会因 `.gitignore` 自动移除，仍应在提交前单独检查其中是否含真实密钥。

`AEDT_KEEP_OPEN` 默认为 `true`。无论成功还是失败，程序都会保存当前项目并仅释放 Python 自动化连接，AEDT 项目和窗口保持打开供检查；设置为 `false` 才会关闭项目和桌面。

## 运行

先验证 AEDT Student 2025 R2 连接：

```powershell
python verify_min.py
```

运行真实设计。通常让求解预算不低于候选迭代上限：

```powershell
python main.py --task-spec tasks/inset_patch_2p45.json --max-iterations 10
```

运行 `model_batch.json` 中的全部启用模型，每个模型分别获得相同的候选和求解预算：

```powershell
python main.py --model-batch model_batch.json --task-spec tasks/inset_patch_2p45.json --max-iterations 10
```

按 `task_batch.json` 的顺序完成全部启用题目：

```powershell
python main.py --task-batch task_batch.json --max-iterations 10
```

让每个模型依次完成题目清单中的每个题目：

```powershell
python main.py --model-batch model_batch.json --task-batch task_batch.json --max-iterations 10
```

只测试某一个新增 YAML 配置：

```powershell
python main.py --model-config job_kimi_k3.yaml --max-iterations 5
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
- `task_spec.py`：任务公共合同校验、只读控制解析、指标结构化和评分
- `frameworks/`：按拓扑独立存放的设计框架、专属约束和参数解析
- `tasks/*.json`：可人工阅读并可复现的评测任务合同
- `task_batch.py` / `task_batch.json`：多题目清单校验、加载与顺序定义
- `prompts.py`：LLM 行为协议和两个工具的 schema
- `model_client.py`：Responses、OpenAI-compatible Chat Completions 和 Anthropic Messages API 适配
- `model_batch.py` / `model_batch.json`：多模型清单校验与批次定义
- `design_loop.py`：候选生成循环与 Python 完整仿真流水线
- `hfss_client.py`：可信 PyAEDT 建模、校验、求解、读数和工程副本管理
- `evaluator.py`：独立结构化评分与历次最高分成功候选选择
- `main.py`：单模型/多模型批次目录创建和两阶段流程入口

真实 AEDT 集成依赖本机许可证、安装版本和求解环境。离线测试不会启动 AEDT；修改建模代码后，应先运行离线测试，再用 `verify_min.py` 和一次低迭代真实任务验证。
