# Antenna AI Designer

一个“模型多轮调用 HFSS API -> 生成设计文件 -> 指标评测”的 Python 项目。
默认使用真实模型（OpenAI 风格接口）与真实 AEDT（PyAEDT）运行；
另提供占位模式（无需真实模型/HFSS 即可跑通流程），用 `--use-placeholder` 切换。

## 目录结构

```
antenna_ai_designer/
├── __init__.py
├── config.py           # 全局配置
├── prompts.py          # 提示词与工具描述
├── model_client.py     # 模型调用接口（占位 + 真实 OpenAI 风格客户端）
├── hfss_client.py      # HFSS/AEDT 调用接口（占位 + PyAEDT 客户端）
├── design_loop.py      # 多轮设计主循环
├── evaluator.py        # 设计评测脚本
├── main.py             # 入口脚本
├── verify_min.py       # 最小化 AEDT 连接验证脚本
├── job_kimi_k3.yaml    # 模型配置（api_key / api_base / model_name）
├── requirements.txt    # 依赖清单
└── README.md           # 本文件
```

## 快速体验（占位模式）

不需要真实模型或 HFSS，即可跑通整个流程。在本目录下运行：

```bash
python main.py --use-placeholder
```

会按顺序执行：
1. `set_variable`
2. `update_geometry`
3. `solve`
4. `get_result`
5. `export_design`
6. `finalize_design`

并输出一份占位评测报告。

## 验证 AEDT 连接

安装 `pyaedt` 后，可先运行根目录下的 `verify_min.py`，
确认能正常拉起 Ansys Electronics Desktop Student 2025 R2：

```bash
pip install pyaedt
python verify_min.py
```

连接成功会打印 AEDT 版本号并释放桌面会话。

## 真实模式

真实模式使用：

- **模型**：`model_client.OpenAIModelClient`（requests 直连 OpenAI 风格
  `/chat/completions` 接口；配置来自环境变量 / `.env` / `job_kimi_k3.yaml`，
  默认指向 Moonshot Kimi）
- **HFSS**：`hfss_client.PyAEDTHFSSClient`（PyAEDT 驱动
  Ansys Electronics Desktop Student 2025 R2）

步骤（在本目录下运行）：

```bash
pip install pyaedt          # 真实 HFSS 客户端依赖
python verify_min.py        # 先确认能拉起 AEDT Student 2025 R2
python main.py \
    --requirements "设计一个中心频率 2.45 GHz 的微带贴片天线，S11 < -10 dB"
```

要点：

- `update_geometry` 工具通过 `exec(script, {"hfss": hfss})` 执行 PyAEDT 脚本，
  脚本中直接使用 `hfss` 对象（`ansys.aedt.core.Hfss`），不是 AEDT 录制脚本的
  `oEditor` 风格；系统提示词中已明确告知模型。
- 命名约定已统一并写入系统提示词：Setup 为 `Setup1`，扫频为 `Sweep1`，
  端口为 `Port1`，读取结果用 `"Setup1 : Sweep1"` 与 `dB(S(1,1))`。
- `solve` 有次数预算（默认 5 次，`MAX_SOLVE_CALLS` 可调），
  每条工具结果都会向模型回报剩余次数。
- 设计循环结束时会先把指标落盘到 `logs/metrics_<时间戳>.json`，
  评测脚本离线读取该文件，无需为评测第二次拉起 AEDT。
- 评测时指标缺失一律判不通过（不会用默认值放行）。
- 评测阈值（频率、S11、带宽、增益）从需求文本中解析，解析不到才用默认值。
- 每次运行生成带时间戳的项目文件（`hfss_projects/eval_design_<时间戳>.aedt`），
  避免重复运行互相污染；日志/指标/评测报告文件名时间戳精确到微秒。
- 真实模式下各关键节点有带前缀的调试输出：HFSS 客户端 `[PyAEDT]`、
  模型客户端 `[MODEL]`、主循环 `[Round N]`，排查问题时按前缀检索即可。

真实客户端在连接时会设置 `settings.grpc_secure_mode = False`
（AEDT Student 2025 R2 的 gRPC server 以 insecure 模式启动，与 `verify_min.py` 一致）。
若 gRPC 仍连接失败，可在 `hfss_client.PyAEDTHFSSClient.connect`
中启用 `use_grpc_api=False` 改用 COM。

## 提示词与工具

- `prompts.py` 中的 `build_system_prompt()` 和 `build_tools_description()` 负责把 HFSS 工具能力描述给模型。
- 工具名与 `design_loop.py` 中的 `_execute_tool` 分发逻辑一一对应。

## 评测指标

`evaluator.py` 当前计算：

- 中心频率
- S11 最小值
- -10 dB 带宽
- 峰值增益
- 辐射效率

真实 HFSS 数据读取逻辑在 `hfss_client.get_metrics()` 中实现。

## 环境变量

`config.py` 会自动加载项目根目录下的 `.env` 文件（依赖 `python-dotenv`）。
模型相关配置（`MODEL_API_KEY` / `MODEL_BASE_URL` / `MODEL_NAME`）的读取优先级为：

1. 环境变量 / `.env`（最高优先级）
2. yaml 配置文件：默认读取项目根目录下的 `job_kimi_k3.yaml`，取其中
   `agents[0].model_name` 与 `agents[0].kwargs.api_base / api_key`；
   可用环境变量 `MODEL_CONFIG_YAML` 指定其他文件
3. 代码内默认值

**不要把 API key 写死在代码里**。如需用环境变量覆盖，可配置：

```bash
MODEL_API_KEY=sk-...
MODEL_BASE_URL=https://api.moonshot.cn/v1
MODEL_NAME=kimi-k3
MODEL_CONFIG_YAML=job_kimi_k3.yaml
AEDT_VERSION=2025.2
AEDT_STUDENT=true
AEDT_NON_GRAPHICAL=false
PROJECT_DIR=./hfss_projects
LOG_DIR=./logs
MAX_DESIGN_ROUNDS=10
MAX_SOLVE_CALLS=5
DEFAULT_DESIGN_NAME=antenna
DEFAULT_SETUP_NAME=Setup1
DEFAULT_SWEEP_NAME=Sweep1
```
