"""Validated task-batch definitions for sequential multi-question runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from task_spec import AntennaTaskSpec


_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass(frozen=True)
class TaskBatchEntry:
    task_id: str
    spec_file: Path
    enabled: bool = True


@dataclass(frozen=True)
class TaskBatchSpec:
    source_file: Path
    entries: List[TaskBatchEntry]

    @classmethod
    def load(cls, source_file: Path | str) -> "TaskBatchSpec":
        source = Path(source_file).expanduser().resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("题目批次配置必须是 JSON object")
        if set(raw) != {"schema_version", "tasks"}:
            raise ValueError("题目批次配置只能包含 schema_version 和 tasks")
        if raw["schema_version"] != 1:
            raise ValueError("仅支持题目批次 schema_version=1")
        tasks = raw["tasks"]
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("题目批次 tasks 必须是非空列表")

        entries: List[TaskBatchEntry] = []
        seen_ids = set()
        for index, value in enumerate(tasks, start=1):
            if not isinstance(value, dict):
                raise ValueError(f"tasks[{index - 1}] 必须是 JSON object")
            allowed = {"id", "spec_file", "enabled"}
            extra = sorted(set(value) - allowed)
            if extra:
                raise ValueError(f"tasks[{index - 1}] 包含未知字段: {', '.join(extra)}")

            task_id = value.get("id")
            if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
                raise ValueError(
                    f"tasks[{index - 1}].id 必须匹配 {_TASK_ID_PATTERN.pattern}"
                )
            if task_id in seen_ids:
                raise ValueError(f"题目 id 重复: {task_id}")
            seen_ids.add(task_id)

            spec_value = value.get("spec_file")
            if not isinstance(spec_value, str) or not spec_value.strip():
                raise ValueError(f"tasks[{index - 1}].spec_file 必须是非空字符串")
            spec_file = Path(spec_value).expanduser()
            if not spec_file.is_absolute():
                spec_file = source.parent / spec_file
            spec_file = spec_file.resolve()
            if spec_file.suffix.lower() != ".json":
                raise ValueError(f"题目定义必须是 JSON 文件: {spec_file}")
            if not spec_file.is_file():
                raise FileNotFoundError(f"题目定义文件不存在: {spec_file}")
            AntennaTaskSpec.load(spec_file)

            enabled = value.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError(f"tasks[{index - 1}].enabled 必须是 boolean")
            entries.append(
                TaskBatchEntry(
                    task_id=task_id,
                    spec_file=spec_file,
                    enabled=enabled,
                )
            )

        if not any(entry.enabled for entry in entries):
            raise ValueError("题目批次中至少要启用一个题目")
        return cls(source_file=source, entries=entries)

    def load_enabled_tasks(self) -> List[AntennaTaskSpec]:
        return [AntennaTaskSpec.load(entry.spec_file) for entry in self.entries if entry.enabled]

    def public_snapshot(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "source_file": str(self.source_file),
            "tasks": [
                {
                    "id": entry.task_id,
                    "spec_file": str(entry.spec_file),
                    "enabled": entry.enabled,
                }
                for entry in self.entries
            ],
        }
