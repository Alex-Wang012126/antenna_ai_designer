"""Independent structured evaluation for antenna benchmark runs.

The natural-language description is never parsed for thresholds. The authoritative
task contract supplies units, measurement semantics, pass criteria, and scoring.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from config import Config, cfg
from hfss_client import HFSSClient
from task_spec import AntennaTaskSpec, load_default_task


@dataclass
class EvaluationResult:
    passed: bool
    score: float
    max_score: float
    metrics: Dict[str, Any]
    measured_metrics: Dict[str, Any]
    design_parameters: Dict[str, Any]
    checklist: Dict[str, bool]
    summary: str
    selected_iteration: Optional[int] = None
    selected_project_file: Optional[Path] = None
    report_file: Optional[Path] = None
    verification_file: Optional[Path] = None
    candidate_evaluations: List[Dict[str, Any]] = field(default_factory=list)


class Evaluator(ABC):
    @abstractmethod
    def evaluate(
        self,
        requirements: Optional[str] = None,
        design_file: Optional[Union[str, Path]] = None,
        hfss_client: Optional[HFSSClient] = None,
        metrics_file: Optional[Union[str, Path]] = None,
        manifest_file: Optional[Union[str, Path]] = None,
        task_spec: Optional[AntennaTaskSpec] = None,
    ) -> EvaluationResult:
        raise NotImplementedError


class AntennaEvaluator(Evaluator):
    """Score every complete candidate and select the highest-scoring one."""

    def __init__(self, config: Config = cfg, task_spec: Optional[AntennaTaskSpec] = None):
        self.config = config
        self.task_spec = task_spec

    @staticmethod
    def _candidate_metrics(candidate: Dict[str, Any]) -> Dict[str, Any]:
        metrics = candidate.get("metrics") or {}
        if metrics:
            return metrics
        metrics_file = candidate.get("metrics_file")
        if metrics_file:
            try:
                payload = json.loads(Path(metrics_file).read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload.get("raw_metrics") or payload
            except Exception:
                return {}
        return {}

    @staticmethod
    def _load_metrics_file(metrics_file: Union[str, Path]) -> Dict[str, Any]:
        payload = json.loads(Path(metrics_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload.get("raw_metrics") or payload

    def evaluate(
        self,
        requirements: Optional[str] = None,
        design_file: Optional[Union[str, Path]] = None,
        hfss_client: Optional[HFSSClient] = None,
        metrics_file: Optional[Union[str, Path]] = None,
        manifest_file: Optional[Union[str, Path]] = None,
        task_spec: Optional[AntennaTaskSpec] = None,
    ) -> EvaluationResult:
        task = task_spec or self.task_spec or load_default_task()
        metrics: Dict[str, Any] = {}
        design_parameters: Dict[str, Any] = {}
        selected_iteration: Optional[int] = None
        selected_project_file: Optional[Path] = None
        candidate_evaluations: List[Dict[str, Any]] = []
        selection_policy = "direct_metrics_input"
        model_metadata: Dict[str, Any] = {}

        if manifest_file is not None:
            try:
                manifest = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
                candidates = list(manifest.get("candidates") or [])
                model_metadata = dict(manifest.get("model") or {})
            except Exception as exc:
                print(f"[评测] 读取候选清单失败: {exc}")
                candidates = []

            successful: List[tuple[Dict[str, Any], Dict[str, Any], int]] = []
            for manifest_position, candidate in enumerate(candidates):
                evaluation: Dict[str, Any] = {
                    "iteration": candidate.get("iteration"),
                    "pipeline_success": bool(candidate.get("success")),
                    "project_file": candidate.get("project_file"),
                    "status": candidate.get("status"),
                    "selected_for_scoring": False,
                }
                if candidate.get("success"):
                    candidate_metrics = self._candidate_metrics(candidate)
                    evaluation.update(task.evaluate_metrics(candidate_metrics))
                    evaluation["measured_metrics"] = task.structured_metrics(candidate_metrics)
                    successful.append((candidate, evaluation, manifest_position))
                candidate_evaluations.append(evaluation)

            # Compare only candidates that completed the trusted pipeline. Higher score
            # wins; an exact tie goes to the later iteration (then later manifest entry)
            # so selection remains deterministic without penalizing further refinement.
            selection_policy = "highest_scoring_successful_candidate_latest_on_tie"

            def selection_key(
                item: tuple[Dict[str, Any], Dict[str, Any], int]
            ) -> tuple[float, float, int]:
                candidate, evaluation, manifest_position = item
                iteration = candidate.get("iteration")
                iteration_rank = (
                    float(iteration)
                    if isinstance(iteration, (int, float)) and not isinstance(iteration, bool)
                    else float(manifest_position)
                )
                return float(evaluation.get("score", 0.0)), iteration_rank, manifest_position

            selected_entry = max(successful, key=selection_key) if successful else None
            if selected_entry is not None:
                selected, selected_evaluation, _ = selected_entry
                metrics = self._candidate_metrics(selected)
                selected_iteration = selected.get("iteration")
                if selected.get("project_file"):
                    selected_project_file = Path(selected["project_file"])
                design_parameters = selected.get("design_parameters") or {}
                if not design_parameters and selected.get("specification"):
                    try:
                        design_parameters = task.structured_design(selected["specification"])
                    except ValueError:
                        design_parameters = {}
                selected_evaluation["selected_for_scoring"] = True
        elif metrics_file is not None:
            try:
                metrics = self._load_metrics_file(metrics_file)
                payload = json.loads(Path(metrics_file).read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    design_parameters = payload.get("design_parameters") or {}
                    selected_iteration = payload.get("iteration")
            except Exception as exc:
                print(f"[评测] 读取指标文件失败: {exc}")
        elif hfss_client is not None:
            metrics_result = hfss_client.get_metrics()
            if metrics_result.success:
                metrics = metrics_result.data or {}

        scored = task.evaluate_metrics(metrics)
        measured_metrics = task.structured_metrics(metrics)
        passed = bool(scored["passed"])
        checklist = scored["checklist"]
        score = float(scored["score"])
        max_score = float(scored["max_score"])
        selection_description = (
            "历次完整成功候选中总分最高者（同分取较后迭代）"
            if manifest_file is not None
            else "直接评测输入指标"
        )
        summary = (
            f"评测结果：{'通过' if passed else '未通过'}\n"
            f"任务: {task.task_id}\n"
            f"候选选择策略: {selection_description}\n"
            f"选中候选: {selected_iteration if selected_iteration is not None else '无'}\n"
            f"得分: {score:.3f}/{max_score:.0f}\n"
            f"检查项: {checklist}\n"
            f"结构化实测指标: {measured_metrics}"
        )
        best_score_trajectory: List[float] = []
        best_score = 0.0
        if manifest_file is not None:
            for evaluation in candidate_evaluations:
                candidate_score = (
                    float(evaluation.get("benchmark_score", 0.0))
                    if evaluation.get("pipeline_success")
                    else 0.0
                )
                best_score = max(best_score, candidate_score)
                best_score_trajectory.append(round(best_score, 6))
        else:
            best_score_trajectory.append(round(score, 6))

        self.config.ensure_dirs()
        report_file = (
            Path(manifest_file).resolve().parent / "evaluation_report.json"
            if manifest_file is not None
            else self.config.log_dir
            / f"evaluation_report_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        )
        verification_file = report_file.parent / "manual_verification.json"
        verification_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": task.task_id,
                    "calibration": task.data["calibration"],
                    "purpose": "Human cross-check of the selected candidate in Ansys Electronics Desktop.",
                    "selected_iteration": selected_iteration,
                    "project_file": str(selected_project_file) if selected_project_file else None,
                    "aedt_objects": {
                        "design_name": self.config.default_design_name,
                        "solution_setup": self.config.default_setup_name,
                        "frequency_sweep": self.config.default_sweep_name,
                        "efficiency_sweep": task.data["simulation_control"].get(
                            "efficiency_sweep", {}
                        ).get("name"),
                        "far_field_setup": "InfiniteSphere1",
                        "port": "Port1",
                        "substrate_material": "AntennaSubstrate",
                    },
                    "verification_steps": [
                        "Open project_file and select the named HFSS design.",
                        "Compare model dimensions with design_parameters below.",
                        "Compare AntennaSubstrate properties with fixed_parameters.",
                        "Compare the trusted port, radiation region, Setup, sweeps, and InfiniteSphere1 with simulation_control.",
                        *(getattr(task.framework, "manual_verification_steps", lambda: [])()),
                    ],
                    "derived_metric_formulas": getattr(
                        task.framework, "manual_verification_formulas", lambda: {}
                    )(),
                    "design_parameters": design_parameters,
                    "fixed_parameters": task.data["fixed_parameters"],
                    "simulation_control": task.data["simulation_control"],
                    "raw_metrics": metrics,
                    "measured_metrics": measured_metrics,
                    "objective_evaluation": scored["objectives"],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        report = {
            "schema_version": 1,
            "task_id": task.task_id,
            "model": model_metadata,
            "calibration": task.data["calibration"],
            "natural_language_supplement": requirements,
            "selection_policy": selection_policy,
            "selected_iteration": selected_iteration,
            "selected_project_file": str(selected_project_file) if selected_project_file else None,
            "manual_verification_file": str(verification_file.resolve()),
            "design_parameters": design_parameters,
            "raw_metrics": metrics,
            "measured_metrics": measured_metrics,
            "objective_evaluation": scored["objectives"],
            "checklist": checklist,
            "passed": passed,
            "engineering_passed": bool(scored["engineering_passed"]),
            "score": score,
            "benchmark_score": float(scored["benchmark_score"]),
            "weakest_objective": scored["weakest_objective"],
            "geometric_mean_score": float(scored["geometric_mean_score"]),
            "best_score_trajectory": best_score_trajectory,
            "max_score": max_score,
            "candidate_evaluations": candidate_evaluations,
            "summary": summary,
        }
        report_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[评测报告已保存] {report_file}")
        print(f"[人工核验清单已保存] {verification_file}")

        return EvaluationResult(
            passed=passed,
            score=score,
            max_score=max_score,
            metrics=metrics,
            measured_metrics=measured_metrics,
            design_parameters=design_parameters,
            checklist=checklist,
            summary=summary,
            selected_iteration=selected_iteration,
            selected_project_file=selected_project_file,
            report_file=report_file,
            verification_file=verification_file,
            candidate_evaluations=candidate_evaluations,
        )
