"""Frameworks for coax-fed rectangular patch benchmark tasks."""

from __future__ import annotations

from numbers import Real
from typing import Any, Dict, Mapping

from design_spec import CoaxFedPatchDesign, CoaxFedPatchSpec


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


def _validate_common_fixed(data: Mapping[str, Any]) -> None:
    fixed = _mapping(data["fixed_parameters"], "fixed_parameters")
    fixed_units = {
        "substrate_material": "label",
        "substrate_relative_permittivity": "1",
        "substrate_loss_tangent": "1",
        "substrate_width_mm": "mm",
        "substrate_length_mm": "mm",
        "substrate_height_mm": "mm",
        "coax_probe_radius_mm": "mm",
        "coax_port_radius_mm": "mm",
    }
    if set(fixed) != set(fixed_units):
        raise ValueError(f"fixed_parameters must contain exactly {sorted(fixed_units)}")
    for name, unit in fixed_units.items():
        value, _ = _quantity(fixed[name], f"fixed_parameters.{name}", unit)
        if name == "substrate_material":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("substrate_material.value must be a non-empty string")
            continue
        number = _finite_number(value, f"fixed_parameters.{name}.value")
        if number <= 0:
            raise ValueError(f"fixed_parameters.{name}.value must be positive")


def _validate_design_variables(data: Mapping[str, Any]) -> None:
    design_variables = _mapping(data["design_variables"], "design_variables")
    if set(design_variables) != set(CoaxFedPatchDesign.FIELD_NAMES):
        raise ValueError(
            "coax-fed patch design_variables must contain exactly "
            f"{sorted(CoaxFedPatchDesign.FIELD_NAMES)}"
        )
    for name, definition_value in design_variables.items():
        definition = _mapping(definition_value, f"design_variables.{name}")
        if set(definition) - {"unit", "minimum", "maximum", "description"}:
            raise ValueError(f"design_variables.{name} contains unsupported fields")
        if definition.get("unit") != "mm":
            raise ValueError(f"design_variables.{name}.unit must be 'mm'")
        minimum = _finite_number(definition.get("minimum"), f"{name}.minimum")
        maximum = _finite_number(definition.get("maximum"), f"{name}.maximum")
        if minimum >= maximum:
            raise ValueError(f"invalid bounds for design variable {name}")


def _validate_base_simulation(simulation: Mapping[str, Any], quantity_units: Mapping[str, str]) -> None:
    for name, unit in quantity_units.items():
        value, _ = _quantity(simulation[name], f"simulation_control.{name}", unit)
        _finite_number(value, f"simulation_control.{name}.value")


def _validate_setup_and_far_field(simulation: Mapping[str, Any]) -> None:
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
    expected = {
        "theta_start": "deg",
        "theta_stop": "deg",
        "theta_step": "deg",
        "phi_start": "deg",
        "phi_stop": "deg",
        "phi_step": "deg",
    }
    if set(far_field) != set(expected):
        raise ValueError(f"simulation_control.far_field must contain exactly {sorted(expected)}")
    for name, unit in expected.items():
        value, _ = _quantity(far_field[name], f"simulation_control.far_field.{name}", unit)
        _finite_number(value, f"simulation_control.far_field.{name}.value")


