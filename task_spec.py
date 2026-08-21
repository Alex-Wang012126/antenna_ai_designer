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
        if topology.get("id") != "inset_fed_rectangular_patch":
            raise ValueError(
                "this backend currently supports topology.id='inset_fed_rectangular_patch' only"
            )
        if topology.get("port_count") != 1:
            raise ValueError("the inset-fed patch builder requires exactly one port")

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

        fixed = _mapping(data["fixed_parameters"], "fixed_parameters")
        fixed_units = {
            "substrate_material": "label",
            "substrate_relative_permittivity": "1",
            "substrate_loss_tangent": "1",
        }
        if set(fixed) != set(fixed_units):
            raise ValueError(f"fixed_parameters must contain exactly {sorted(fixed_units)}")
        for name, unit in fixed_units.items():
            value, _ = _quantity(fixed[name], f"fixed_parameters.{name}", unit)
            if name == "substrate_material":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("substrate_material.value must be a non-empty string")
            else:
                _finite_number(value, f"fixed_parameters.{name}.value")

        from design_spec import PatchAntennaDesign

        design_variables = _mapping(data["design_variables"], "design_variables")
        if set(design_variables) != set(PatchAntennaDesign.FIELD_NAMES):
            raise ValueError(
                f"design_variables must contain exactly {sorted(PatchAntennaDesign.FIELD_NAMES)}"
            )
        for name, definition_value in design_variables.items():
            definition = _mapping(definition_value, f"design_variables.{name}")
            if set(definition) - {"unit", "minimum", "maximum", "description"}:
                raise ValueError(f"design_variables.{name} contains unsupported fields")
            if definition.get("unit") != "mm":
                raise ValueError(f"design_variables.{name}.unit must be 'mm'")
            minimum = _finite_number(definition.get("minimum"), f"{name}.minimum")
            maximum = _finite_number(definition.get("maximum"), f"{name}.maximum")
            if minimum <= 0 or minimum >= maximum:
                raise ValueError(f"invalid bounds for design variable {name}")

        simulation = _mapping(data["simulation_control"], "simulation_control")
        cls._validate_simulation_control(simulation)

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
        cls._validate_efficiency_sweep_coverage(simulation, objectives)

        return cls(copy.deepcopy(data), source_file=source_file)

    @staticmethod
    def _validate_simulation_control(simulation: Dict[str, Any]) -> None:
        quantity_units = {
            "adaptive_frequency": "GHz",
            "sweep_start": "GHz",
            "sweep_stop": "GHz",
            "sweep_points": "count",
            "bandwidth_s11_threshold": "dB",
            "total_efficiency_window_half_span": "MHz",
            "air_margin_xy": "mm",
            "air_above": "mm",
            "air_below": "mm",
            "port_impedance": "ohm",
        }
        required = set(quantity_units) | {
            "sweep_type",
            "save_fields",
            "save_rad_fields",
            "efficiency_sweep",
            "setup",
            "far_field",
        }
        if set(simulation) != required:
            raise ValueError(f"simulation_control must contain exactly {sorted(required)}")
        parsed: Dict[str, float] = {}
        for name, unit in quantity_units.items():
            value, _ = _quantity(simulation[name], f"simulation_control.{name}", unit)
            parsed[name] = _finite_number(value, f"simulation_control.{name}.value")
        if not parsed["sweep_start"] < parsed["adaptive_frequency"] < parsed["sweep_stop"]:
            raise ValueError("adaptive_frequency must lie strictly inside the fixed sweep")
        if not float(parsed["sweep_points"]).is_integer() or not 11 <= parsed["sweep_points"] <= 2001:
            raise ValueError("sweep_points must be an integer from 11 to 2001")
        sweep_span_mhz = (parsed["sweep_stop"] - parsed["sweep_start"]) * 1000.0
        efficiency_half_span = parsed["total_efficiency_window_half_span"]
        if efficiency_half_span <= 0 or 2.0 * efficiency_half_span > sweep_span_mhz:
            raise ValueError("total-efficiency window must be positive and fit inside the sweep span")
        if simulation["sweep_type"] != "Interpolating":
            raise ValueError("only the Interpolating sweep type is currently supported")
        for name in ("save_fields", "save_rad_fields"):
            if not isinstance(simulation[name], bool):
                raise ValueError(f"simulation_control.{name} must be boolean")

        efficiency_sweep = _mapping(
            simulation["efficiency_sweep"], "simulation_control.efficiency_sweep"
        )
        efficiency_fields = {
            "name",
            "start",
            "stop",
            "points",
            "sweep_type",
            "save_fields",
            "save_rad_fields",
        }
        if set(efficiency_sweep) != efficiency_fields:
            raise ValueError(
                "simulation_control.efficiency_sweep must contain exactly "
                f"{sorted(efficiency_fields)}"
            )
        if efficiency_sweep["name"] != "EfficiencySweep":
            raise ValueError("simulation_control.efficiency_sweep.name must be 'EfficiencySweep'")
        efficiency_start, _ = _quantity(
            efficiency_sweep["start"], "simulation_control.efficiency_sweep.start", "GHz"
        )
        efficiency_stop, _ = _quantity(
            efficiency_sweep["stop"], "simulation_control.efficiency_sweep.stop", "GHz"
        )
        efficiency_points, _ = _quantity(
            efficiency_sweep["points"], "simulation_control.efficiency_sweep.points", "count"
        )
        efficiency_start = _finite_number(
            efficiency_start, "simulation_control.efficiency_sweep.start.value"
        )
        efficiency_stop = _finite_number(
            efficiency_stop, "simulation_control.efficiency_sweep.stop.value"
        )
        efficiency_points = _finite_number(
            efficiency_points, "simulation_control.efficiency_sweep.points.value"
        )
        if not parsed["sweep_start"] <= efficiency_start < efficiency_stop <= parsed["sweep_stop"]:
            raise ValueError("efficiency_sweep must lie inside the main evaluation sweep")
        if not efficiency_points.is_integer() or not 3 <= efficiency_points <= 201:
            raise ValueError("efficiency_sweep.points must be an integer from 3 to 201")
        if efficiency_sweep["sweep_type"] != "Discrete":
            raise ValueError("efficiency_sweep.sweep_type must be 'Discrete'")
        for name in ("save_fields", "save_rad_fields"):
            if not isinstance(efficiency_sweep[name], bool):
                raise ValueError(f"simulation_control.efficiency_sweep.{name} must be boolean")
        if not efficiency_sweep["save_fields"]:
            raise ValueError(
                "efficiency_sweep.save_fields must be true because AEDT requires saved "
                "discrete solution fields to evaluate swept antenna parameters"
            )
        if not efficiency_sweep["save_rad_fields"]:
            raise ValueError(
                "efficiency_sweep.save_rad_fields must be true because RadiationEfficiency "
                "requires discrete swept radiation data"
            )

        setup = _mapping(simulation["setup"], "simulation_control.setup")
        setup_units = {
            "max_delta_s": "1",
            "maximum_passes": "count",
            "minimum_passes": "count",
            "minimum_converged_passes": "count",
        }
        if set(setup) != set(setup_units):
            raise ValueError(f"simulation_control.setup must contain exactly {sorted(setup_units)}")
        for name, unit in setup_units.items():
            value, _ = _quantity(setup[name], f"simulation_control.setup.{name}", unit)
            number = _finite_number(value, f"simulation_control.setup.{name}.value")
            if name != "max_delta_s" and not number.is_integer():
                raise ValueError(f"simulation_control.setup.{name} must be an integer count")

        far_field = _mapping(simulation["far_field"], "simulation_control.far_field")
        expected_far_field = {
            "theta_start": "deg",
            "theta_stop": "deg",
            "theta_step": "deg",
            "phi_start": "deg",
            "phi_stop": "deg",
            "phi_step": "deg",
        }
        if set(far_field) != set(expected_far_field):
            raise ValueError(
                f"simulation_control.far_field must contain exactly {sorted(expected_far_field)}"
            )
        for name, unit in expected_far_field.items():
            value, _ = _quantity(
                far_field[name], f"simulation_control.far_field.{name}", unit
            )
            _finite_number(value, f"simulation_control.far_field.{name}.value")

    @staticmethod
    def _validate_efficiency_sweep_coverage(
        simulation: Dict[str, Any], objectives: Dict[str, Any]
    ) -> None:
        """Ensure the fixed target-frequency efficiency window is fully covered."""
        resonance = _mapping(
            objectives.get("resonant_frequency"), "objectives.resonant_frequency"
        )
        if resonance.get("goal") != "target_with_tolerance":
            raise ValueError(
                "objectives.resonant_frequency must use goal='target_with_tolerance'"
            )
        target, _ = _quantity(
            resonance.get("target"), "objectives.resonant_frequency.target", "GHz"
        )
        target = _finite_number(target, "objectives.resonant_frequency.target.value")
        half_span, _ = _quantity(
            simulation["total_efficiency_window_half_span"],
            "simulation_control.total_efficiency_window_half_span",
            "MHz",
        )
        half_span_ghz = _finite_number(
            half_span, "simulation_control.total_efficiency_window_half_span.value"
        ) / 1000.0
        efficiency_sweep = simulation["efficiency_sweep"]
        start = float(efficiency_sweep["start"]["value"])
        stop = float(efficiency_sweep["stop"]["value"])
        required_start = target - half_span_ghz
        required_stop = target + half_span_ghz
        epsilon = 1e-12
        if start > required_start + epsilon or stop < required_stop - epsilon:
            raise ValueError(
                "efficiency_sweep must cover the fixed target-frequency efficiency window: "
                f"required {required_start:.12g}-{required_stop:.12g} GHz, "
                f"configured {start:.12g}-{stop:.12g} GHz"
            )

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
        from design_spec import PatchAntennaDesign

        return PatchAntennaDesign.from_mapping(values, self.data["design_variables"])

    def resolve_patch_spec(self, values: Mapping[str, Any]):
        from design_spec import PatchAntennaSpec

        design = self.validate_design(values)
        simulation = self.data["simulation_control"]
        full = {
            **design.to_dict(),
            "center_frequency_ghz": self.simulation_value("adaptive_frequency", "GHz"),
            "substrate_material": self.fixed_value("substrate_material", "label"),
            "substrate_permittivity": self.fixed_value(
                "substrate_relative_permittivity", "1"
            ),
            "substrate_loss_tangent": self.fixed_value("substrate_loss_tangent", "1"),
            "air_margin_xy_mm": self.simulation_value("air_margin_xy", "mm"),
            "air_above_mm": self.simulation_value("air_above", "mm"),
            "air_below_mm": self.simulation_value("air_below", "mm"),
            "sweep_start_ghz": self.simulation_value("sweep_start", "GHz"),
            "sweep_stop_ghz": self.simulation_value("sweep_stop", "GHz"),
            "sweep_points": int(self.simulation_value("sweep_points", "count")),
        }
        return PatchAntennaSpec.from_mapping(full)

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
