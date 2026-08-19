"""入口脚本

用法示例（在本目录下运行）：
    python main.py \
        --requirements "设计一个中心频率 2.45 GHz 的微带贴片天线，S11 <-10 dB，带宽 > 100 MHz" \
        --max-rounds 10

默认使用真实模型 + 真实 AEDT；加 --use-placeholder 可切换到占位演示模式。

运行后会：
1. 启动设计循环（模型多次调用 HFSS API）；
2. 保存完整对话日志；
3. 调用评测脚本读取/计算指标并生成报告。
"""
import os
# 学生版 2025 R2 必须：强制使用 legacy "-grpcsrv <port>" 启动方式（官方 workaround）
os.environ["PYAEDT_USE_PRE_GRPC_ARGS"] = "True"
# 正确的学生版安装路径变量名（原来写的是 ANSYSEM_ROOTSV252，顺序反了）
os.environ["ANSYSEMSV_ROOT252"] = r"C:\ANSYS Inc\ANSYS Student\v252\AnsysEM"

import argparse
import sys

from config import cfg
from design_loop import DesignAgent
from evaluator import AntennaEvaluator
from hfss_client import PlaceholderHFSSClient, PyAEDTHFSSClient
from model_client import ResponsesModelClient, PlaceholderModelClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI-driven antenna design with HFSS")
    parser.add_argument(
        "--requirements",
        type=str,
        default="设计一个中心频率 2.45 GHz 的微带贴片天线，S11 < -10 dB，-10 dB 带宽 > 100 MHz，峰值增益 > 3 dBi。",
        help="天线设计需求描述",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=cfg.max_design_rounds,
        help="最大设计轮次",
    )
    parser.add_argument(
        "--use-placeholder",
        action="store_true",
        help="使用占位模型和占位 HFSS 演示流程（默认关闭：默认使用真实模型 + 真实 AEDT）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    mode = "占位模式" if args.use_placeholder else "真实模式（模型 + AEDT）"
    print("=" * 60)
    print(f"天线 AI 设计工具 [{mode}]")
    print("=" * 60)
    print(f"需求: {args.requirements}")
    print(f"仿真次数预算: {cfg.max_solve_calls}\n")

    if args.use_placeholder:
        model_client = PlaceholderModelClient()
        hfss_client = PlaceholderHFSSClient()
    else:
        # 真实模式：先回显关键配置，连不上/调不通时方便排查
        print(f"模型: {cfg.model_name} @ {cfg.model_base_url}")
        key = cfg.model_api_key
        print(f"模型 API key: {'已配置（尾号 ' + key[-4:] + '）' if key else '未配置！请设置 MODEL_API_KEY 或在 job yaml 中提供'}")
        print(f"AEDT: version={cfg.aedt_version}, student={cfg.aedt_student}, "
              f"non_graphical={cfg.aedt_non_graphical}")
        print(f"项目目录: {cfg.project_dir.resolve()}\n")
        model_client = ResponsesModelClient(cfg)
        hfss_client = PyAEDTHFSSClient()

    # 阶段 1：设计循环
    agent = DesignAgent(
        model_client=model_client,
        hfss_client=hfss_client,
        requirements=args.requirements,
        config=cfg,
    )
    design_result = agent.run(max_rounds=args.max_rounds)

    print("\n" + "=" * 60)
    print("设计结果")
    print("=" * 60)
    print(f"是否完成: {design_result.success}")
    print(f"使用轮次: {design_result.rounds_used}")
    print(f"最终总结:\n{design_result.final_summary}")
    print(f"日志文件: {design_result.log_file}")
    print(f"指标文件: {design_result.metrics_file}")

    # 阶段 2：评测（离线读取设计循环落盘的指标文件，无需重连 AEDT）
    evaluator = AntennaEvaluator(config=cfg)
    eval_result = evaluator.evaluate(
        requirements=args.requirements,
        design_file=None,
        metrics_file=design_result.metrics_file,
    )

    print("\n" + "=" * 60)
    print("评测结果")
    print("=" * 60)
    print(eval_result.summary)

    return 0 if design_result.success and eval_result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
