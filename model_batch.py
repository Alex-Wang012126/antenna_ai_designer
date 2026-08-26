"""Validated model-batch definitions for sequential benchmark runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


SUPPORTED_API_STYLES = {
    "responses",
    "chat_completions",
    "anthropic_messages",
}
_MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass(frozen=True)
class ModelBatchEntry:
    model_id: str
    config_file: Path
    enabled: bool = True
    api_style: Optional[str] = None


@dataclass(frozen=True)
class ModelBatchSpec:
    source_file: Path
    entries: List[ModelBatchEntry]

    @classmethod
    def load(cls, source_file: Path | str) -> "ModelBatchSpec":
        source = Path(source_file).expanduser().resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("模型批次配置必须是 JSON object")
        if set(raw) != {"schema_version", "models"}:
            raise ValueError("模型批次配置只能包含 schema_version 和 models")
        if raw["schema_version"] != 1:
            raise ValueError("仅支持模型批次 schema_version=1")
        models = raw["models"]
        if not isinstance(models, list) or not models:
            raise ValueError("模型批次 models 必须是非空列表")

        entries: List[ModelBatchEntry] = []
        seen_ids = set()
        for index, value in enumerate(models, start=1):
            if not isinstance(value, dict):
                raise ValueError(f"models[{index - 1}] 必须是 JSON object")
            allowed = {"id", "config_file", "enabled", "api_style"}
            extra = sorted(set(value) - allowed)
            if extra:
                raise ValueError(
                    f"models[{index - 1}] 包含未知字段: {', '.join(extra)}"
                )
            model_id = value.get("id")
            if not isinstance(model_id, str) or not _MODEL_ID_PATTERN.fullmatch(model_id):
                raise ValueError(
                    f"models[{index - 1}].id 必须匹配 {_MODEL_ID_PATTERN.pattern}"
                )
            if model_id in seen_ids:
                raise ValueError(f"模型 id 重复: {model_id}")
            seen_ids.add(model_id)

            config_value = value.get("config_file")
            if not isinstance(config_value, str) or not config_value.strip():
                raise ValueError(f"models[{index - 1}].config_file 必须是非空字符串")
            config_file = Path(config_value).expanduser()
            if not config_file.is_absolute():
                config_file = source.parent / config_file
            config_file = config_file.resolve()
            if config_file.suffix.lower() not in {".yaml", ".yml"}:
                raise ValueError(f"模型配置必须是 YAML 文件: {config_file}")
            if not config_file.is_file():
                raise FileNotFoundError(f"模型配置文件不存在: {config_file}")

            enabled = value.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError(f"models[{index - 1}].enabled 必须是 boolean")
            api_style = value.get("api_style")
            if api_style is not None and api_style not in SUPPORTED_API_STYLES:
                raise ValueError(
                    f"models[{index - 1}].api_style 不受支持: {api_style}"
                )
            entries.append(
                ModelBatchEntry(
                    model_id=model_id,
                    config_file=config_file,
                    enabled=enabled,
                    api_style=api_style,
                )
            )

        if not any(entry.enabled for entry in entries):
            raise ValueError("模型批次中至少要启用一个模型")
        return cls(source_file=source, entries=entries)

    def public_snapshot(self) -> Dict[str, Any]:
        """Return serializable metadata that never contains API credentials."""
        return {
            "schema_version": 1,
            "source_file": str(self.source_file),
            "models": [
                {
                    "id": entry.model_id,
                    "config_file": str(entry.config_file),
                    "enabled": entry.enabled,
                    "api_style": entry.api_style,
                }
                for entry in self.entries
            ],
        }
