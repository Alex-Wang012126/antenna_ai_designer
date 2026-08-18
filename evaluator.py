"""设计评测脚本

读取 HFSS 设计或导出的结果文件，计算关键指标：
- S11 最小值 / -10 dB 带宽
- 中心频率 / 谐振频率
- 增益 / 辐射效率

TODO：当前为占位实现，真实数据读取逻辑待 HFSS API 提供后补充。
"""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .config import Config, cfg
from .hfss_client import HFSSClient


@dataclass
class EvaluationResult:
    """评测结果。"""
    passed: bool
    metrics: Dict[str, Any]
    checklist: Dict[str, bool]
    summary: str


class Evaluator(ABC):
    """评测器抽象基类。"""

    @abstractmethod
    def evaluate(
        self,
        requirements: str,
        design_file: Optional[Union[str, Path]] = None,
        hfss_client: Optional[HFSSClient] = None,
        metrics_file: Optional[Union[str, Path]] = None,
    ) -> EvaluationResult:
        """对设计进行评测。"""
        raise NotImplementedError


class AntennaEvaluator(Evaluator):
    """针对天线指标的占位评测器。"""

    def __init__(self, config: Config = cfg):
        self.config = config

    def _extract_targets(self, requirements: str) -> Dict[str, Optional[float]]:
        """从需求文本中提取目标指标（占位，可用正则/LLM改进）。"""
        # 简单示例：从文本中查找类似 "2.45 GHz" 的目标频率
        targets: Dict[str, Optional[float]] = {
            "target_freq_ghz": None,
            "max_s11_db": -10.0,
            "min_bandwidth_mhz": 50.0,
            "min_gain_dbi": 0.0,
        }
        # 极简单的频率匹配：找 "x.xx GHz"
        m = re.search(r"(\d+\.?\d*)\s*GHz", requirements, re.IGNORECASE)
        if m:
            targets["target_freq_ghz"] = float(m.group(1))
        return targets

    def _compute_bandwidth(
        self,
        freq_ghz: List[float],
        s11_db: List[float],
        threshold: float = -10.0,
    ) -> Dict[str, Any]:
        """从 S11 曲线计算 -10 dB 带宽（简单线性插值占位）。"""
        indices = [i for i, v in enumerate(s11_db) if v <= threshold]
        if not indices:
            return {"bandwidth_mhz": 0.0, "low_ghz": None, "high_ghz": None}

        low_idx, high_idx = indices[0], indices[-1]
        low_ghz = freq_ghz[low_idx]
        high_ghz = freq_ghz[high_idx]
        bw_mhz = (high_ghz - low_ghz) * 1000.0
        return {"bandwidth_mhz": bw_mhz, "low_ghz": low_ghz, "high_ghz": high_ghz}

    def evaluate(
        self,
        requirements: str,
        design_file: Optional[Union[str, Path]] = None,
        hfss_client: Optional[HFSSClient] = None,
        metrics_file: Optional[Union[str, Path]] = None,
    ) -> EvaluationResult:
        targets = self._extract_targets(requirements)

        # 指标来源优先级：落盘的指标文件（设计循环结束时导出）> 在线 HFSS 客户端。
        # 两者都没有时不编造数据——缺失指标会直接判为不通过。
        metrics: Dict[str, Any] = {}
        if metrics_file is not None:
            try:
                metrics = json.loads(Path(metrics_file).read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[评测] 读取指标文件失败: {e}")
        elif hfss_client is not None:
            metrics_result = hfss_client.get_metrics()
            if metrics_result.success:
                metrics = metrics_result.data or {}

        # 判定是否通过：指标缺失（None）一律判失败，不给"白捡"的默认值
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

        bw = metrics.get("bandwidth_mhz")
        checklist["bandwidth"] = bw is not None and bw >= targets["min_bandwidth_mhz"]

        gain = metrics.get("peak_gain_dbi")
        checklist["gain"] = gain is not None and gain >= targets["min_gain_dbi"]

        passed = all(checklist.values())
        summary = (
            f"评测结果：{'通过' if passed else '未通过'}\n"
            f"目标频率: {targets['target_freq_ghz']} GHz, "
            f"实际中心频率: {metrics.get('center_freq_ghz')} GHz\n"
            f"S11 最小值: {s11} dB\n"
            f"-10 dB 带宽: {bw} MHz\n"
            f"峰值增益: {metrics.get('peak_gain_dbi')} dBi\n"
            f"辐射效率: {metrics.get('radiation_efficiency_percent')}%\n"
            f"检查项: {checklist}"
        )

        # 保存评测报告（带时间戳，避免多次运行互相覆盖）
        self.config.ensure_dirs()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.config.log_dir / f"evaluation_report_{timestamp}.json"
        report_file.write_text(
            json.dumps(
                {
                    "requirements": requirements,
                    "targets": targets,
                    "metrics": metrics,
                    "checklist": checklist,
                    "passed": passed,
                    "summary": summary,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"[评测报告已保存] {report_file}")

        return EvaluationResult(passed=passed, metrics=metrics, checklist=checklist, summary=summary)
