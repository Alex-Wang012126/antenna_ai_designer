"""Authoritative, structured benchmark task definitions.

Natural-language text is retained for people and as a model-facing supplement, but
all validation and scoring use the structured fields in :class:`AntennaTaskSpec`.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from frameworks import get_framework


DEFAULT_TASK_FILE = Path(__file__).resolve().parent / "tasks" / "inset_patch_2p45.json"


def _mapping(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _quantity(value: Any, label: str, expected_unit: Optional[str] = None) -> Tuple[Any, str]:
    quantity = _mapping(value, label)
    if set(quantity) - {"value", "unit", "description"}:
        raise ValueError(f"{label} contains unsupported fields")
    if "value" not in quantity or "unit" not in quantity:
        raise ValueError(f"{label} requires value and unit")
    unit = quantity["unit"]
    if not isinstance(unit, str) or not unit:
        raise ValueError(f"{label}.unit must be a non-empty string")
    if expected_unit is not None and unit != expected_unit:
        raise ValueError(f"{label}.unit must be {expected_unit!r}, got {unit!r}")
    return quantity["value"], unit


@dataclass(frozen=True)
class AntennaTaskSpec:
    """Validated benchmark contract shared by the model, builder, and evaluator."""

    data: Dict[str, Any]
    source_file: Optional[Path] = None

    @classmethod
    def load(cls, path: Path | str) -> "AntennaTaskSpec":
        source = Path(path).expanduser().resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        return cls.from_mapping(raw, source_file=source)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        source_file: Optional[Path] = None,
    ) -> "AntennaTaskSpec":
        data = _mapping(values, "task spec")
        required = {
            "schema_version",
            "task_id",
            "title",
            "natural_language_description",
            "topology",
            "calibration",
            "fixed_parameters",
            "design_variables",
            "simulation_control",
            "objectives",
        }
        missing = sorted(required - set(data))
        extra = sorted(set(data) - required)
        if missing:
            raise ValueError(f"task spec missing required fields: {', '.join(missing)}")
        if extra:
            raise ValueError(f"task spec contains unknown fields: {', '.join(extra)}")
        if data["schema_version"] != 1:
            raise ValueError("only task schema_version 1 is supported")
        for name in ("task_id", "title", "natural_language_description"):
            if not isinstance(data[name], str) or not data[name].strip():
                raise ValueError(f"{name} must be a non-empty string")

        topology = _mapping(data["topology"], "topology")
        topology_id = topology.get("id")
        if not isinstance(topology_id, str) or not topology_id.strip():
            raise ValueError("topology.id must be a non-empty string")

        calibration = _mapping(data["calibration"], "calibration")
        calibration_fields = {
            "status",
            "full_score_witness_verified",
            "verification_date",
            "solver",
            "notes",
        }
        if set(calibration) != calibration_fields:
            raise ValueError(f"calibration must contain exactly {sorted(calibration_fields)}")
        if calibration["status"] not in {"uncalibrated", "verified"}:
            raise ValueError("calibration.status must be 'uncalibrated' or 'verified'")
        if not isinstance(calibration["full_score_witness_verified"], bool):
            raise ValueError("calibration.full_score_witness_verified must be boolean")
        witness_verified = calibration["full_score_witness_verified"]
        if (calibration["status"] == "verified") != witness_verified:
            raise ValueError(
                "calibration.status may be 'verified' exactly when a full-score witness is verified"
            )
        for name in ("verification_date", "solver"):
            value = calibration[name]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"calibration.{name} must be null or a non-empty string")
            if witness_verified and value is None:
                raise ValueError(f"verified calibration requires calibration.{name}")
        if not isinstance(calibration["notes"], str) or not calibration["notes"].strip():
            raise ValueError("calibration.notes must be a non-empty string")

        objectives = _mapping(data["objectives"], "objectives")
        if not objectives:
            raise ValueError("objectives must not be empty")
        total_points = 0.0
        for objective_id, objective_value in objectives.items():
            objective = _mapping(objective_value, f"objectives.{objective_id}")
            required_objective = {
                "metric_key",
                "display_name",
                "unit",
                "value_representation",
                "goal",
                "points",
                "semantics",
            }
            if not required_objective.issubset(objective):
                missing_objective = sorted(required_objective - set(objective))
                raise ValueError(
                    f"objective {objective_id} missing fields: {', '.join(missing_objective)}"
                )
            if objective["goal"] not in {"target_with_tolerance", "at_most", "at_least"}:
                raise ValueError(f"unsupported goal for objective {objective_id}")
            points = _finite_number(objective["points"], f"objectives.{objective_id}.points")
            if points <= 0:
                raise ValueError(f"objective {objective_id} points must be positive")
            total_points += points
            cls._validate_objective_thresholds(objective_id, objective)
        if abs(total_points - 100.0) > 1e-9:
            raise ValueError(f"objective points must sum to 100, got {total_points}")
        framework = get_framework(topology_id)
        framework.validate_task(data)
        framework.validate_objectives(data["objectives"], data["simulation_control"])

        return cls(copy.deepcopy(data), source_file=source_file)

    @staticmethod
    def _validate_objective_thresholds(objective_id: str, objective: Dict[str, Any]) -> None:
        unit = objective["unit"]
        goal = objective["goal"]
        if goal == "target_with_tolerance":
            names = ("target", "tolerance", "zero_score_tolerance")
        else:
            names = ("pass_threshold", "zero_score_threshold")
        for name in names:
            value, _ = _quantity(objective.get(name), f"objectives.{objective_id}.{name}", unit)
            _finite_number(value, f"objectives.{objective_id}.{name}.value")
        if goal == "target_with_tolerance":
            tolerance = float(objective["tolerance"]["value"])
            zero_tolerance = float(objective["zero_score_tolerance"]["value"])
            if tolerance < 0 or zero_tolerance <= tolerance:
                raise ValueError(
                    f"objective {objective_id} requires 0 <= tolerance < zero_score_tolerance"
                )
        elif goal == "at_most":
            threshold = float(objective["pass_threshold"]["value"])
            zero = float(objective["zero_score_threshold"]["value"])
            if zero <= threshold:
                raise ValueError(
                    f"objective {objective_id} at_most requires zero_score_threshold > pass_threshold"
                )
        elif goal == "at_least":
            threshold = float(objective["pass_threshold"]["value"])
            zero = float(objective["zero_score_threshold"]["value"])
            if zero >= threshold:
                raise ValueError(
                    f"objective {objective_id} at_least requires zero_score_threshold < pass_threshold"
                )

    @property
    def task_id(self) -> str:
        return self.data["task_id"]

    @property
    def description(self) -> str:
        return self.data["natural_language_description"]

    def to_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self.data)

    def write(self, path: Path | str) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return destination

    def fixed_value(self, name: str, expected_unit: str) -> Any:
        value, _ = _quantity(
            self.data["fixed_parameters"][name], f"fixed_parameters.{name}", expected_unit
        )
        return value

    def simulation_value(self, name: str, expected_unit: str) -> float:
        value, _ = _quantity(
            self.data["simulation_control"][name],
            f"simulation_control.{name}",
            expected_unit,
        )
        return float(value)

    def setup_value(self, name: str, expected_unit: str) -> float:
        value, _ = _quantity(
            self.data["simulation_control"]["setup"][name],
            f"simulation_control.setup.{name}",
            expected_unit,
        )
        return float(value)

    def efficiency_sweep_value(self, name: str, expected_unit: str) -> float:
        value, _ = _quantity(
            self.data["simulation_control"]["efficiency_sweep"][name],
            f"simulation_control.efficiency_sweep.{name}",
            expected_unit,
        )
        return float(value)

    def objective_value(self, objective_id: str, name: str, expected_unit: str) -> float:
        value, _ = _quantity(
            self.data["objectives"][objective_id][name],
            f"objectives.{objective_id}.{name}",
            expected_unit,
        )
        return float(value)

    def far_field_value(self, name: str) -> float:
        value, _ = _quantity(
            self.data["simulation_control"]["far_field"][name],
            f"simulation_control.far_field.{name}",
            "deg",
        )
        return float(value)

    def tool_properties(self) -> Dict[str, Dict[str, Any]]:
        properties: Dict[str, Dict[str, Any]] = {}
        for name, definition in self.data["design_variables"].items():
            properties[name] = {
                "type": "number",
                "minimum": definition["minimum"],
                "maximum": definition["maximum"],
                "description": f"{definition['description']} Unit: {definition['unit']}.",
            }
        return properties

    def validate_design(self, values: Mapping[str, Any]):
        return self.framework.validate_design(values, self.data)

    def resolve_design_spec(self, values: Mapping[str, Any]):
        return self.framework.resolve_design_spec(self, values)

    def resolve_patch_spec(self, values: Mapping[str, Any]):
        return self.resolve_design_spec(values)

    @property
    def topology_id(self) -> str:
        return str(self.data["topology"]["id"])

    @property
    def framework(self):
        return get_framework(self.topology_id)

    @property
    def design_tool_name(self) -> str:
        return self.framework.tool_name

    def create_candidate(self, hfss_client: Any, values: Mapping[str, Any]):
        return self.framework.create_candidate(hfss_client, values)

    def structured_design(self, values: Mapping[str, Any]) -> Dict[str, Any]:
        design = self.validate_design(values)
        return {
            name: {
                "value": value,
                "unit": self.data["design_variables"][name]["unit"],
                "description": self.data["design_variables"][name]["description"],
            }
            for name, value in design.to_dict().items()
        }

    def structured_metrics(self, raw_metrics: Mapping[str, Any]) -> Dict[str, Any]:
        structured: Dict[str, Any] = {}
        for objective_id, objective in self.data["objectives"].items():
            value = raw_metrics.get(objective["metric_key"])
            structured[objective_id] = {
                "metric_key": objective["metric_key"],
                "value": value,
                "unit": objective["unit"],
                "value_representation": objective["value_representation"],
                "semantics": objective["semantics"],
            }
        return structured

    def evaluate_metrics(self, raw_metrics: Mapping[str, Any]) -> Dict[str, Any]:
        objective_evaluations: Dict[str, Any] = {}
        total_score = 0.0
        checklist: Dict[str, bool] = {}
        for objective_id, objective in self.data["objectives"].items():
            raw_value = raw_metrics.get(objective["metric_key"])
            passed = False
            fraction = 0.0
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                value = float(raw_value)
                if math.isfinite(value):
                    goal = objective["goal"]
                    if goal == "target_with_tolerance":
                        target = float(objective["target"]["value"])
                        tolerance = float(objective["tolerance"]["value"])
                        zero_tolerance = float(objective["zero_score_tolerance"]["value"])
                        error = abs(value - target)
                        passed = error <= tolerance
                        if error <= tolerance:
                            fraction = 1.0
                        elif error < zero_tolerance:
                            fraction = (zero_tolerance - error) / (zero_tolerance - tolerance)
                    elif goal == "at_most":
                        threshold = float(objective["pass_threshold"]["value"])
                        zero = float(objective["zero_score_threshold"]["value"])
                        passed = value <= threshold
                        if passed:
                            fraction = 1.0
                        elif value < zero:
                            fraction = (zero - value) / (zero - threshold)
                    elif goal == "at_least":
                        threshold = float(objective["pass_threshold"]["value"])
                        zero = float(objective["zero_score_threshold"]["value"])
                        passed = value >= threshold
                        if passed:
                            fraction = 1.0
                        elif value > zero:
                            fraction = (value - zero) / (threshold - zero)
            fraction = max(0.0, min(1.0, fraction))
            points_awarded = fraction * float(objective["points"])
            total_score += points_awarded
            checklist[objective_id] = passed
            objective_evaluations[objective_id] = {
                "measurement": {
                    "value": raw_value,
                    "unit": objective["unit"],
                    "value_representation": objective["value_representation"],
                },
                "goal": objective["goal"],
                "passed": passed,
                "points_awarded": round(points_awarded, 6),
                "points_available": objective["points"],
                "semantics": objective["semantics"],
            }
        return {
            "passed": all(checklist.values()),
            "score": round(total_score, 6),
            "max_score": 100.0,
            "checklist": checklist,
            "objectives": objective_evaluations,
        }


def load_default_task() -> AntennaTaskSpec:
    return AntennaTaskSpec.load(DEFAULT_TASK_FILE)
