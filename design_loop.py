"""多轮设计主循环

核心流程：
1. 初始化 System Prompt + 用户需求；
2. 每轮调用模型，获取工具调用；
3. 在 HFSS 客户端上执行工具；
4. 将结果反馈给模型；
5. 遇到 finalize_design 或达到最大轮次时结束；
6. 保存完整对话日志。
"""

import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import Config, cfg
from hfss_client import HFSSClient, HFSSResult
from model_client import ChatResponse, ModelClient, ToolCall
from prompts import build_initial_message, build_system_prompt, build_tools_description


@dataclass
class DesignLoopResult:
    """设计循环结果。"""
    success: bool
    final_summary: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    rounds_used: int = 0
    log_file: Optional[Path] = None
    metrics_file: Optional[Path] = None


class DesignAgent:
    """天线设计 Agent：驱动模型与 HFSS 多轮交互。"""

    def __init__(
        self,
        model_client: ModelClient,
        hfss_client: HFSSClient,
        requirements: str,
        config: Config = cfg,
    ):
        self.model = model_client
        self.hfss = hfss_client
        self.config = config
        self.requirements = requirements
        self.messages: List[Dict[str, Any]] = []
        self.rounds_used = 0
        self._finalized = False
        self._solve_count = 0

    @property
    def remaining_solve_calls(self) -> int:
        return max(0, self.config.max_solve_calls - self._solve_count)

    def _append_tool_result(self, tool_call_id: str, name: str, result: HFSSResult) -> None:
        content = json.dumps(
            {
                "success": result.success,
                "data": result.data,
                "message": result.message,
                # 让模型随时知道剩余仿真预算，便于规划迭代策略
                "remaining_solve_calls": self.remaining_solve_calls,
            },
            ensure_ascii=False,
            default=str,
        )
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        })

    def _execute_tool(self, tool_call: ToolCall) -> HFSSResult:
        name = tool_call.name
        args = tool_call.arguments

        if name == "set_variable":
            return self.hfss.set_design_variable(args["name"], args["value"])

        if name == "update_geometry":
            return self.hfss.update_geometry(args["script"])

        if name == "solve":
            if self._solve_count >= self.config.max_solve_calls:
                return HFSSResult(
                    success=False,
                    message=(
                        f"仿真次数预算已用完（共 {self.config.max_solve_calls} 次），"
                        "请基于已有结果调用 finalize_design。"
                    ),
                )
            self._solve_count += 1
            return self.hfss.solve(args.get("solution_name"))

        if name == "get_result":
            return self.hfss.get_result(args["report_name"], args["solution_name"])

        if name == "export_design":
            return self.hfss.export_design(args["file_path"])

        if name == "finalize_design":
            self._finalized = True
            return HFSSResult(
                success=True,
                data={"summary": args.get("summary", "")},
                message="Design finalized.",
            )

        return HFSSResult(success=False, message=f"Unknown tool: {name}")

    def _build_assistant_message(self, response: ChatResponse) -> Dict[str, Any]:
        """把模型返回的 ChatResponse 转成可保存/可回传的 assistant 消息。"""
        msg: Dict[str, Any] = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": f"call_{self.rounds_used}_{i}",   # 全局唯一，避免多轮重复
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for i, tc in enumerate(response.tool_calls)
            ]
        return msg

    def run(self, max_rounds: Optional[int] = None) -> DesignLoopResult:
        if max_rounds is None:
            max_rounds = self.config.max_design_rounds

        self.config.ensure_dirs()
        conn = self.hfss.connect(self.config)
        if not conn.success:
            raise RuntimeError(f"无法连接 HFSS/AEDT: {conn.message}")

        # 初始化对话
        self.messages = [
            {"role": "system", "content": build_system_prompt(self.requirements)},
            build_initial_message(self.requirements),
        ]

        final_summary = ""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        metrics_file: Optional[Path] = None

        try:
            while self.rounds_used < max_rounds and not self._finalized:
                # 调用模型；API 报错或工具参数 JSON 被 max_tokens 截断时，
                # 记录原因并跳出循环，对话日志仍会在 finally 中落盘
                tools = build_tools_description()
                try:
                    response = self.model.chat(self.messages, tools=tools)
                except Exception as exc:
                    final_summary = (
                        f"模型调用失败（{type(exc).__name__}: {exc}），设计循环提前终止。"
                    )
                    print(f"\n[模型错误] {final_summary}")
                    break

                # 记录模型回复
                assistant_msg = self._build_assistant_message(response)
                self.messages.append(assistant_msg)

                # 如果没有工具调用，视为模型已自然结束
                if not response.tool_calls:
                    final_summary = response.content or "模型未返回工具调用，设计结束。"
                    break

                # 逐个执行工具调用
                for i, tool_call in enumerate(response.tool_calls):
                    tc_id = f"call_{self.rounds_used}_{i}"

                    # finalize 之后的调用不再执行，但仍需补回执，
                    # 否则下一轮请求会因"悬空 tool_call"被真实 API 拒绝
                    if self._finalized:
                        self._append_tool_result(
                            tc_id,
                            tool_call.name,
                            HFSSResult(success=False, message="Skipped: design already finalized."),
                        )
                        continue

                    print(f"\n[Round {self.rounds_used + 1}] Tool call: {tool_call.name}({tool_call.arguments})")
                    try:
                        result = self._execute_tool(tool_call)
                    except Exception as exc:
                        error_msg = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                        result = HFSSResult(success=False, message=error_msg)
                        print(f"[HFSS 错误] {error_msg}")

                    self._append_tool_result(tc_id, tool_call.name, result)

                    if tool_call.name == "finalize_design" and result.success:
                        final_summary = result.data.get("summary", "")

                self.rounds_used += 1

            if not self._finalized and self.rounds_used >= max_rounds:
                final_summary = f"达到最大轮次 ({max_rounds})，强制结束。"

            # 断开连接前把指标落盘，评测脚本离线读取该文件即可，
            # 无需为评测第二次拉起 AEDT
            try:
                m = self.hfss.get_metrics()
                if m.success and m.data:
                    metrics_file = self.config.log_dir / f"metrics_{timestamp}.json"
                    metrics_file.write_text(
                        json.dumps(m.data, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8",
                    )
                    print(f"\n[指标已保存] {metrics_file}")
                else:
                    print(f"\n[指标读取失败] {m.message}")
            except Exception as exc:
                print(f"\n[指标读取异常] {type(exc).__name__}: {exc}")

        finally:
            try:
                self.hfss.disconnect()
            except Exception as exc:
                print(f"\n[断开连接异常] {type(exc).__name__}: {exc}")

            # 保存日志（放在 finally 中：模型调用崩溃等异常路径下日志也不丢）
            log_file = self.config.log_dir / f"design_log_{timestamp}.json"
            log_file.write_text(
                json.dumps(
                    {
                        "requirements": self.requirements,
                        "final_summary": final_summary,
                        "rounds_used": self.rounds_used,
                        "messages": self.messages,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            print(f"\n[日志已保存] {log_file}")

        return DesignLoopResult(
            success=self._finalized,
            final_summary=final_summary,
            messages=self.messages,
            rounds_used=self.rounds_used,
            log_file=log_file,
            metrics_file=metrics_file,
        )
