"""LLM-driven candidate generation with a Python-controlled HFSS pipeline.

The model only proposes complete antenna specifications or asks to stop. For every
proposal, Python performs build, validation, solve, saving, and metric extraction as
one atomic design iteration. Final pass/fail evaluation is intentionally separate.
"""

import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import Config, cfg
from hfss_client import HFSSClient, HFSSResult
from model_client import ChatResponse, ModelClient, ToolCall
from prompts import build_initial_message, build_system_prompt, build_tools_description
from task_spec import AntennaTaskSpec, load_default_task


@dataclass
class DesignLoopResult:
    """Artifacts produced by the candidate-generation stage."""

    success: bool
    final_summary: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    iterations_used: int = 0
    model_calls: int = 0
    log_file: Optional[Path] = None
    metrics_file: Optional[Path] = None
    manifest_file: Optional[Path] = None
    task_file: Optional[Path] = None
    resource_file: Optional[Path] = None
    selected_candidate: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def rounds_used(self) -> int:
        """Backward-compatible alias for callers that still display model calls."""
        return self.model_calls


class DesignAgent:
    """Ask the model for candidates and execute each candidate entirely in Python."""

    def __init__(
        self,
        model_client: ModelClient,
        hfss_client: HFSSClient,
        requirements: Optional[str] = None,
        config: Config = cfg,
        task_spec: Optional[AntennaTaskSpec] = None,
    ):
        self.model = model_client
        self.hfss = hfss_client
        self.config = config
        self.task_spec = task_spec or load_default_task()
        self.requirements = requirements or self.task_spec.description
        self.hfss.configure_task(self.task_spec)
        self.messages: List[Dict[str, Any]] = []
        self.iterations_used = 0
        self.model_calls = 0
        self._iteration_limit = config.max_design_iterations
        self._solve_count = 0
        self._finalized = False
        self._stop_reason = ""
        self._final_summary = ""
        self._candidates: List[Dict[str, Any]] = []
        self._selected_candidate: Optional[Dict[str, Any]] = None
        self._model_call_records: List[Dict[str, Any]] = []

    @property
    def remaining_solve_calls(self) -> int:
        return max(0, self.config.max_solve_calls - self._solve_count)

    @property
    def remaining_design_iterations(self) -> int:
        return max(0, self._iteration_limit - self.iterations_used)

    def _append_tool_result(self, tool_call_id: str, name: str, result: HFSSResult) -> None:
        content = json.dumps(
            {
                "protocol_version": 3,
                "tool": name,
                "success": result.success,
                "data": result.data,
                "message": result.message,
                "iterations_used": self.iterations_used,
                "remaining_design_iterations": self.remaining_design_iterations,
                "remaining_solve_calls": self.remaining_solve_calls,
                "latest_successful_iteration": (
                    self._selected_candidate.get("iteration") if self._selected_candidate else None
                ),
            },
            ensure_ascii=False,
            default=str,
        )
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": content,
            }
        )

    @staticmethod
    def _stage_result(result: HFSSResult) -> Dict[str, Any]:
        return {
            "success": result.success,
            "message": result.message,
            "data": result.data,
        }

    def _run_stage(
        self,
        record: Dict[str, Any],
        stage: str,
        operation: Callable[[], HFSSResult],
    ) -> HFSSResult:
        """Normalize backend exceptions and malformed returns into candidate failures."""
        started = time.perf_counter()
        try:
            result = operation()
            if not isinstance(result, HFSSResult):
                raise TypeError(f"HFSS client returned {type(result).__name__}, expected HFSSResult")
        except Exception as exc:
            result = HFSSResult(
                success=False,
                message=f"{stage} raised {type(exc).__name__}: {exc}",
            )
        stage_record = self._stage_result(result)
        stage_record["duration_seconds"] = round(time.perf_counter() - started, 6)
        record["stages"][stage] = stage_record
        return result

    def _finish_failed_candidate(
        self,
        record: Dict[str, Any],
        stage: str,
        result: HFSSResult,
    ) -> HFSSResult:
        record["success"] = False
        record["status"] = f"{stage}_failed"
        record["failed_stage"] = stage
        record["message"] = result.message
        self._candidates.append(record)
        return HFSSResult(
            success=False,
            data={"candidate": record},
            message=f"候选 {record['iteration']} 在 {stage} 阶段失败: {result.message}",
        )

    def _run_candidate_pipeline(self, arguments: Dict[str, Any]) -> HFSSResult:
        """Run one complete build-to-save pipeline for a model-proposed candidate."""
        if self.iterations_used >= self._iteration_limit:
            return HFSSResult(success=False, message="候选设计迭代次数已用完。")

        self.iterations_used += 1
        iteration = self.iterations_used
        record: Dict[str, Any] = {
            "iteration": iteration,
            "success": False,
            "status": "started",
            "specification": arguments,
            "design_parameters": {},
            "resolved_specification": {},
            "project_file": None,
            "metrics_file": None,
            "metrics": {},
            "measured_metrics": {},
            "stages": {},
        }

        try:
            design = self.task_spec.validate_design(arguments)
            resolved = self.task_spec.resolve_patch_spec(arguments)
            record["specification"] = design.to_dict()
            record["design_parameters"] = self.task_spec.structured_design(arguments)
            record["resolved_specification"] = resolved.to_dict()
        except ValueError as exc:
            result = HFSSResult(success=False, message=f"Invalid patch specification: {exc}")
            record["stages"]["parameter_validation"] = self._stage_result(result)
            return self._finish_failed_candidate(record, "parameter_validation", result)

        print(f"\n[候选 {iteration}] Python 开始完整仿真流水线", flush=True)

        build = self._run_stage(
            record,
            "build",
            lambda: self.hfss.create_patch_antenna(record["specification"]),
        )
        if isinstance(build.data, dict) and build.data.get("project_path"):
            record["project_file"] = str(build.data["project_path"])
        if not build.success:
            return self._finish_failed_candidate(record, "build", build)

        validation = self._run_stage(record, "validation", self.hfss.validate_design)
        if not validation.success:
            return self._finish_failed_candidate(record, "validation", validation)

        if self.remaining_solve_calls <= 0:
            result = HFSSResult(success=False, message="HFSS 求解次数预算已用完。")
            record["stages"]["solve"] = self._stage_result(result)
            return self._finish_failed_candidate(record, "solve", result)

        self._solve_count += 1
        solve = self._run_stage(record, "solve", self.hfss.solve)
        if not solve.success:
            return self._finish_failed_candidate(record, "solve", solve)

        # Save a solved checkpoint before post-processing.  A report/API failure must
        # not leave the active AEDT project unnamed and poison the next Save As.
        saved = self._run_stage(record, "save", self.hfss.export_design)
        if isinstance(saved.data, dict) and saved.data.get("file_path"):
            record["project_file"] = str(saved.data["file_path"])
        if not saved.success:
            return self._finish_failed_candidate(record, "save", saved)

        metrics = self._run_stage(record, "metrics", lambda: self.hfss.get_result("all"))
        if metrics.success and (not isinstance(metrics.data, dict) or not metrics.data):
            metrics = HFSSResult(
                success=False,
                data=metrics.data,
                message="HFSS metrics must be a non-empty JSON object.",
            )
            record["stages"]["metrics"] = self._stage_result(metrics)
        if not metrics.success:
            failed = self._finish_failed_candidate(record, "metrics", metrics)
            fatal_backend_error = bool(
                isinstance(metrics.data, dict)
                and metrics.data.get("_fatal_backend_error")
            )
            if fatal_backend_error:
                self._finalized = True
                self._stop_reason = "hfss_metrics_backend_failure"
                self._final_summary = (
                    f"候选 {iteration} 已在指标读取前保存，但 AEDT 必要后处理数据不可用。"
                    "为避免在失效自动化会话上继续 Save As，设计循环已停止。"
                )
            return failed
        record["metrics"] = metrics.data
        record["measured_metrics"] = self.task_spec.structured_metrics(metrics.data)

        metrics_file = self.config.log_dir / f"candidate_{iteration:03d}_metrics.json"
        try:
            metrics_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "task_id": self.task_spec.task_id,
                        "iteration": iteration,
                        "design_parameters": record["design_parameters"],
                        "measured_metrics": record["measured_metrics"],
                        "raw_metrics": metrics.data,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            result = HFSSResult(success=False, message=f"保存候选指标失败: {type(exc).__name__}: {exc}")
            record["stages"]["metrics_file"] = self._stage_result(result)
            return self._finish_failed_candidate(record, "metrics_file", result)

        record["metrics_file"] = str(metrics_file.resolve())
        record["success"] = True
        has_metric_penalty = bool(metrics.data.get("metric_errors"))
        record["status"] = (
            "completed_with_metric_penalty" if has_metric_penalty else "completed"
        )
        record["message"] = (
            "完整仿真成功，但部分指标因候选响应超出固定覆盖范围而不可用，"
            "独立评测会将对应目标计为零分。"
            if has_metric_penalty
            else "建模、校验、求解、保存和指标读取均成功。"
        )
        self._candidates.append(record)
        self._selected_candidate = record
        print(f"[候选 {iteration}] 完整流水线成功: {metrics.data}", flush=True)
        return HFSSResult(
            success=True,
            data={"candidate": record},
            message=(
                f"候选 {iteration} 完整仿真成功，但存在零分指标；"
                "请根据实测结果继续修改或结束。"
                if has_metric_penalty
                else f"候选 {iteration} 完整仿真成功，请根据实测指标决定继续修改或结束。"
            ),
        )

    def _execute_tool(self, tool_call: ToolCall) -> HFSSResult:
        name = tool_call.name
        args = tool_call.arguments
        if not isinstance(args, dict):
            return HFSSResult(success=False, message="Tool arguments must be a JSON object.")

        if name == "create_patch_antenna":
            return self._run_candidate_pipeline(args)

        if name == "finalize_design":
            if set(args) != {"summary"} or not isinstance(args.get("summary"), str) or not args["summary"].strip():
                return HFSSResult(success=False, message="finalize_design requires one non-empty summary string.")
            if len(args["summary"]) > 2000:
                return HFSSResult(success=False, message="finalize_design summary must not exceed 2000 characters.")
            if self._selected_candidate is None:
                return HFSSResult(
                    success=False,
                    message="Cannot finalize before at least one candidate completes the full simulation pipeline.",
                )
            self._finalized = True
            self._stop_reason = "model_requested"
            self._final_summary = args["summary"]
            return HFSSResult(
                success=True,
                data={
                    "summary": self._final_summary,
                    "selected_candidate": self._selected_candidate,
                },
                message="Candidate generation finalized; independent evaluation will run next.",
            )

        return HFSSResult(success=False, message=f"Unknown tool: {name}")

    def _build_assistant_message(self, response: ChatResponse) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": f"call_{self.model_calls}_{i}",
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                    },
                }
                for i, tool_call in enumerate(response.tool_calls)
            ]
        return msg

    def run(self, max_iterations: Optional[int] = None) -> DesignLoopResult:
        if max_iterations is None:
            max_iterations = self.config.max_design_iterations
        if max_iterations < 1:
            raise ValueError("max_iterations must be greater than zero")

        self._iteration_limit = max_iterations
        self.iterations_used = 0
        self.model_calls = 0
        self._solve_count = 0
        self._finalized = False
        self._stop_reason = ""
        self._final_summary = ""
        self._candidates = []
        self._selected_candidate = None
        self._model_call_records = []
        run_started_at = datetime.now(timezone.utc)
        run_started_clock = time.perf_counter()

        self.config.ensure_dirs()
        task_file = self.task_spec.write(self.config.log_dir / "task_spec.json")
        connection = self.hfss.connect(self.config)
        if not connection.success:
            raise RuntimeError(f"无法连接 HFSS/AEDT: {connection.message}")

        self.messages = [
            {"role": "system", "content": build_system_prompt(self.task_spec)},
            build_initial_message(self.task_spec, self.requirements),
        ]
        log_file = self.config.log_dir / "design_log.json"
        manifest_file = self.config.log_dir / "run_manifest.json"
        resource_file = self.config.log_dir / "resource_usage.json"
        max_model_calls = max_iterations * 3 + 3

        try:
            while not self._finalized and self.iterations_used < max_iterations:
                if self.model_calls >= max_model_calls:
                    self._stop_reason = "model_call_safety_limit"
                    self._final_summary = "模型重复调用无效工具，已达到内部安全上限。"
                    break

                model_call_started = time.perf_counter()
                try:
                    response = self.model.chat(
                        self.messages,
                        tools=build_tools_description(self.task_spec),
                    )
                except KeyboardInterrupt:
                    self._model_call_records.append(
                        {
                            "call_index": len(self._model_call_records) + 1,
                            "success": False,
                            "interrupted": True,
                            "duration_seconds": round(
                                time.perf_counter() - model_call_started, 6
                            ),
                            "error": "KeyboardInterrupt: user interrupted model request",
                            "usage": {},
                        }
                    )
                    raise
                except Exception as exc:
                    self._model_call_records.append(
                        {
                            "call_index": len(self._model_call_records) + 1,
                            "success": False,
                            "duration_seconds": round(time.perf_counter() - model_call_started, 6),
                            "error": f"{type(exc).__name__}: {exc}",
                            "usage": {},
                        }
                    )
                    self._stop_reason = "model_error"
                    self._final_summary = f"模型调用失败（{type(exc).__name__}: {exc}），设计循环提前终止。"
                    print(f"\n[模型错误] {self._final_summary}")
                    self.model_calls += 1
                    break

                measured_latency = time.perf_counter() - model_call_started
                self._model_call_records.append(
                    {
                        "call_index": len(self._model_call_records) + 1,
                        "success": True,
                        "duration_seconds": round(measured_latency, 6),
                        "provider_latency_seconds": response.latency_seconds,
                        "response_id": response.response_id,
                        "usage": response.usage,
                        "tool_calls": [tool_call.name for tool_call in response.tool_calls],
                        "content_characters": len(response.content or ""),
                    }
                )

                assistant_message = self._build_assistant_message(response)
                self.messages.append(assistant_message)

                if not response.tool_calls:
                    self.model_calls += 1
                    self._stop_reason = "model_no_tool"
                    self._final_summary = response.content or "模型未返回工具调用，设计结束。"
                    if self._selected_candidate is not None:
                        self._finalized = True
                    break

                if len(response.tool_calls) != 1:
                    for i, tool_call in enumerate(response.tool_calls):
                        self._append_tool_result(
                            f"call_{self.model_calls}_{i}",
                            tool_call.name,
                            HFSSResult(
                                success=False,
                                message="Protocol violation: make exactly one tool call per response.",
                            ),
                        )
                    self.model_calls += 1
                    continue

                tool_call = response.tool_calls[0]
                tool_call_id = f"call_{self.model_calls}_0"
                print(
                    f"\n[模型调用 {self.model_calls + 1}] {tool_call.name}({tool_call.arguments})",
                    flush=True,
                )
                try:
                    result = self._execute_tool(tool_call)
                except Exception as exc:
                    error_message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                    result = HFSSResult(success=False, message=error_message)
                    print(f"[流水线错误] {error_message}")

                self._append_tool_result(tool_call_id, tool_call.name, result)
                status = "成功" if result.success else "失败"
                print(
                    f"[模型调用 {self.model_calls + 1}] {tool_call.name} -> {status}: "
                    f"{str(result.message)[:300]}",
                    flush=True,
                )
                self.model_calls += 1

            if not self._finalized and self.iterations_used >= max_iterations:
                self._stop_reason = "iteration_limit"
                self._finalized = self._selected_candidate is not None
                self._final_summary = (
                    f"已用完 {max_iterations} 次候选设计迭代，提交最后一个完整仿真候选进行独立评测。"
                    if self._selected_candidate is not None
                    else f"已用完 {max_iterations} 次候选设计迭代，但没有候选完成完整仿真流水线。"
                )
        except KeyboardInterrupt:
            self._stop_reason = "keyboard_interrupt"
            self._final_summary = "用户通过 KeyboardInterrupt 中止运行；现有工程、清单和资源日志已保存。"
            print(f"\n[运行中断] {self._final_summary}", flush=True)
        finally:
            try:
                self.hfss.disconnect()
            except Exception as exc:
                print(f"\n[断开连接异常] {type(exc).__name__}: {exc}")

            if self._selected_candidate is None:
                successful = [candidate for candidate in self._candidates if candidate.get("success")]
                if successful:
                    self._selected_candidate = successful[-1]

            run_finished_at = datetime.now(timezone.utc)
            token_keys = (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
            )
            token_totals: Dict[str, Optional[int]] = {}
            for key in token_keys:
                values = [
                    call.get("usage", {}).get(key)
                    for call in self._model_call_records
                    if isinstance(call.get("usage", {}).get(key), int)
                ]
                token_totals[key] = sum(values) if values else None
            solve_seconds = sum(
                float(candidate.get("stages", {}).get("solve", {}).get("duration_seconds", 0.0))
                for candidate in self._candidates
            )
            resource_usage = {
                "schema_version": 1,
                "task_id": self.task_spec.task_id,
                "run": {
                    "started_at_utc": run_started_at.isoformat(),
                    "finished_at_utc": run_finished_at.isoformat(),
                    "duration_seconds": round(time.perf_counter() - run_started_clock, 6),
                },
                "model": {
                    "client_class": type(self.model).__name__,
                    "configured_model_name": self.config.model_name,
                    "configured_reasoning_effort": self.config.model_reasoning_effort,
                    "configured_max_completion_tokens_per_call": self.config.model_max_completion_tokens,
                    "call_safety_limit": max_model_calls,
                    "attempted_calls": len(self._model_call_records),
                    "token_totals": token_totals,
                    "calls": self._model_call_records,
                },
                "hfss": {
                    "client_class": type(self.hfss).__name__,
                    "candidate_attempts": self.iterations_used,
                    "solve_calls": self._solve_count,
                    "solve_duration_seconds": round(solve_seconds, 6),
                    "max_design_iterations": max_iterations,
                    "max_solve_calls": self.config.max_solve_calls,
                },
            }
            resource_file.write_text(
                json.dumps(resource_usage, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            manifest = {
                "protocol_version": 3,
                "task_id": self.task_spec.task_id,
                "task_spec_file": str(task_file.resolve()),
                "natural_language_supplement": self.requirements,
                "stop_reason": self._stop_reason,
                "final_summary": self._final_summary,
                "max_design_iterations": max_iterations,
                "iterations_used": self.iterations_used,
                "model_calls": self.model_calls,
                "solve_calls": self._solve_count,
                "resource_usage_file": str(resource_file.resolve()),
                "resource_summary": {
                    "model_calls": len(self._model_call_records),
                    "token_totals": token_totals,
                    "hfss_solve_calls": self._solve_count,
                    "hfss_solve_duration_seconds": round(solve_seconds, 6),
                },
                "selected_iteration": (
                    self._selected_candidate.get("iteration") if self._selected_candidate else None
                ),
                "candidates": self._candidates,
            }
            manifest_file.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            log_file.write_text(
                json.dumps(
                    {
                        **{key: value for key, value in manifest.items() if key != "candidates"},
                        "messages": self.messages,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            print(f"\n[候选清单已保存] {manifest_file}")
            print(f"[对话日志已保存] {log_file}")
            print(f"[任务快照已保存] {task_file}")
            print(f"[资源统计已保存] {resource_file}")

        metrics_file = (
            Path(self._selected_candidate["metrics_file"])
            if self._selected_candidate and self._selected_candidate.get("metrics_file")
            else None
        )
        return DesignLoopResult(
            success=self._selected_candidate is not None,
            final_summary=self._final_summary,
            messages=self.messages,
            iterations_used=self.iterations_used,
            model_calls=self.model_calls,
            log_file=log_file,
            metrics_file=metrics_file,
            manifest_file=manifest_file,
            task_file=task_file,
            resource_file=resource_file,
            selected_candidate=self._selected_candidate,
            candidates=self._candidates,
        )
