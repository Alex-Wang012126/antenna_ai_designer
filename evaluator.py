"""设计评测脚本

读取设计循环落盘的指标文件（或在线 HFSS 客户端），对照需求判定：
- S11 最小值 / -10 dB 带宽
- 中心频率 / 谐振频率
- 增益 / 辐射效率

指标阈值（频率、S11、带宽、增益）从需求文本中解析；解析不到时使用默认值。
指标缺失一律判不通过，不用默认值放行。
"""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from config import Config, cfg
from hfss_client import HFSSClient


@dataclass
class EvaluationResult:
    """评测结果。"""
    passed: bool
    metrics: Dict[str, Any]
    checklist: Dict[str, bool]
    summary: str
    selected_iteration: Optional[int] = None
    selected_project_file: Optional[Path] = None
    report_file: Optional[Path] = None
    candidate_evaluations: List[Dict[str, Any]] = field(default_factory=list)


class Evaluator(ABC):
    """评测器抽象基类。"""

    @abstractmethod
    def evaluate(
        self,
        requirements: str,
        design_file: Optional[Union[str, Path]] = None,
        hfss_client: Optional[HFSSClient] = None,
        metrics_file: Optional[Union[str, Path]] = None,
        manifest_file: Optional[Union[str, Path]] = None,
    ) -> EvaluationResult:
        """对设计进行评测。"""
        raise NotImplementedError


