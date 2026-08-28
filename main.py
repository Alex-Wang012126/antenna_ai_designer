"""入口脚本

用法示例（在本目录下运行）：
    python main.py \
        --task-spec tasks/inset_patch_2p45.json \
        --max-iterations 10

默认使用真实模型 + 真实 AEDT；加 --use-placeholder 可切换到占位演示模式。

运行后会：
1. 让模型提出完整候选参数或主动结束；
2. 由 Python 为每个候选自动完成建模、校验、求解、保存和读数；
3. 由独立评测器对所有完整成功候选评分，选择最高分候选并生成报告。
"""
import os
# 学生版 2025 R2 必须：强制使用 legacy "-grpcsrv <port>" 启动方式（官方 workaround）
os.environ.setdefault("PYAEDT_USE_PRE_GRPC_ARGS", "True")
# 正确的学生版安装路径变量名（原来写的是 ANSYSEM_ROOTSV252，顺序反了）
os.environ.setdefault(
    "ANSYSEMSV_ROOT252",
    os.getenv("AEDT_ROOT", r"C:\ANSYS Inc\ANSYS Student\v252\AnsysEM"),
)

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from config import Config, cfg, config_from_model_yaml
from design_loop import DesignAgent, DesignLoopResult
from evaluator import AntennaEvaluator, EvaluationResult
from hfss_client import PlaceholderHFSSClient, PyAEDTHFSSClient
from model_batch import ModelBatchSpec
from model_client import PlaceholderModelClient, create_model_client
from task_batch import TaskBatchSpec
from task_spec import AntennaTaskSpec, DEFAULT_TASK_FILE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI-driven antenna design with HFSS")
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument(
        "--task-spec",
        type=str,
        default=None,
        help="结构化评测任务 JSON；它是参数、单位、仿真控制和评分的唯一权威来源",
    )
    task_group.add_argument(
        "--task-batch",
        type=str,
        default=None,
        help="题目批次 JSON；按列表顺序依次完成每个启用的题目",
    )
    parser.add_argument(
        "--description-supplement",
        "--requirements",
        dest="requirements",
        type=str,
        default=None,
        help="可选自然语言补充，仅供模型和人工阅读，不参与阈值解析或评分",
    )
    parser.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=cfg.max_iterations,
        help="最多完整处理的候选设计迭代数量（每轮包含建模和求解）",
    )
    model_group = parser.add_mutually_exclusive_group()
    model_group.add_argument(
        "--model-config",
        type=str,
        default=None,
        help="单模型运行使用的 Harbor 格式 YAML；未指定时沿用 MODEL_CONFIG_YAML/job_gpt55.yaml",
    )
    model_group.add_argument(
        "--model-batch",
        type=str,
        default=None,
        help="模型批次 JSON；按列表顺序运行，每个模型使用独立结果子目录",
    )
    parser.add_argument(
        "--use-placeholder",
        action="store_true",
        help="使用占位模型和占位 HFSS 演示流程（默认关闭：默认使用真实模型 + 真实 AEDT）",
    )
    return parser.parse_args()


