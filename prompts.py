"""提示词与工具描述

所有给模型的提示词、工具说明都集中在这里，方便统一维护。
"""

from typing import Any, Dict, List


def build_system_prompt(requirements: str) -> str:
    """根据用户需求生成系统提示词。"""
    return f"""You are an expert antenna engineer and HFSS scripting assistant.
Your goal is to design an antenna in Ansys HFSS that satisfies the following requirements:

{requirements}

You can interact with HFSS through the provided tools. At each step, decide which tool(s) to call.
After each tool call, you will receive the result/observation from HFSS.
Design iteratively: adjust geometry/material/variables, solve, read results, and refine.
When you are satisfied with the design, call `finalize_design` with a short summary and the final metrics.

Environment (read carefully — most mistakes happen here):
- `update_geometry` executes a Python snippet via PyAEDT. A variable `hfss` is in scope:
  an `ansys.aedt.core.Hfss` object already connected to your project.
  Use the PyAEDT API, e.g.:
    hfss.modeler.create_box(origin=["-W/2", "-L/2", "0mm"], sizes=["W", "L", "1.6mm"], name="Substrate", material="FR4_epoxy")
    hfss.create_setup("Setup1")
  Do NOT use the AEDT record-script style (oEditor.CreateRectangle / oDesign...) — it will fail.
- Fixed naming convention (do not invent other names):
  solution setup: "Setup1"; frequency sweep: "Sweep1"; wave port: "Port1".
  When reading results use solution name "Setup1 : Sweep1" and expression dB(S(1,1)).
- To read gain / radiation efficiency, call `get_result` with a report name mentioning
  gain or efficiency (e.g. "Gain Plot"); the result returns peak_gain_dbi and
  radiation_efficiency_percent.
- `solve` calls are budget-limited. Every tool result reports `remaining_solve_calls`;
  plan your iterations so you stay within budget.
- Call `export_design` to save the project before `finalize_design`.

Rules:
1. Always use SI units or specify units explicitly (e.g. "28.5mm", "2.45GHz").
2. Keep changes minimal and explain why you make each change.
3. If a simulation or script fails, read the error message in the tool result and fix the issue.
4. Do not invent values that you can read from HFSS; prefer using `get_result`.
5. After finalize_design, no more tool calls are allowed.
"""


def build_tools_description() -> List[Dict[str, Any]]:
    """返回 OpenAI 风格（JSON Schema）的工具描述列表。

    TODO：如果后续模型接口不支持 function calling schema，
    可改成纯文本形式嵌入 system prompt 中。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "set_variable",
                "description": "Set or update a design variable in HFSS.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Variable name, e.g. 'patch_length'"},
                        "value": {"type": "string", "description": "Variable value, e.g. '28.5mm'"},
                    },
                    "required": ["name", "value"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_geometry",
                "description": "Execute a Python script snippet to modify geometry, materials, boundaries, or excitations in HFSS.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {"type": "string", "description": "HFSS/AEDT Python script snippet"},
                    },
                    "required": ["script"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "solve",
                "description": "Run the current HFSS simulation (budget-limited).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "solution_name": {
                            "type": "string",
                            "description": "Optional setup name to analyze, default 'Setup1'",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_result",
                "description": "Read a simulation result report. Returns the S11 curve by default; if the report name mentions gain/efficiency/far-field, returns peak_gain_dbi and radiation_efficiency_percent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "report_name": {"type": "string", "description": "Name of the report to read, e.g. 'S Parameter Plot 1'"},
                        "solution_name": {"type": "string", "description": "Solution name, e.g. 'Setup1 : Sweep1'"},
                    },
                    "required": ["report_name", "solution_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "export_design",
                "description": "Export/save the HFSS design file to a specific path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Destination path for the design file"},
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finalize_design",
                "description": "Finish the design loop and provide the final summary.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Short summary of final design and metrics"},
                    },
                    "required": ["summary"],
                },
            },
        },
    ]


def build_initial_message(requirements: str) -> Dict[str, str]:
    """构造初始 user 消息，触发模型开始设计。"""
    return {
        "role": "user",
        "content": (
            f"Please design an antenna satisfying these requirements:\n{requirements}\n\n"
            "Start by creating geometry/variables. Report back each step using tools."
        ),
    }