class AntennaEvaluator(Evaluator):
    """针对天线指标的评测器。"""

    def __init__(self, config: Config = cfg):
        self.config = config

    def _extract_targets(self, requirements: str) -> Dict[str, Optional[float]]:
        """从需求文本中解析目标指标阈值；解析不到的项使用保守默认值。"""
        targets: Dict[str, Optional[float]] = {
            "target_freq_ghz": None,
            "max_s11_db": -10.0,
            "min_bandwidth_mhz": 50.0,
            "min_gain_dbi": 0.0,
        }
        # 目标频率："2.45 GHz"
        m = re.search(r"(\d+\.?\d*)\s*GHz", requirements, re.IGNORECASE)
        if m:
            targets["target_freq_ghz"] = float(m.group(1))
        # S11 阈值："S11 < -10 dB" / "回波损耗 < 10 dB" / "return loss < 10 dB"
        m = re.search(
            r"(?:S\s*11|回波损耗|return\s*loss)\s*[<≤＜]?\s*-?\s*(\d+\.?\d*)\s*dB",
            requirements,
            re.IGNORECASE,
        )
        if m:
            targets["max_s11_db"] = -abs(float(m.group(1)))
        # 带宽阈值："带宽 > 100 MHz" / "bandwidth > 0.1 GHz"
        m = re.search(
            r"(?:带宽|bandwidth)\s*[>≥＞]?\s*(\d+\.?\d*)\s*(MHz|GHz)",
            requirements,
            re.IGNORECASE,
        )
        if m:
            bw = float(m.group(1))
            if m.group(2).lower() == "ghz":
                bw *= 1000.0
            targets["min_bandwidth_mhz"] = bw
        # 增益阈值："增益 > 3 dBi" / "gain > 3 dBi"
        m = re.search(
            r"(?:增益|gain)\s*[>≥＞]?\s*(-?\d+\.?\d*)\s*dBi",
            requirements,
            re.IGNORECASE,
        )
        if m:
            targets["min_gain_dbi"] = float(m.group(1))
        return targets

    @staticmethod
    def _check_metrics(
        metrics: Dict[str, Any],
        targets: Dict[str, Optional[float]],
    ) -> Dict[str, bool]:
        checklist: Dict[str, bool] = {}
        if targets["target_freq_ghz"] is not None:
            center = metrics.get("center_freq_ghz")
            checklist["center_frequency"] = (
                center is not None and abs(center - targets["target_freq_ghz"]) <= 0.05
            )
        else:
            checklist["center_frequency"] = True

        s11 = metrics.get("s11_min_db")
        checklist["return_loss"] = s11 is not None and s11 <= targets["max_s11_db"]
        bandwidth = metrics.get("bandwidth_mhz")
        checklist["bandwidth"] = (
            bandwidth is not None and bandwidth >= targets["min_bandwidth_mhz"]
        )
        gain = metrics.get("peak_gain_dbi")
        checklist["gain"] = gain is not None and gain >= targets["min_gain_dbi"]
        return checklist

    @staticmethod
    def _candidate_metrics(candidate: Dict[str, Any]) -> Dict[str, Any]:
        metrics = candidate.get("metrics") or {}
        if metrics:
            return metrics
        metrics_file = candidate.get("metrics_file")
        if metrics_file:
            try:
                return json.loads(Path(metrics_file).read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def evaluate(
        self,
        requirements: str,
        design_file: Optional[Union[str, Path]] = None,
        hfss_client: Optional[HFSSClient] = None,
        metrics_file: Optional[Union[str, Path]] = None,
        manifest_file: Optional[Union[str, Path]] = None,
    ) -> EvaluationResult:
        targets = self._extract_targets(requirements)

        metrics: Dict[str, Any] = {}
        checklist: Dict[str, bool] = {}
        selected_iteration: Optional[int] = None
        selected_project_file: Optional[Path] = None
        candidate_evaluations: List[Dict[str, Any]] = []

        if manifest_file is not None:
            try:
                manifest = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
                candidates = list(manifest.get("candidates") or [])
            except Exception as e:
                print(f"[评测] 读取候选清单失败: {e}")
                candidates = []

            latest_completed: Optional[Dict[str, Any]] = None
            passing_candidate: Optional[Dict[str, Any]] = None
            for candidate in reversed(candidates):
                evaluation = {
                    "iteration": candidate.get("iteration"),
                    "pipeline_success": bool(candidate.get("success")),
                    "project_file": candidate.get("project_file"),
                    "status": candidate.get("status"),
                    "passed": False,
                    "checklist": {},
                }
                if candidate.get("success"):
                    latest_completed = latest_completed or candidate
                    candidate_metrics = self._candidate_metrics(candidate)
                    candidate_checklist = self._check_metrics(candidate_metrics, targets)
                    evaluation["metrics"] = candidate_metrics
                    evaluation["checklist"] = candidate_checklist
                    evaluation["passed"] = all(candidate_checklist.values())
                    if evaluation["passed"] and passing_candidate is None:
                        passing_candidate = candidate
                candidate_evaluations.append(evaluation)
                if passing_candidate is not None:
                    break

            selected = passing_candidate or latest_completed
            if selected is not None:
                metrics = self._candidate_metrics(selected)
                checklist = self._check_metrics(metrics, targets)
                selected_iteration = selected.get("iteration")
                if selected.get("project_file"):
                    selected_project_file = Path(selected["project_file"])
        elif metrics_file is not None:
            try:
                metrics = json.loads(Path(metrics_file).read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[评测] 读取指标文件失败: {e}")
        elif hfss_client is not None:
            metrics_result = hfss_client.get_metrics()
            if metrics_result.success:
                metrics = metrics_result.data or {}

        if not checklist:
            checklist = self._check_metrics(metrics, targets)

        passed = all(checklist.values())
        s11 = metrics.get("s11_min_db")
        bw = metrics.get("bandwidth_mhz")
        summary = (
            f"评测结果：{'通过' if passed else '未通过'}\n"
            f"选中候选: {selected_iteration if selected_iteration is not None else '无'}\n"
            f"目标频率: {targets['target_freq_ghz']} GHz, "
            f"实际中心频率: {metrics.get('center_freq_ghz')} GHz\n"
            f"S11 最小值: {s11} dB\n"
            f"-10 dB 带宽: {bw} MHz\n"
            f"峰值增益: {metrics.get('peak_gain_dbi')} dBi\n"
            f"辐射效率: {metrics.get('radiation_efficiency_percent')}%\n"
            f"检查项: {checklist}"
        )

        self.config.ensure_dirs()
        report_file = (
            Path(manifest_file).resolve().parent / "evaluation_report.json"
            if manifest_file is not None
            else self.config.log_dir / f"evaluation_report_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        )
        report_file.write_text(
            json.dumps(
                {
                    "requirements": requirements,
                    "targets": targets,
                    "metrics": metrics,
                    "checklist": checklist,
                    "passed": passed,
                    "selected_iteration": selected_iteration,
                    "selected_project_file": str(selected_project_file) if selected_project_file else None,
                    "candidate_evaluations": candidate_evaluations,
                    "summary": summary,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"[评测报告已保存] {report_file}")

        return EvaluationResult(
            passed=passed,
            metrics=metrics,
            checklist=checklist,
            summary=summary,
            selected_iteration=selected_iteration,
            selected_project_file=selected_project_file,
            report_file=report_file,
            candidate_evaluations=candidate_evaluations,
        )