def run_single_model(
    runtime_config: Config,
    task_spec: AntennaTaskSpec,
    natural_language_supplement: str,
    *,
    use_placeholder: bool = False,
    display_label: str | None = None,
) -> Tuple[DesignLoopResult, EvaluationResult]:
    """Run the complete design and evaluation pipeline for exactly one model."""
    mode = "占位模式" if use_placeholder else "真实模式（模型 + AEDT）"
    print("=" * 60)
    title = f"天线 AI 设计工具 [{mode}]"
    if display_label:
        title += f" - {display_label}"
    print(title)
    print("=" * 60)
    print(f"结构化任务: {task_spec.task_id}")
    calibration_status = task_spec.data["calibration"]["status"]
    print(f"任务校准状态: {calibration_status}")
    if calibration_status != "verified":
        print("警告: 该任务尚未验证存在满分见证解，不应直接用于正式排行榜。")
    print(f"自然语言说明: {natural_language_supplement}")
    print(f"候选设计迭代上限: {runtime_config.max_iterations}\n")

    if use_placeholder:
        model_client = PlaceholderModelClient()
        hfss_client = PlaceholderHFSSClient()
    else:
        # 真实模式：先回显关键配置，连不上/调不通时方便排查
        print(f"模型: {runtime_config.model_name} @ {runtime_config.model_base_url}")
        key = runtime_config.model_api_key
        print(f"模型 API key: {'已配置' if key else '未配置！请设置 MODEL_API_KEY 或 .env'}")
        print(f"AEDT: version={runtime_config.aedt_version}, student={runtime_config.aedt_student}, "
              f"non_graphical={runtime_config.aedt_non_graphical}, "
              f"keep_open={runtime_config.aedt_keep_open}")
        print(f"项目目录: {runtime_config.project_dir.resolve()}\n")
        model_client = create_model_client(runtime_config)
        hfss_client = PyAEDTHFSSClient()

    # 阶段 1：设计循环
    agent = DesignAgent(
        model_client=model_client,
        hfss_client=hfss_client,
        requirements=natural_language_supplement,
        config=runtime_config,
        task_spec=task_spec,
    )
    design_result = agent.run(max_iterations=runtime_config.max_iterations)

    print("\n" + "=" * 60)
    print("设计结果")
    print("=" * 60)
    print(f"是否完成: {design_result.success}")
    print(f"候选迭代次数: {design_result.iterations_used}")
    print(f"最终总结:\n{design_result.final_summary}")
    print(f"日志文件: {design_result.log_file}")
    print(f"指标文件: {design_result.metrics_file}")
    print(f"候选清单: {design_result.manifest_file}")
    print(f"任务快照: {design_result.task_file}")
    print(f"资源统计: {design_result.resource_file}")

    # 阶段 2：评测（离线读取设计循环落盘的指标文件，无需重连 AEDT）
    evaluator = AntennaEvaluator(config=runtime_config, task_spec=task_spec)
    eval_result = evaluator.evaluate(
        requirements=natural_language_supplement,
        design_file=None,
        metrics_file=design_result.metrics_file,
        manifest_file=design_result.manifest_file,
        task_spec=task_spec,
    )

    print("\n" + "=" * 60)
    print("评测结果")
    print("=" * 60)
    print(eval_result.summary)
    if eval_result.selected_project_file:
        print(f"最终选中工程: {eval_result.selected_project_file}")
    print(f"人工核验清单: {eval_result.verification_file}")

    return design_result, eval_result


