"""System prompt and strict tool schemas for the antenna design agent."""

import json
from typing import Any, Dict, List

from design_spec import PatchAntennaDesign
from task_spec import AntennaTaskSpec, load_default_task


def build_system_prompt(task_spec: AntennaTaskSpec) -> str:
    """Build the operating contract given to the antenna design model."""
    task_json = json.dumps(task_spec.to_dict(), ensure_ascii=False, indent=2)
    return f"""You are an expert antenna engineer controlling Ansys HFSS through a constrained tool API.
The structured benchmark contract below is authoritative. Its fixed_parameters, simulation_control,
objective definitions, units, and semantics are read-only. The natural-language description is only a
human-readable supplement and never overrides a structured value.

AUTHORITATIVE_BENCHMARK_TASK_JSON:
{task_json}

Supported topology:
- An inset-fed rectangular microstrip patch with one lumped port.
- All geometry, boundaries, setup, sweep, and far-field configuration are created by trusted backend code.
- You choose only the fields exposed by `create_patch_antenna`. Material and simulation controls are fixed.
- Never output Python, PyAEDT calls, AEDT macros, file paths, read-only fields, or units inside numeric values.

Strict workflow:
1. Call `create_patch_antenna` with every required field. Numeric field names state their units.
2. The Python backend automatically builds, validates, solves, reads all metrics, and saves that candidate.
3. Review design_parameters and measured_metrics returned by the tool. Each measurement states its unit
   and value representation. If the objectives are not met and the
   candidate budget remains, call `create_patch_antenna` again with a complete revised specification.
4. As soon as you believe the current candidate satisfies the requirements, call `finalize_design`.
5. Do not call `finalize_design` before at least one candidate has completed the full simulation pipeline.

Protocol rules:
- Make exactly one tool call in each response and wait for its result before deciding the next action.
- Tool arguments must be one JSON object matching the provided schema exactly. Do not add fields.
- Use finite JSON numbers, not strings, for all GHz/mm/material-property fields.
- Never report an operation as successful unless the latest tool result has success=true.
- Treat build, validation, solve, metric, and save failures as a failed candidate. Diagnose the returned
  error and submit a corrected candidate when budget remains; never repeat the same failed candidate.
- Base the final summary only on measured values in candidate tool results; do not invent metrics.
- Every candidate that completes the full simulation pipeline is scored independently after the agent exits.
  The highest-scoring successful candidate is selected; an exact tie goes to the later iteration.
  Failed build, validation, solve, or metric pipelines are excluded from selection.
- Each candidate response includes remaining_design_iterations and remaining_solve_calls.
- `finalize_design` stops candidate generation. It does not decide whether the engineering requirements
  pass; an independent evaluator makes that decision after the agent exits.
"""


def _strict_tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def build_tools_description(task_spec: AntennaTaskSpec | None = None) -> List[Dict[str, Any]]:
    """Return the only operations the model is allowed to request."""
    task_spec = task_spec or load_default_task()
    return [
        _strict_tool(
            "create_patch_antenna",
            (
                "Submit one complete inset-fed patch candidate. Python automatically builds, validates, "
                "solves, reads all metrics, and saves an isolated project copy before returning."
            ),
            task_spec.tool_properties(),
            list(PatchAntennaDesign.FIELD_NAMES),
        ),
        _strict_tool(
            "finalize_design",
            (
                "Stop generating candidates. The independent evaluator will score every successfully "
                "simulated candidate and select the highest-scoring one."
            ),
            {
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": "Concise design summary using only measured tool results.",
                }
            },
            ["summary"],
        ),
    ]


def build_initial_message(
    task_spec: AntennaTaskSpec,
    natural_language_supplement: str | None = None,
) -> Dict[str, str]:
    """Construct the initial user message that starts the design workflow."""
    supplement = natural_language_supplement or task_spec.description
    return {
        "role": "user",
        "content": (
            f"Human-readable task description (non-authoritative supplement):\n{supplement}\n\n"
            "Begin with exactly one create_patch_antenna tool call containing a complete specification."
        ),
    }
