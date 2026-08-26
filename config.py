"""项目配置

HFSS 相关路径、模型调用参数等都集中在这里。
配置优先级（高 -> 低）：环境变量 / .env > yaml 配置文件 > 代码默认值。
模型参数可以从项目根目录下的 job_gpt55.yaml（可用 MODEL_CONFIG_YAML
指定其他文件）读取；环境变量和 .env 的值优先于 YAML。
多模型批次显式列出的 YAML 对模型身份、端点和密钥具有优先权，避免同一组
MODEL_* 环境变量把所有批次项错误地覆盖成同一个模型。
"""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Optional

# 若安装了 python-dotenv，则自动加载项目根目录下的 .env 文件
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def infer_model_api_style(llm_import_path: Optional[str]) -> str:
    """Map the Harbor adapter hint to one of the locally supported HTTP APIs."""
    hint = (llm_import_path or "").lower()
    if "responses" in hint:
        return "responses"
    if "claude" in hint or "anthropic" in hint:
        return "anthropic_messages"
    return "chat_completions"


def load_model_config_from_yaml(
    config_file: Optional[Path | str] = None,
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    """从 yaml 文件中读取模型配置。

    期望结构（取 agents 列表第一项）：
        agents:
          - model_name: gpt-5.5
            kwargs:
              api_base: https://api.apevon.ai/v1
              api_key: your-api-key
    单模型默认配置缺失时返回空字典；批次加载使用 ``strict=True``，
    让配置错误只影响对应模型并被批次汇总记录。
    """
    if config_file is None:
        config_file = os.getenv("MODEL_CONFIG_YAML", "job_gpt55.yaml")
    path = Path(config_file).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    path = path.resolve()
    if not path.is_file():
        if strict:
            raise FileNotFoundError(f"模型配置文件不存在: {path}")
        return {}
    try:
        import yaml
    except ImportError as exc:
        if strict:
            raise RuntimeError("未安装 PyYAML，无法解析模型配置") from exc
        print("未安装 PyYAML，无法解析 yaml 配置文件，回退到默认值")
        return {}

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"模型配置根节点必须是对象: {path}")

    agents = data.get("agents") or []
    if not agents:
        if strict:
            raise ValueError(f"模型配置缺少非空 agents 列表: {path}")
        return {}
    agent = agents[0] or {}
    if not isinstance(agent, dict):
        raise ValueError(f"模型配置的 agents[0] 必须是对象: {path}")
    kwargs = agent.get("kwargs") or {}
    if not isinstance(kwargs, dict):
        raise ValueError(f"模型配置的 agents[0]/kwargs 必须是对象: {path}")
    token_parameter = (
        "max_completion_tokens"
        if kwargs.get("max_completion_tokens") is not None
        else "max_tokens"
    )
    max_tokens = kwargs.get(token_parameter)
    provider_options = {
        key: kwargs[key]
        for key in ("thinking", "output_config", "extra_body", "reasoning_effort")
        if key in kwargs and kwargs[key] is not None
    }
    output_config = kwargs.get("output_config")
    google_options = (
        (kwargs.get("extra_body") or {}).get("google", {})
        if isinstance(kwargs.get("extra_body"), dict)
        else {}
    )
    thinking_config = (
        google_options.get("thinking_config", {})
        if isinstance(google_options, dict)
        else {}
    )
    reasoning_effort = (
        kwargs.get("reasoning_effort")
        or (output_config.get("effort") if isinstance(output_config, dict) else None)
        or (
            thinking_config.get("thinking_level")
            if isinstance(thinking_config, dict)
            else None
        )
    )
    llm_import_path = kwargs.get("llm_import_path")
    return {
        "config_file": path,
        "job_name": data.get("job_name"),
        "model_name": agent.get("model_name"),
        "model_base_url": kwargs.get("api_base"),
        "model_api_key": kwargs.get("api_key"),
        "model_api_style": infer_model_api_style(llm_import_path),
        "model_llm_import_path": llm_import_path,
        "model_temperature": kwargs.get("temperature"),
        "model_max_completion_tokens": max_tokens,
        "model_token_parameter": token_parameter,
        "model_reasoning_effort": reasoning_effort,
        "model_provider_options": provider_options,
        "model_request_interval": kwargs.get("request_interval"),
        "model_timeout_seconds": (
            float(data["timeout_multiplier"]) * 60.0
            if data.get("timeout_multiplier") is not None
            else None
        ),
    }


_yaml_model = load_model_config_from_yaml()


