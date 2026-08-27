"""Framework contract for the inset-fed rectangular patch topology."""

from __future__ import annotations

from numbers import Real
from typing import Any, Dict, Mapping

from design_spec import PatchAntennaDesign, PatchAntennaSpec


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be finite")
    return number


def _quantity(value: Any, label: str, expected_unit: str):
    quantity = _mapping(value, label)
    if set(quantity) - {"value", "unit", "description"}:
        raise ValueError(f"{label} contains unsupported fields")
    if quantity.get("unit") != expected_unit:
        raise ValueError(f"{label}.unit must be '{expected_unit}'")
    return quantity.get("value"), quantity


class InsetFedRectangularPatchFramework:
    topology_id = "inset_fed_rectangular_patch"
    tool_name = "create_patch_antenna"
    tool_description = (
        "Submit one complete inset-fed patch candidate. Python automatically builds, validates, "
        "solves, reads all metrics, and saves an isolated project copy before returning."
    )
    prompt_guidance = (
        "- An inset-fed rectangular microstrip patch with one lumped port.\n"
        "- All geometry, boundaries, setup, sweep, and far-field configuration are created by trusted backend code.\n"
        "- You choose only the fields exposed by `create_patch_antenna`. Material and simulation controls are fixed."
    )

    def validate_task(self, data: Mapping[str, Any]) -> None:
        topology = _mapping(data["topology"], "topology")
        if topology.get("port_count") != 1:
            raise ValueError("the inset-fed patch builder requires exactly one port")

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

        design_variables = _mapping(data["design_variables"], "design_variables")
        if set(design_variables) != set(PatchAntennaDesign.FIELD_NAMES):
            raise ValueError(
                "inset-fed patch design_variables must contain exactly "
                f"{sorted(PatchAntennaDesign.FIELD_NAMES)}"
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

        self._validate_simulation_control(data["simulation_control"])

    def validate_objectives(
        self, objectives: Mapping[str, Any], simulation: Mapping[str, Any]
    ) -> None:
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

    def validate_design(self, values: Mapping[str, Any], data: Mapping[str, Any]):
        return PatchAntennaDesign.from_mapping(values, data["design_variables"])

    def resolve_design_spec(self, task: Any, values: Mapping[str, Any]) -> PatchAntennaSpec:
        design = self.validate_design(values, task.data)
        full = {
            **design.to_dict(),
            "center_frequency_ghz": task.simulation_value("adaptive_frequency", "GHz"),
            "substrate_material": task.fixed_value("substrate_material", "label"),
            "substrate_permittivity": task.fixed_value(
                "substrate_relative_permittivity", "1"
            ),
            "substrate_loss_tangent": task.fixed_value("substrate_loss_tangent", "1"),
            "air_margin_xy_mm": task.simulation_value("air_margin_xy", "mm"),
            "air_above_mm": task.simulation_value("air_above", "mm"),
            "air_below_mm": task.simulation_value("air_below", "mm"),
            "sweep_start_ghz": task.simulation_value("sweep_start", "GHz"),
            "sweep_stop_ghz": task.simulation_value("sweep_stop", "GHz"),
            "sweep_points": int(task.simulation_value("sweep_points", "count")),
        }
        return PatchAntennaSpec.from_mapping(full)

    def create_candidate(self, hfss_client: Any, values: Mapping[str, Any]):
        return hfss_client.create_patch_antenna(values)

    def manual_verification_steps(self) -> list[str]:
        return [
            "Plot dB(S(1,1)) on Setup1:Sweep1 and compare resonance, minimum S11, and contiguous threshold bandwidth.",
            "At Setup1:LastAdaptive, open the InfiniteSphere1 Antenna Parameters report and compare dB(PeakGain).",
            "Export InfiniteSphere1 RadiationEfficiency from the discrete Setup1:EfficiencySweep and verify the fixed-window total-efficiency mean.",
        ]

    def manual_verification_formulas(self) -> dict[str, str]:
        return {
            "mismatch_efficiency_ratio": "1 - 10^(dB(S11)/10)",
            "interpolated_radiation_efficiency_ratio": "linear interpolation of Setup1:EfficiencySweep RadiationEfficiency onto the Setup1:Sweep1 grid; no extrapolation",
            "single_port_total_efficiency_ratio": "clamp(interpolated_radiation_efficiency_ratio * mismatch_efficiency_ratio, 0, 1)",
            "total_efficiency_mean_percent": "100 * arithmetic_mean(single_port_total_efficiency_ratio on Sweep1 samples within objective target frequency +/- configured half span)",
        }


framework = InsetFedRectangularPatchFramework()