class _CoaxPatchFrameworkBase:
    tool_name: str
    tool_description: str
    prompt_guidance: str

    def validate_task(self, data: Mapping[str, Any]) -> None:
        topology = _mapping(data["topology"], "topology")
        if topology.get("port_count") != 1:
            raise ValueError("the coax-fed patch builder requires exactly one port")
        _validate_common_fixed(data)
        _validate_design_variables(data)

    def validate_design(self, values: Mapping[str, Any], data: Mapping[str, Any]):
        return CoaxFedPatchDesign.from_mapping(values, data["design_variables"])

    def resolve_design_spec(self, task: Any, values: Mapping[str, Any]) -> CoaxFedPatchSpec:
        design = self.validate_design(values, task.data)
        fixed = task.data["fixed_parameters"]
        simulation = task.data["simulation_control"]
        full = {
            "substrate_material": fixed["substrate_material"]["value"],
            "substrate_permittivity": fixed["substrate_relative_permittivity"]["value"],
            "substrate_loss_tangent": fixed["substrate_loss_tangent"]["value"],
            "substrate_width_mm": fixed["substrate_width_mm"]["value"],
            "substrate_length_mm": fixed["substrate_length_mm"]["value"],
            "substrate_height_mm": fixed["substrate_height_mm"]["value"],
            "patch_length_mm": design.patch_length_mm,
            "patch_width_mm": design.patch_width_mm,
            "feed_x_mm": design.feed_x_mm,
            "feed_y_mm": design.feed_y_mm,
            "probe_radius_mm": fixed["coax_probe_radius_mm"]["value"],
            "port_radius_mm": fixed["coax_port_radius_mm"]["value"],
            "port_impedance_ohm": simulation["port_impedance"]["value"],
            "adaptive_frequency_ghz": simulation["adaptive_frequency"]["value"],
            "sweep_start_ghz": simulation["sweep_start"]["value"],
            "sweep_stop_ghz": simulation["sweep_stop"]["value"],
            "sweep_points": int(simulation["sweep_points"]["value"]),
            "air_margin_xy_mm": simulation["air_margin_xy"]["value"],
            "air_above_mm": simulation["air_above"]["value"],
            "air_below_mm": simulation["air_below"]["value"],
        }
        return CoaxFedPatchSpec.from_mapping(full)

    def create_candidate(self, hfss_client: Any, values: Mapping[str, Any]):
        return hfss_client.create_coax_fed_patch_antenna(values)

    def manual_verification_steps(self) -> list[str]:
        return [
            "Verify that the coax probe is centered at (feed_x_mm, feed_y_mm), passes through the substrate, and contacts the patch and ground.",
            "Verify that the circular port sheet has the configured coax port radius and 50 ohm reference impedance.",
            "Plot dB(S(1,1)) on Setup1:Sweep1 and verify every target-anchored resonance detection window and -10 dB bandwidth.",
            "For far-field metrics, use InfiniteSphere1 reports at theta=0 degrees and phi=0 degrees where boresight values are required.",
            "Export RadiationEfficiency as a linear ratio and combine it with mismatch efficiency inside every fixed frequency window.",
        ]

    def manual_verification_formulas(self) -> dict[str, str]:
        return {
            "mismatch_efficiency_ratio": "1 - 10^(dB(S11)/10)",
            "single_port_total_efficiency_ratio": "clamp(RadiationEfficiency * mismatch_efficiency_ratio, 0, 1)",
        }


