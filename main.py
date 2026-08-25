"""入口脚本

用法示例（在本目录下运行）：
    python main.py \
        --task-spec tasks/inset_patch_2p45.json \
        --max-design-iterations 10

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
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from config import cfg
from design_loop import DesignAgent
from evaluator import AntennaEvaluator
from hfss_client import PlaceholderHFSSClient, PyAEDTHFSSClient
from model_client import ResponsesModelClient, PlaceholderModelClient
from task_spec import AntennaTaskSpec, DEFAULT_TASK_FILE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI-driven antenna design with HFSS")
    parser.add_argument(
        "--task-spec",
        type=str,
        default=str(DEFAULT_TASK_FILE),
        help="结构化评测任务 JSON；它是参数、单位、仿真控制和评分的唯一权威来源",
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
        "--max-design-iterations",
        "--max-rounds",
        dest="max_design_iterations",
        type=int,
        default=cfg.max_design_iterations,
        help="最多生成并完整仿真的候选设计数量（--max-rounds 为兼容别名）",
    )
    parser.add_argument(
        "--max-solve-calls",
        type=int,
        default=cfg.max_solve_calls,
        help="最大 HFSS 求解次数",
    )
    parser.add_argument(
        "--use-placeholder",
        action="store_true",
        help="使用占位模型和占位 HFSS 演示流程（默认关闭：默认使用真实模型 + 真实 AEDT）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_design_iterations < 1 or args.max_solve_calls < 1:
        raise SystemExit("--max-design-iterations 和 --max-solve-calls 必须大于 0")
    try:
        task_spec = AntennaTaskSpec.load(args.task_spec)
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"无法加载结构化任务 {args.task_spec}: {exc}") from exc
    natural_language_supplement = args.requirements or task_spec.description

    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    run_dir = cfg.project_dir.expanduser().resolve() / run_id
    runtime_config = replace(
        cfg,
        project_dir=run_dir,
        log_dir=run_dir,
        max_design_iterations=args.max_design_iterations,
        max_solve_calls=args.max_solve_calls,
    )

    mode = "占位模式" if args.use_placeholder else "真实模式（模型 + AEDT）"
    print("=" * 60)
    print(f"天线 AI 设计工具 [{mode}]")
    print("=" * 60)
    print(f"结构化任务: {task_spec.task_id} ({Path(args.task_spec).resolve()})")
    calibration_status = task_spec.data["calibration"]["status"]
    print(f"任务校准状态: {calibration_status}")
    if calibration_status != "verified":
        print("警告: 该任务尚未验证存在满分见证解，不应直接用于正式排行榜。")
    print(f"自然语言说明: {natural_language_supplement}")
    print(f"候选设计迭代上限: {args.max_design_iterations}")
    print(f"仿真次数预算: {runtime_config.max_solve_calls}\n")

    if args.use_placeholder:
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
        model_client = ResponsesModelClient(runtime_config)
        hfss_client = PyAEDTHFSSClient()

    # 阶段 1：设计循环
    agent = DesignAgent(
        model_client=model_client,
        hfss_client=hfss_client,
        requirements=natural_language_supplement,
        config=runtime_config,
        task_spec=task_spec,
    )
    design_result = agent.run(max_iterations=args.max_design_iterations)

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

    return 0 if design_result.success and eval_result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
