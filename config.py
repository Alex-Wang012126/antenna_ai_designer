"""项目配置

HFSS 相关路径、模型调用参数等都集中在这里。
配置优先级（高 -> 低）：环境变量 / .env > yaml 配置文件 > 代码默认值。
模型相关的 key/base_url/model_name 默认从项目根目录下的
job_gpt55.yaml（可用 MODEL_CONFIG_YAML 指定其他文件）读取。
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

# 若安装了 python-dotenv，则自动加载项目根目录下的 .env 文件
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _load_model_config_from_yaml() -> Dict[str, Any]:
    """从 yaml 文件中读取模型配置。

    期望结构（取 agents 列表第一项）：
        agents:
          - model_name: gpt-5.5
            kwargs:
              api_base: https://api.apevon.ai/v1
              api_key: sk-...
    文件不存在或未安装 PyYAML 时返回空字典，回退到默认值。
    """
    path = Path(__file__).resolve().parent / os.getenv("MODEL_CONFIG_YAML", "job_gpt55.yaml")
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        print("未安装 PyYAML，无法解析 yaml 配置文件，回退到默认值")
        return {}

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    agents = data.get("agents") or []
    if not agents:
        return {}
    agent = agents[0] or {}
    kwargs = agent.get("kwargs") or {}
    print(f"模型配置: model_name={agent.get('model_name')}, api_base={kwargs.get('api_base')}, api_key={'已配置' if kwargs.get('api_key') else '未配置'}")
    return {
        "model_name": agent.get("model_name"),
        "model_base_url": kwargs.get("api_base"),
        "model_api_key": kwargs.get("api_key"),
    }


_yaml_model = _load_model_config_from_yaml()


@dataclass(frozen=True)
class Config:
    # ---------------- 模型接口配置 ----------------
    # 优先取环境变量 / .env；缺省时回退到 yaml 配置；再缺省用代码默认值。
    # 若使用本地模型（如 vLLM / ollama / 私有 API），则按实际情况修改 model_client.py。
    model_api_key: str = os.getenv("MODEL_API_KEY") or _yaml_model.get("model_api_key") or ""
    model_base_url: str = (
        os.getenv("MODEL_BASE_URL") or _yaml_model.get("model_base_url") or "https://api.apevon.ai/v1"
    )
    model_name: str = os.getenv("MODEL_NAME") or _yaml_model.get("model_name") or "gpt-5.5"
    model_temperature: float = float(os.getenv("MODEL_TEMPERATURE", "0.2"))
    model_max_tokens: int = int(os.getenv("MODEL_MAX_TOKENS", "16384"))

    # ---------------- HFSS / AEDT 占位配置 ----------------
    # Ansys Electronics Desktop Student 2025 R2 示例配置
    aedt_version: str = os.getenv("AEDT_VERSION", "2025.2")
    aedt_student: bool = os.getenv("AEDT_STUDENT", "true").lower() in ("1", "true", "yes")
    # 非图形模式可设置为 True
    aedt_non_graphical: bool = os.getenv("AEDT_NON_GRAPHICAL", "false").lower() in ("1", "true", "yes")
    # 项目目录：生成的 HFSS 项目文件默认放在这里
    project_dir: Path = Path(os.getenv("PROJECT_DIR", "./hfss_projects"))
    # 默认设计名、求解/扫参名（与 prompts.py 中告知模型的命名约定保持一致）
    default_design_name: str = os.getenv("DEFAULT_DESIGN_NAME", "antenna")
    default_setup_name: str = os.getenv("DEFAULT_SETUP_NAME", "Setup1")
    default_sweep_name: str = os.getenv("DEFAULT_SWEEP_NAME", "Sweep1")

    @property
    def default_setup_sweep_name(self) -> str:
        """pyaedt get_solution_data 使用的 'Setup : Sweep' 格式。"""
        return f"{self.default_setup_name} : {self.default_sweep_name}"

    # ---------------- 运行控制 ----------------
    max_design_rounds: int = int(os.getenv("MAX_DESIGN_ROUNDS", "10"))
    # 仿真次数预算：限制 solve 工具的调用次数，防止模型无限求解
    max_solve_calls: int = int(os.getenv("MAX_SOLVE_CALLS", "5"))
    log_dir: Path = Path(os.getenv("LOG_DIR", "./logs"))

    def ensure_dirs(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


# 全局默认配置实例
cfg = Config()