class DualBandCoaxPatchFramework(_CoaxPatchFrameworkBase):
    topology_id = "dual_band_coax_patch"
    tool_name = "create_dual_band_patch_antenna"
    tool_description = (
        "Submit one complete dual-band coax-fed rectangular patch candidate. Python "
        "automatically builds, validates, solves, reads all metrics, and saves an isolated project."
    )
    prompt_guidance = (
        "- A rectangular microstrip patch fed from below by one coaxial probe.\n"
        "- The patch is centered on the fixed substrate. feed_x_mm and feed_y_mm are signed\n"
        "  offsets from the patch center along x and y, respectively.\n"
        "- All boundaries, materials, probe radius, port radius, setup, sweeps, and far-field\n"
        "  configuration are created by trusted backend code."
    )

    def validate_task(self, data: Mapping[str, Any]) -> None:
        super().validate_task(data)
        simulation = _mapping(data["simulation_control"], "simulation_control")
        required = {
            "adaptive_frequency",
            "sweep_start",
            "sweep_stop",
            "sweep_points",
            "bandwidth_s11_threshold",
            "sweep_type",
            "save_fields",
            "save_rad_fields",
            "efficiency_sweep",
            "efficiency_windows",
            "air_margin_xy",
            "air_above",
            "air_below",
            "port_impedance",
            "setup",
            "far_field",
        }
        if set(simulation) != required:
            raise ValueError(f"simulation_control must contain exactly {sorted(required)}")
        quantity_units = {
            "adaptive_frequency": "GHz",
            "sweep_start": "GHz",
            "sweep_stop": "GHz",
            "sweep_points": "count",
            "bandwidth_s11_threshold": "dB",
            "air_margin_xy": "mm",
            "air_above": "mm",
            "air_below": "mm",
            "port_impedance": "ohm",
        }
        _validate_base_simulation(simulation, quantity_units)
        parsed = {
            name: float(simulation[name]["value"])
            for name in quantity_units
        }
        if not parsed["sweep_start"] < parsed["adaptive_frequency"] < parsed["sweep_stop"]:
            raise ValueError("adaptive_frequency must lie strictly inside the sweep")
        if simulation["sweep_type"] != "Interpolating":
            raise ValueError("dual-band sweep_type must be 'Interpolating'")
        if not simulation["save_rad_fields"]:
            raise ValueError("dual-band save_rad_fields must be true")
        if not isinstance(simulation["save_fields"], bool) or not isinstance(
            simulation["save_rad_fields"], bool
        ):
            raise ValueError("dual-band save_fields and save_rad_fields must be boolean")

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
        for name, unit in {
            "start": "GHz",
            "stop": "GHz",
            "points": "count",
        }.items():
            value, _ = _quantity(
                efficiency_sweep[name],
                f"simulation_control.efficiency_sweep.{name}",
                unit,
            )
            _finite_number(value, f"simulation_control.efficiency_sweep.{name}.value")
        if efficiency_sweep["sweep_type"] != "Discrete":
            raise ValueError("dual-band efficiency_sweep.sweep_type must be 'Discrete'")
        if not efficiency_sweep["save_fields"] or not efficiency_sweep["save_rad_fields"]:
            raise ValueError("dual-band efficiency_sweep must save fields and radiation fields")

        windows = _mapping(
            simulation["efficiency_windows"], "simulation_control.efficiency_windows"
        )
        if set(windows) != {"low", "high"}:
            raise ValueError("simulation_control.efficiency_windows must contain 'low' and 'high'")
        for name, window_value in windows.items():
            window = _mapping(
                window_value, f"simulation_control.efficiency_windows.{name}"
            )
            if set(window) != {"target", "half_span"}:
                raise ValueError(f"efficiency_windows.{name} must contain target and half_span")
            target, _ = _quantity(window["target"], f"efficiency_windows.{name}.target", "GHz")
            half_span, _ = _quantity(
                window["half_span"], f"efficiency_windows.{name}.half_span", "MHz"
            )
            _finite_number(target, f"efficiency_windows.{name}.target.value")
            _finite_number(half_span, f"efficiency_windows.{name}.half_span.value")

        _validate_setup_and_far_field(simulation)

    def validate_objectives(
        self, objectives: Mapping[str, Any], simulation: Mapping[str, Any]
    ) -> None:
        expected = {
            "resonance_low",
            "resonance_high",
            "s11_low",
            "s11_high",
            "bandwidth_low",
            "bandwidth_high",
            "gain_min",
            "efficiency_min",
        }
        if set(objectives) != expected:
            raise ValueError(f"dual-band objectives must contain exactly {sorted(expected)}")
        efficiency_sweep = simulation["efficiency_sweep"]
        start = float(efficiency_sweep["start"]["value"])
        stop = float(efficiency_sweep["stop"]["value"])
        for name in ("low", "high"):
            window = simulation["efficiency_windows"][name]
            target = float(window["target"]["value"])
            half_span = float(window["half_span"]["value"]) / 1000.0
            if start > target - half_span + 1e-12 or stop < target + half_span - 1e-12:
                raise ValueError(
                    f"efficiency_sweep must cover efficiency_windows.{name}: "
                    f"{target - half_span:.12g}-{target + half_span:.12g} GHz"
                )

    def manual_verification_steps(self) -> list[str]:
        return [
            "Plot dB(S(1,1)) on Setup1:Sweep1 and separately inspect the 1.6-2.2 GHz and 2.15-2.75 GHz detection windows.",
            "For each valid dip, verify the anchored resonance frequency, minimum S11, and contiguous -10 dB bandwidth containing that dip.",
            "Open InfiniteSphere1 Antenna Parameters on Setup1:EfficiencySweep and compare dB(PeakGain) at 1.9 GHz and 2.45 GHz.",
            "Export RadiationEfficiency from Setup1:EfficiencySweep, interpolate it without extrapolation onto the Sweep1 S11 grid, and compute total efficiency in both fixed +/-15 MHz windows.",
            "Confirm that gain_min_dbi and efficiency_min_percent use the worse of the two bands.",
        ]

    def manual_verification_formulas(self) -> dict[str, str]:
        return {
            "mismatch_efficiency_ratio": "1 - 10^(dB(S11)/10)",
            "interpolated_radiation_efficiency_ratio": "linear interpolation of Setup1:EfficiencySweep RadiationEfficiency onto the Setup1:Sweep1 grid; no extrapolation",
            "single_port_total_efficiency_ratio": "clamp(interpolated_radiation_efficiency_ratio * mismatch_efficiency_ratio, 0, 1)",
            "efficiency_low_mean_percent": "100 * mean(single_port_total_efficiency_ratio over 1.9 GHz +/- 15 MHz)",
            "efficiency_high_mean_percent": "100 * mean(single_port_total_efficiency_ratio over 2.45 GHz +/- 15 MHz)",
            "efficiency_min_percent": "min(efficiency_low_mean_percent, efficiency_high_mean_percent)",
        }


