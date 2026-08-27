"""Structured antenna specifications shared by prompts and HFSS clients."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from numbers import Real
from typing import Any, ClassVar, Dict, Mapping, Tuple


@dataclass(frozen=True)
class PatchAntennaDesign:
    """The physical dimensions the model is allowed to choose."""

    substrate_width_mm: float
    substrate_length_mm: float
    substrate_height_mm: float
    patch_width_mm: float
    patch_length_mm: float
    feed_width_mm: float
    inset_depth_mm: float
    inset_gap_mm: float

    FIELD_NAMES: ClassVar[Tuple[str, ...]] = (
        "substrate_width_mm",
        "substrate_length_mm",
        "substrate_height_mm",
        "patch_width_mm",
        "patch_length_mm",
        "feed_width_mm",
        "inset_depth_mm",
        "inset_gap_mm",
    )

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        variable_definitions: Mapping[str, Mapping[str, Any]],
    ) -> "PatchAntennaDesign":
        if not isinstance(values, Mapping):
            raise ValueError("patch antenna design must be a JSON object")
        supplied = set(values)
        expected = set(cls.FIELD_NAMES)
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        if extra:
            raise ValueError(f"unknown or read-only fields: {', '.join(extra)}")

        converted: Dict[str, float] = {}
        for field_name in cls.FIELD_NAMES:
            value = values[field_name]
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{field_name} must be a number")
            number = float(value)
            definition = variable_definitions[field_name]
            minimum = float(definition["minimum"])
            maximum = float(definition["maximum"])
            if not math.isfinite(number) or not minimum <= number <= maximum:
                raise ValueError(f"{field_name} must be between {minimum} and {maximum} mm")
            converted[field_name] = number

        if converted["patch_width_mm"] >= converted["substrate_width_mm"]:
            raise ValueError("patch_width_mm must be smaller than substrate_width_mm")
        if converted["patch_length_mm"] >= converted["substrate_length_mm"]:
            raise ValueError("patch_length_mm must be smaller than substrate_length_mm")
        if converted["feed_width_mm"] + 2 * converted["inset_gap_mm"] >= converted["patch_width_mm"]:
            raise ValueError("feed_width_mm plus both inset gaps must be smaller than patch_width_mm")
        if converted["inset_depth_mm"] >= converted["patch_length_mm"]:
            raise ValueError("inset_depth_mm must be smaller than patch_length_mm")
        return cls(**converted)

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class PatchAntennaSpec:
    """Validated dimensions for one inset-fed rectangular patch antenna."""

    center_frequency_ghz: float
    substrate_material: str
    substrate_permittivity: float
    substrate_loss_tangent: float
    substrate_width_mm: float
    substrate_length_mm: float
    substrate_height_mm: float
    patch_width_mm: float
    patch_length_mm: float
    feed_width_mm: float
    inset_depth_mm: float
    inset_gap_mm: float
    air_margin_xy_mm: float
    air_above_mm: float
    air_below_mm: float
    sweep_start_ghz: float
    sweep_stop_ghz: float
    sweep_points: int

    FIELD_NAMES: ClassVar[Tuple[str, ...]] = (
        "center_frequency_ghz",
        "substrate_material",
        "substrate_permittivity",
        "substrate_loss_tangent",
        "substrate_width_mm",
        "substrate_length_mm",
        "substrate_height_mm",
        "patch_width_mm",
        "patch_length_mm",
        "feed_width_mm",
        "inset_depth_mm",
        "inset_gap_mm",
        "air_margin_xy_mm",
        "air_above_mm",
        "air_below_mm",
        "sweep_start_ghz",
        "sweep_stop_ghz",
        "sweep_points",
    )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "PatchAntennaSpec":
        """Build and validate a spec, rejecting unknown and omitted fields."""
        if not isinstance(values, Mapping):
            raise ValueError("patch antenna specification must be a JSON object")

        supplied = set(values)
        expected = set(cls.FIELD_NAMES)
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        if extra:
            raise ValueError(f"unknown fields: {', '.join(extra)}")

        material = values["substrate_material"]
        if not isinstance(material, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_. -]{0,63}", material):
            raise ValueError("substrate_material must be a simple material name of at most 64 characters")

        numeric_bounds = {
            "center_frequency_ghz": (0.1, 100.0),
            "substrate_permittivity": (1.0, 30.0),
            "substrate_loss_tangent": (0.0, 1.0),
            "substrate_width_mm": (1.0, 2000.0),
            "substrate_length_mm": (1.0, 2000.0),
            "substrate_height_mm": (0.01, 100.0),
            "patch_width_mm": (0.1, 2000.0),
            "patch_length_mm": (0.1, 2000.0),
            "feed_width_mm": (0.01, 500.0),
            "inset_depth_mm": (0.0, 1000.0),
            "inset_gap_mm": (0.0, 100.0),
            "air_margin_xy_mm": (0.1, 5000.0),
            "air_above_mm": (0.1, 5000.0),
            "air_below_mm": (0.1, 5000.0),
            "sweep_start_ghz": (0.01, 100.0),
            "sweep_stop_ghz": (0.01, 100.0),
        }
        converted: Dict[str, Any] = {"substrate_material": material}
        for field_name, (minimum, maximum) in numeric_bounds.items():
            value = values[field_name]
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{field_name} must be a number")
            number = float(value)
            if not math.isfinite(number) or not minimum <= number <= maximum:
                raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
            converted[field_name] = number

        points = values["sweep_points"]
        if isinstance(points, bool) or not isinstance(points, int) or not 11 <= points <= 2001:
            raise ValueError("sweep_points must be an integer between 11 and 2001")
        converted["sweep_points"] = points

        if converted["inset_depth_mm"] <= 0.0:
            raise ValueError("inset_depth_mm must be greater than zero for an inset-fed patch")
        if converted["inset_gap_mm"] <= 0.0:
            raise ValueError("inset_gap_mm must be greater than zero for an inset-fed patch")
        if converted["patch_width_mm"] >= converted["substrate_width_mm"]:
            raise ValueError("patch_width_mm must be smaller than substrate_width_mm")
        if converted["patch_length_mm"] >= converted["substrate_length_mm"]:
            raise ValueError("patch_length_mm must be smaller than substrate_length_mm")
        if converted["feed_width_mm"] + 2 * converted["inset_gap_mm"] >= converted["patch_width_mm"]:
            raise ValueError("feed_width_mm plus both inset gaps must be smaller than patch_width_mm")
        if converted["inset_depth_mm"] >= converted["patch_length_mm"]:
            raise ValueError("inset_depth_mm must be smaller than patch_length_mm")
        if not (
            converted["sweep_start_ghz"]
            < converted["center_frequency_ghz"]
            < converted["sweep_stop_ghz"]
        ):
            raise ValueError("center_frequency_ghz must lie strictly inside the sweep range")

        return cls(**converted)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoaxFedPatchDesign:
    """The four model-controlled dimensions of a coax-fed rectangular patch."""

    patch_length_mm: float
    patch_width_mm: float
    feed_x_mm: float
    feed_y_mm: float

    FIELD_NAMES: ClassVar[Tuple[str, ...]] = (
        "patch_length_mm",
        "patch_width_mm",
        "feed_x_mm",
        "feed_y_mm",
    )

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        variable_definitions: Mapping[str, Mapping[str, Any]],
    ) -> "CoaxFedPatchDesign":
        if not isinstance(values, Mapping):
            raise ValueError("coax-fed patch design must be a JSON object")
        supplied = set(values)
        expected = set(cls.FIELD_NAMES)
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        if extra:
            raise ValueError(f"unknown or read-only fields: {', '.join(extra)}")

        converted: Dict[str, float] = {}
        for field_name in cls.FIELD_NAMES:
            value = values[field_name]
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{field_name} must be a number")
            number = float(value)
            definition = variable_definitions[field_name]
            minimum = float(definition["minimum"])
            maximum = float(definition["maximum"])
            if not math.isfinite(number) or not minimum <= number <= maximum:
                raise ValueError(f"{field_name} must be between {minimum} and {maximum} mm")
            converted[field_name] = number

        if converted["patch_length_mm"] <= 0 or converted["patch_width_mm"] <= 0:
            raise ValueError("patch dimensions must be positive")
        if abs(converted["feed_x_mm"]) < 2.0 or abs(converted["feed_y_mm"]) < 2.0:
            raise ValueError("feed_x_mm and feed_y_mm must each be at least 2 mm from the patch center")
        if converted["patch_length_mm"] / 2.0 - abs(converted["feed_x_mm"]) < 2.0:
            raise ValueError("feed_x_mm must be at least 2 mm from the patch x edge")
        if converted["patch_width_mm"] / 2.0 - abs(converted["feed_y_mm"]) < 2.0:
            raise ValueError("feed_y_mm must be at least 2 mm from the patch y edge")
        return cls(**converted)

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class CoaxFedPatchSpec:
    """Resolved read-only and model-controlled parameters for a coax-fed patch."""

    substrate_material: str
    substrate_permittivity: float
    substrate_loss_tangent: float
    substrate_width_mm: float
    substrate_length_mm: float
    substrate_height_mm: float
    patch_length_mm: float
    patch_width_mm: float
    feed_x_mm: float
    feed_y_mm: float
    probe_radius_mm: float
    port_radius_mm: float
    port_impedance_ohm: float
    adaptive_frequency_ghz: float
    sweep_start_ghz: float
    sweep_stop_ghz: float
    sweep_points: int
    air_margin_xy_mm: float
    air_above_mm: float
    air_below_mm: float

    FIELD_NAMES: ClassVar[Tuple[str, ...]] = (
        "substrate_material",
        "substrate_permittivity",
        "substrate_loss_tangent",
        "substrate_width_mm",
        "substrate_length_mm",
        "substrate_height_mm",
        "patch_length_mm",
        "patch_width_mm",
        "feed_x_mm",
        "feed_y_mm",
        "probe_radius_mm",
        "port_radius_mm",
        "port_impedance_ohm",
        "adaptive_frequency_ghz",
        "sweep_start_ghz",
        "sweep_stop_ghz",
        "sweep_points",
        "air_margin_xy_mm",
        "air_above_mm",
        "air_below_mm",
    )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CoaxFedPatchSpec":
        if not isinstance(values, Mapping):
            raise ValueError("coax-fed patch specification must be a JSON object")
        supplied = set(values)
        expected = set(cls.FIELD_NAMES)
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        if extra:
            raise ValueError(f"unknown fields: {', '.join(extra)}")

        converted: Dict[str, Any] = {}
        for field_name in cls.FIELD_NAMES:
            value = values[field_name]
            if field_name == "substrate_material":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("substrate_material must be a non-empty string")
                converted[field_name] = value
                continue
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{field_name} must be a number")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"{field_name} must be a finite number")
            if field_name not in {"feed_x_mm", "feed_y_mm"} and number <= 0:
                raise ValueError(f"{field_name} must be a finite positive number")
            converted[field_name] = number
        converted["sweep_points"] = int(converted["sweep_points"])

        patch_length = converted["patch_length_mm"]
        patch_width = converted["patch_width_mm"]
        if patch_length + 10.0 > converted["substrate_width_mm"]:
            raise ValueError("patch_length_mm must leave at least 5 mm margin on each x edge")
        if patch_width + 10.0 > converted["substrate_length_mm"]:
            raise ValueError("patch_width_mm must leave at least 5 mm margin on each y edge")
        if not converted["sweep_start_ghz"] < converted["adaptive_frequency_ghz"] < converted["sweep_stop_ghz"]:
            raise ValueError("adaptive_frequency_ghz must lie inside the sweep")
        return cls(**converted)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