@dataclass(frozen=True)
class Config:
    # ---------------- 模型接口配置 ----------------
    # 环境变量 / .env 优先；缺省时回退到 YAML 和代码默认值。
    # 若使用本地模型（如 vLLM / ollama / 私有 API），则按实际情况修改 model_client.py。
    model_api_key: str = os.getenv("MODEL_API_KEY") or _yaml_model.get("model_api_key") or ""
    model_base_url: str = (
        os.getenv("MODEL_BASE_URL") or _yaml_model.get("model_base_url") or "https://api.apevon.ai/v1"
    )
    model_name: str = os.getenv("MODEL_NAME") or _yaml_model.get("model_name") or "gpt-5.5"
    model_temperature: float = float(
        os.getenv("MODEL_TEMPERATURE") or _yaml_model.get("model_temperature") or "1"
    )
    model_max_completion_tokens: int = int(
        os.getenv("MODEL_MAX_COMPLETION_TOKENS")
        or _yaml_model.get("model_max_completion_tokens")
        or "32000"
    )
    model_reasoning_effort: str = (
        os.getenv("MODEL_REASONING_EFFORT")
        or _yaml_model.get("model_reasoning_effort")
        or "high"
    )
    model_api_style: str = _yaml_model.get("model_api_style") or "responses"
    model_llm_import_path: Optional[str] = _yaml_model.get("model_llm_import_path")
    model_config_file: Optional[Path] = _yaml_model.get("config_file")
    model_token_parameter: str = _yaml_model.get("model_token_parameter") or "max_completion_tokens"
    model_provider_options: Dict[str, Any] = field(
        default_factory=lambda: dict(_yaml_model.get("model_provider_options") or {})
    )
    model_request_interval: float = float(_yaml_model.get("model_request_interval") or 0.0)
    model_timeout_seconds: float = float(
        os.getenv("MODEL_TIMEOUT_SECONDS")
        or _yaml_model.get("model_timeout_seconds")
        or 600.0
    )

    # ---------------- HFSS / AEDT 占位配置 ----------------
    # Ansys Electronics Desktop Student 2025 R2 示例配置
    aedt_version: str = os.getenv("AEDT_VERSION", "2025.2")
    aedt_student: bool = os.getenv("AEDT_STUDENT", "true").lower() in ("1", "true", "yes")
    # 非图形模式可设置为 True
    aedt_non_graphical: bool = os.getenv("AEDT_NON_GRAPHICAL", "false").lower() in ("1", "true", "yes")
    # 自动化结束后保留 AEDT 项目和窗口，便于检查成功或失败现场
    aedt_keep_open: bool = os.getenv("AEDT_KEEP_OPEN", "true").lower() in ("1", "true", "yes")
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
    # Maximum number of complete candidate designs proposed by the model.
    max_design_iterations: int = int(os.getenv("MAX_DESIGN_ITERATIONS", "5"))
    # 仿真次数预算：限制 solve 工具的调用次数，防止模型无限求解
    max_solve_calls: int = int(os.getenv("MAX_SOLVE_CALLS", "5"))
    log_dir: Path = Path(os.getenv("LOG_DIR", "./logs"))

    def ensure_dirs(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def config_from_model_yaml(
    base: Config,
    config_file: Path | str,
    *,
    api_style: Optional[str] = None,
) -> Config:
    """Return ``base`` with model-only fields replaced by one YAML profile.

    Batch profiles are authoritative for model identity, endpoint and credentials;
    shared AEDT/task/resource controls remain inherited from ``base``.
    """
    profile = load_model_config_from_yaml(config_file, strict=True)
    required = ("model_name", "model_base_url", "model_api_key")
    missing = [name for name in required if not profile.get(name)]
    if missing:
        raise ValueError(
            f"模型配置 {profile['config_file']} 缺少: {', '.join(missing)}"
        )
    selected_style = api_style or profile["model_api_style"]
    if selected_style not in {"responses", "chat_completions", "anthropic_messages"}:
        raise ValueError(f"不支持的模型 API 类型: {selected_style}")
    return replace(
        base,
        model_name=str(profile["model_name"]),
        model_base_url=str(profile["model_base_url"]),
        model_api_key=str(profile["model_api_key"]),
        model_temperature=float(
            profile["model_temperature"]
            if profile.get("model_temperature") is not None
            else base.model_temperature
        ),
        model_max_completion_tokens=int(
            profile["model_max_completion_tokens"]
            if profile.get("model_max_completion_tokens") is not None
            else base.model_max_completion_tokens
        ),
        model_reasoning_effort=str(
            profile["model_reasoning_effort"]
            if profile.get("model_reasoning_effort") is not None
            else base.model_reasoning_effort
        ),
        model_api_style=selected_style,
        model_llm_import_path=profile.get("model_llm_import_path"),
        model_config_file=Path(profile["config_file"]),
        model_token_parameter=str(profile["model_token_parameter"]),
        model_provider_options=dict(profile.get("model_provider_options") or {}),
        model_request_interval=float(profile.get("model_request_interval") or 0.0),
        model_timeout_seconds=float(
            profile["model_timeout_seconds"]
            if profile.get("model_timeout_seconds") is not None
            else base.model_timeout_seconds
        ),
    )


# 全局默认配置实例
cfg = Config()