class SingleFeedCPCoaxPatchFramework(_CoaxPatchFrameworkBase):
    topology_id = "single_feed_cp_coax_patch"
    tool_name = "create_cp_patch_antenna"
    tool_description = (
        "Submit one complete single-feed circularly polarized coax-fed rectangular patch "
        "candidate. Python automatically builds, validates, solves, reads all metrics, and saves it."
    )
    prompt_guidance = (
        "- A near-square rectangular microstrip patch fed from below by one coaxial probe.\n"
        "- The patch is centered on the fixed substrate. feed_x_mm and feed_y_mm are signed\n"
        "  offsets from the patch center and may lie in any quadrant.\n"
        "- The polarization sense is determined by the physical geometry; do not submit a label.\n"
        "- All boundaries, materials, probe radius, port radius, setup, sweep, and far-field\n"
        "  configuration are created by trusted backend code."
    )

    def validate_task(self, data: Mapping[str, Any]) -> None:
        super().validate_task(data)
        simulation = _mapping(data["simulation_control"], "simulation_control")
        required = {
            "adaptive_frequency",
            "sweep_start",
            "sweep_stop",
            "sweep_points",
            "sweep_type",
            "save_fields",
            "save_rad_fields",
            "axial_ratio_threshold",
            "air_margin_xy",
            "air_above",
            "air_below",
            "port_impedance",
            "setup",
            "far_field",
        }
        if set(simulation) != required:
            raise ValueError(f"simulation_control must contain exactly {sorted(required)}")
        quantity_units = {
            "adaptive_frequency": "GHz",
            "sweep_start": "GHz",
            "sweep_stop": "GHz",
            "sweep_points": "count",
            "axial_ratio_threshold": "dB",
            "air_margin_xy": "mm",
            "air_above": "mm",
            "air_below": "mm",
            "port_impedance": "ohm",
        }
        _validate_base_simulation(simulation, quantity_units)
        if not simulation["sweep_start"]["value"] < simulation["adaptive_frequency"]["value"] < simulation["sweep_stop"]["value"]:
            raise ValueError("adaptive_frequency must lie strictly inside the sweep")
        if simulation["sweep_type"] != "Interpolating":
            raise ValueError("circular-polarization sweep_type must be 'Interpolating'")
        if not simulation["save_fields"] or not simulation["save_rad_fields"]:
            raise ValueError("circular-polarization sweep must save fields and radiation fields")
        if not isinstance(simulation["save_fields"], bool) or not isinstance(
            simulation["save_rad_fields"], bool
        ):
            raise ValueError("save_fields and save_rad_fields must be boolean")
        _validate_setup_and_far_field(simulation)

    def validate_objectives(
        self, objectives: Mapping[str, Any], simulation: Mapping[str, Any]
    ) -> None:
        expected = {
            "resonance",
            "s11_center",
            "axial_ratio_center",
            "rhcp_dominance",
            "axial_ratio_bandwidth",
            "gain_rhcp",
        }
        if set(objectives) != expected:
            raise ValueError(f"circular-polarization objectives must contain exactly {sorted(expected)}")
        frequency_step_mhz = (
            (float(simulation["sweep_stop"]["value"]) - float(simulation["sweep_start"]["value"]))
            * 1000.0
            / (float(simulation["sweep_points"]["value"]) - 1.0)
        )
        if frequency_step_mhz > 0.5 + 1e-12:
            raise ValueError(
                "circular-polarization sweep resolution must be no coarser than 0.5 MHz"
            )

    def manual_verification_steps(self) -> list[str]:
        return [
            "Plot dB(S(1,1)) on Setup1:Sweep1 and verify the resonance within the 1.425-1.725 GHz detection window and the value at 1.575 GHz.",
            "Create an InfiniteSphere1 report of dB(AxialRatioValue) versus frequency at theta=0 degrees and phi=0 degrees.",
            "Verify the 1.575 GHz axial ratio and the contiguous 3 dB axial-ratio bandwidth containing that frequency; note edge truncation if present.",
            "At theta=0 degrees and phi=0 degrees, compare dB(GainRHCP) and dB(GainLHCP), then subtract LHCP gain from RHCP gain.",
            "Confirm that all polarization quantities are read from saved radiation fields on Setup1:Sweep1.",
        ]

    def manual_verification_formulas(self) -> dict[str, str]:
        return {
            "rhcp_dominance_db": "dB(GainRHCP) - dB(GainLHCP) at theta=0 deg, phi=0 deg, 1.575 GHz",
            "axial_ratio_bandwidth_mhz": "contiguous span containing 1.575 GHz where dB(AxialRatioValue) <= 3 dB at theta=0 deg, phi=0 deg",
        }


dual_band_coax_patch_framework = DualBandCoaxPatchFramework()
single_feed_cp_coax_patch_framework = SingleFeedCPCoaxPatchFramework()
frameworks = [
    dual_band_coax_patch_framework,
    single_feed_cp_coax_patch_framework,
]