def _write_batch_report(report_file: Path, report: Dict[str, Any]) -> None:
    report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_run_report(report_file: Path, report: Dict[str, Any]) -> None:
    report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def run_model_batch(
    args: argparse.Namespace,
    task_specs: list[AntennaTaskSpec],
) -> int:
    batch_spec = ModelBatchSpec.load(args.model_batch)
    batch_id = datetime.now().strftime("batch_%Y%m%d_%H%M%S_%f")
    batch_dir = cfg.project_dir.expanduser().resolve() / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    snapshot_file = batch_dir / "model_batch.json"
    snapshot_file.write_text(
        json.dumps(batch_spec.public_snapshot(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    root_report: Dict[str, Any] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "status": "running",
        "execution_mode": "sequential",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "finished_at_utc": None,
        "model_batch_file": str(batch_spec.source_file),
        "model_batch_snapshot": str(snapshot_file),
        "max_iterations_per_model_per_task": args.max_iterations,
        "tasks": [],
    }
    root_report_file = batch_dir / "batch_results.json"
    _write_batch_report(root_report_file, root_report)

    had_execution_error = False
    for task_position, task_spec in enumerate(task_specs, start=1):
        task_report_file = batch_dir / f"batch_results_{task_spec.task_id}.json"
        root_report["tasks"].append(
            {
                "position": task_position,
                "task_id": task_spec.task_id,
                "task_spec_file": (
                    str(task_spec.source_file)
                    if task_spec.source_file is not None
                    else None
                ),
                "task_report": str(task_report_file),
                "status": "running",
            }
        )
        _write_batch_report(root_report_file, root_report)
        try:
            exit_code = _run_model_batch_one_task(
                args,
                task_spec,
                args.requirements or task_spec.description,
                batch_dir=batch_dir,
                task_position=task_position,
            )
        except Exception as exc:
            root_report["tasks"][-1]["error"] = f"{type(exc).__name__}: {exc}"
            exit_code = 1
        had_execution_error = had_execution_error or exit_code != 0
        root_report["tasks"][-1]["status"] = (
            "completed" if exit_code == 0 else "completed_with_errors"
        )
        _write_batch_report(root_report_file, root_report)

    root_report["status"] = (
        "completed_with_errors" if had_execution_error else "completed"
    )
    root_report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_batch_report(root_report_file, root_report)
    print(f"\n[批次汇总] {root_report_file}")
    return 1 if had_execution_error else 0


def _run_model_batch_one_task(
    args: argparse.Namespace,
    task_spec: AntennaTaskSpec,
    natural_language_supplement: str,
    batch_dir: Path,
    task_position: int,
) -> int:
    batch_spec = ModelBatchSpec.load(args.model_batch)
    batch_id = batch_dir.name
    report_file = batch_dir / f"batch_results_{task_spec.task_id}.json"
    snapshot_file = batch_dir / "model_batch.json"
    report: Dict[str, Any] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "status": "running",
        "execution_mode": "sequential",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "finished_at_utc": None,
        "task_id": task_spec.task_id,
        "task_spec_file": (
            str(task_spec.source_file)
            if task_spec.source_file is not None
            else None
        ),
        "model_batch_file": str(batch_spec.source_file),
        "model_batch_snapshot": str(snapshot_file),
        "max_iterations_per_model": args.max_iterations,
        "models": [],
    }
    _write_batch_report(report_file, report)

    enabled_entries = [entry for entry in batch_spec.entries if entry.enabled]
    print("=" * 72)
    print(f"开始模型批次: {batch_id}，共 {len(enabled_entries)} 个启用模型（严格串行）")
    print(f"批次目录: {batch_dir}")
    print("批次模式会在每个模型结束后关闭 AEDT，确保模型之间工程与会话隔离。")
    print("=" * 72)

    had_execution_error = False
    for position, entry in enumerate(batch_spec.entries, start=1):
        if not entry.enabled:
            report["models"].append(
                {
                    "position": position,
                    "model_id": entry.model_id,
                    "config_file": str(entry.config_file),
                    "status": "skipped_disabled",
                }
            )
            _write_batch_report(report_file, report)
            continue

        model_dir = batch_dir / f"{position:02d}_{entry.model_id}"
        run_dir = model_dir / f"{task_position:02d}_{task_spec.task_id}"
        run_dir.mkdir(parents=True, exist_ok=False)
        item: Dict[str, Any] = {
            "position": position,
            "model_id": entry.model_id,
            "config_file": str(entry.config_file),
            "result_directory": str(run_dir),
            "status": "running",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        report["models"].append(item)
        _write_batch_report(report_file, report)

        print("\n" + "#" * 72)
        print(f"批次模型 {position}/{len(batch_spec.entries)}: {entry.model_id}")
        print(f"配置文件: {entry.config_file}")
        print(f"结果目录: {run_dir}")
        print("#" * 72)
        try:
            model_config = config_from_model_yaml(
                cfg,
                entry.config_file,
                api_style=entry.api_style,
            )
            runtime_config = replace(
                model_config,
                project_dir=run_dir,
                log_dir=run_dir,
                max_iterations=args.max_iterations,
                # A retained desktop from one model can poison the next model's
                # active-project context. Saved projects remain available on disk.
                aedt_keep_open=False,
            )
            item.update(
                {
                    "model_name": runtime_config.model_name,
                    "api_style": runtime_config.model_api_style,
                    "temperature": runtime_config.model_temperature,
                    "reasoning_effort": runtime_config.model_reasoning_effort,
                    "max_completion_tokens": runtime_config.model_max_completion_tokens,
                    "request_timeout_seconds": runtime_config.model_timeout_seconds,
                }
            )
            design_result, evaluation = run_single_model(
                runtime_config,
                task_spec,
                natural_language_supplement,
                use_placeholder=args.use_placeholder,
                display_label=entry.model_id,
            )
            stop_reason = None
            if design_result.manifest_file and design_result.manifest_file.is_file():
                run_manifest = json.loads(
                    design_result.manifest_file.read_text(encoding="utf-8")
                )
                stop_reason = run_manifest.get("stop_reason")
            if stop_reason == "keyboard_interrupt":
                run_status = "interrupted"
            elif stop_reason == "model_error":
                run_status = "completed_with_model_error"
                had_execution_error = True
            elif stop_reason == "hfss_metrics_backend_failure":
                run_status = "completed_with_hfss_error"
                had_execution_error = True
            elif design_result.success:
                run_status = "completed"
            else:
                run_status = "completed_without_successful_candidate"
            item.update(
                {
                    "status": run_status,
                    "stop_reason": stop_reason,
                    "design_completed": design_result.success,
                    "evaluation_passed": evaluation.passed,
                    "score": evaluation.score,
                    "max_score": evaluation.max_score,
                    "selected_iteration": evaluation.selected_iteration,
                    "selected_project_file": (
                        str(evaluation.selected_project_file)
                        if evaluation.selected_project_file
                        else None
                    ),
                    "evaluation_report": str(evaluation.report_file),
                    "run_manifest": str(design_result.manifest_file),
                    "resource_usage": str(design_result.resource_file),
                }
            )
            print(
                f"\n[批次结果] {entry.model_id}: "
                f"{evaluation.score:.3f}/{evaluation.max_score:.0f}, "
                f"选中候选 {evaluation.selected_iteration}"
            )
            if stop_reason == "keyboard_interrupt":
                had_execution_error = True
                report["status"] = "interrupted"
                break
        except KeyboardInterrupt:
            item["status"] = "interrupted"
            item["error"] = "KeyboardInterrupt"
            had_execution_error = True
            report["status"] = "interrupted"
            item["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            _write_batch_report(report_file, report)
            break
        except Exception as exc:
            had_execution_error = True
            item.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            (run_dir / "run_error.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"[批次错误] {entry.model_id}: {type(exc).__name__}: {exc}")
        finally:
            item["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            _write_batch_report(report_file, report)

    if report["status"] == "running":
        report["status"] = "completed_with_errors" if had_execution_error else "completed"
    scored_models = sorted(
        (
            item
            for item in report["models"]
            if isinstance(item.get("score"), (int, float))
        ),
        key=lambda item: (-float(item["score"]), int(item["position"])),
    )
    report["ranking"] = [
        {
            "rank": rank,
            "model_id": item["model_id"],
            "model_name": item.get("model_name"),
            "score": item["score"],
            "evaluation_passed": item.get("evaluation_passed"),
            "selected_iteration": item.get("selected_iteration"),
        }
        for rank, item in enumerate(scored_models, start=1)
    ]
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_batch_report(report_file, report)

    print("\n" + "=" * 72)
    print("模型批次完成")
    print("=" * 72)
    for item in report["models"]:
        score = item.get("score")
        score_text = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
        print(
            f"{item['position']:02d} {item['model_id']}: "
            f"status={item['status']}, score={score_text}, "
            f"selected={item.get('selected_iteration', '-')}"
        )
    if report["ranking"]:
        print("得分排序: " + " > ".join(
            f"{item['model_id']}({item['score']:.3f})"
            for item in report["ranking"]
        ))
    print(f"批次汇总: {report_file}")
    return 1 if had_execution_error else 0


def main() -> int:
    args = parse_args()
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations 必须大于 0")
    try:
        if args.task_batch:
            task_specs = TaskBatchSpec.load(args.task_batch).load_enabled_tasks()
        else:
            task_specs = [AntennaTaskSpec.load(args.task_spec or DEFAULT_TASK_FILE)]
    except (OSError, ValueError, TypeError) as exc:
        task_source = args.task_batch or args.task_spec or DEFAULT_TASK_FILE
        raise SystemExit(f"无法加载结构化任务 {task_source}: {exc}") from exc

    if args.model_batch:
        try:
            return run_model_batch(args, task_specs)
        except (OSError, ValueError, TypeError) as exc:
            raise SystemExit(f"无法运行模型批次 {args.model_batch}: {exc}") from exc

    model_config = cfg
    if args.model_config:
        try:
            model_config = config_from_model_yaml(cfg, args.model_config)
        except (OSError, ValueError, TypeError) as exc:
            raise SystemExit(f"无法加载模型配置 {args.model_config}: {exc}") from exc
    completed = True
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    run_root = cfg.project_dir.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    run_report: Dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "finished_at_utc": None,
        "max_iterations_per_task": args.max_iterations,
        "tasks": [],
    }
    run_report_file = run_root / "run_results.json"
    for task_position, task_spec in enumerate(task_specs, start=1):
        run_dir = run_root / f"{task_position:02d}_{task_spec.task_id}"
        runtime_config = replace(
            model_config,
            project_dir=run_dir,
            log_dir=run_dir,
            max_iterations=args.max_iterations,
            # Sequential tasks must not leave a desktop session holding an active
            # project. Saved projects remain available on disk for verification.
            aedt_keep_open=False,
        )
        task_item: Dict[str, Any] = {
            "position": task_position,
            "task_id": task_spec.task_id,
            "result_directory": str(run_dir),
            "status": "running",
        }
        run_report["tasks"].append(task_item)
        _write_run_report(run_report_file, run_report)
        try:
            design_result, eval_result = run_single_model(
                runtime_config,
                task_spec,
                args.requirements or task_spec.description,
                use_placeholder=args.use_placeholder,
            )
            completed = completed and design_result.success and eval_result.passed
            task_item.update(
                {
                    "status": "completed",
                    "design_completed": design_result.success,
                    "evaluation_passed": eval_result.passed,
                    "score": eval_result.score,
                    "max_score": eval_result.max_score,
                    "evaluation_report": str(eval_result.report_file),
                    "run_manifest": str(design_result.manifest_file),
                }
            )
        except Exception as exc:
            completed = False
            task_item.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        _write_run_report(run_report_file, run_report)

    run_report["status"] = "completed" if completed else "completed_with_errors"
    run_report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_run_report(run_report_file, run_report)

    return 0 if completed else 1


if __name__ == "__main__":
    sys.exit(main())
