import unittest
from pathlib import Path

import numpy as np

from design_spec import CoaxFedPatchDesign
from frameworks import get_framework
from hfss_client import PyAEDTHFSSClient
from prompts import build_system_prompt, redact_task_for_model
from task_batch import TaskBatchSpec
from task_spec import AntennaTaskSpec, load_default_task


DUAL_BAND_TASK = "tasks/dual_band_patch_1p9_2p45_v1.json"
CP_TASK = "tasks/cp_patch_gps_1p575_v1.json"


def dual_band_design(**overrides):
    values = {
        "patch_length_mm": 28.0,
        "patch_width_mm": 37.0,
        "feed_x_mm": 7.0,
        "feed_y_mm": 8.0,
    }
    values.update(overrides)
    return values


def cp_design(**overrides):
    values = {
        "patch_length_mm": 45.0,
        "patch_width_mm": 44.0,
        "feed_x_mm": -8.0,
        "feed_y_mm": 8.0,
    }
    values.update(overrides)
    return values


class NewTaskFrameworkTests(unittest.TestCase):
    def test_framework_registry_routes_both_coax_topologies(self):
        dual = AntennaTaskSpec.load(DUAL_BAND_TASK)
        circular = AntennaTaskSpec.load(CP_TASK)

        self.assertEqual(dual.framework, get_framework("dual_band_coax_patch"))
        self.assertEqual(circular.framework, get_framework("single_feed_cp_coax_patch"))
        self.assertEqual(dual.design_tool_name, "create_dual_band_patch_antenna")
        self.assertEqual(circular.design_tool_name, "create_cp_patch_antenna")

    def test_task_batch_preserves_declared_order(self):
        batch = TaskBatchSpec.load("task_batch.json")

        self.assertEqual(
            [entry.task_id for entry in batch.entries],
            [
                "inset_patch_2p45",
                "dual_band_patch_1p9_2p45",
                "cp_patch_gps_1p575",
            ],
        )

    def test_invalid_full_score_threshold_order_is_rejected(self):
        task_data = AntennaTaskSpec.load(CP_TASK).to_dict()
        task_data["objectives"]["axial_ratio_center"]["full_score_threshold"]["value"] = 3.5

        with self.assertRaisesRegex(ValueError, "full_score_threshold < pass_threshold"):
            AntennaTaskSpec.from_mapping(task_data)

    def test_feed_geometry_validates_center_edge_and_signed_coordinates(self):
        dual = AntennaTaskSpec.load(DUAL_BAND_TASK)
        circular = AntennaTaskSpec.load(CP_TASK)

        self.assertIsInstance(dual.validate_design(dual_band_design()), CoaxFedPatchDesign)
        self.assertIsInstance(circular.validate_design(cp_design()), CoaxFedPatchDesign)

        with self.assertRaisesRegex(ValueError, "at least 2 mm from the patch center"):
            circular.validate_design(cp_design(feed_x_mm=1.9))
        with self.assertRaisesRegex(ValueError, "at least 2 mm from the patch x edge"):
            dual.validate_design(dual_band_design(patch_length_mm=24.0, feed_x_mm=12.0))
        with self.assertRaisesRegex(ValueError, "at least 2 mm from the patch y edge"):
            dual.validate_design(dual_band_design(patch_width_mm=32.0, feed_y_mm=15.0))


class ThreeStageScoringTests(unittest.TestCase):
    def test_cp_task_scores_zero_pass_and_full_boundaries(self):
        task = AntennaTaskSpec.load(CP_TASK)
        zero = {
            "resonant_freq_ghz": 1.645,
            "s11_center_db": -8.0,
            "axial_ratio_center_db": 8.0,
            "rhcp_dominance_db": 8.0,
            "axial_ratio_bandwidth_mhz": 2.0,
            "gain_rhcp_dbi": 0.0,
        }
        passing = {
            "resonant_freq_ghz": 1.55,
            "s11_center_db": -18.0,
            "axial_ratio_center_db": 3.0,
            "rhcp_dominance_db": 15.0,
            "axial_ratio_bandwidth_mhz": 8.0,
            "gain_rhcp_dbi": 1.0,
        }
        full = {
            "resonant_freq_ghz": 1.575,
            "s11_center_db": -25.0,
            "axial_ratio_center_db": 1.5,
            "rhcp_dominance_db": 20.0,
            "axial_ratio_bandwidth_mhz": 12.0,
            "gain_rhcp_dbi": 2.0,
        }

        self.assertEqual(task.evaluate_metrics(zero)["score"], 0.0)
        self.assertEqual(task.evaluate_metrics(passing)["score"], 60.0)
        self.assertEqual(task.evaluate_metrics(full)["score"], 100.0)
        self.assertEqual(task.evaluate_metrics(passing)["engineering_passed"], True)

    def test_legacy_task_retains_single_stage_behavior(self):
        task = load_default_task()
        metrics = {"center_freq_ghz": 2.5}

        scored = task.evaluate_metrics(metrics)

        self.assertEqual(scored["objectives"]["resonant_frequency"]["score_fraction"], 0.5)
        self.assertEqual(scored["objectives"]["resonant_frequency"]["points_awarded"], 12.5)
        self.assertEqual(scored["objectives"]["resonant_frequency"]["scoring_model"], "legacy_single_stage")

    def test_prompt_redacts_all_full_score_calibration_fields(self):
        task = AntennaTaskSpec.load(CP_TASK)
        redacted = redact_task_for_model(task)
        prompt = build_system_prompt(task)

        def contains_full_score(value):
            if isinstance(value, dict):
                return any(
                    str(key) in {"full_score_threshold", "full_score_tolerance"}
                    or contains_full_score(item)
                    for key, item in value.items()
                )
            if isinstance(value, list):
                return any(contains_full_score(item) for item in value)
            return False

        self.assertFalse(contains_full_score(redacted))
        self.assertNotIn("full_score_threshold", prompt)
        self.assertNotIn("full_score_tolerance", prompt)
        self.assertNotIn("0.8GHz", prompt)


class _FakeNamedObject:
    def __init__(self, name):
        self.name = name

    def delete(self):
        return True


class _FakeModeler:
    def __init__(self):
        self.model_units = "mm"
        self.object_names = []
        self.created = {}

    def create_box(self, **kwargs):
        self.created[kwargs["name"]] = kwargs
        self.object_names.append(kwargs["name"])
        return _FakeNamedObject(kwargs["name"])

    def create_rectangle(self, **kwargs):
        return self.create_box(**kwargs)

    def create_cylinder(self, **kwargs):
        return self.create_box(**kwargs)

    def create_circle(self, **kwargs):
        return self.create_box(**kwargs)

    def create_region(self, **kwargs):
        return self.create_box(**kwargs)

    def delete(self, assignment=None):
        self.object_names = []
        return True


class _FakeMaterial:
    def __init__(self, name):
        self.name = name


class _FakeMaterials:
    def __init__(self):
        self.material_keys = []

    def add_material(self, name):
        self.material_keys.append(name)
        return _FakeMaterial(name)

    def __getitem__(self, name):
        return _FakeMaterial(name)


class _FakeSetup:
    def __init__(self, name):
        self.name = name
        self.props = {}

    def update(self):
        return True


class _FakeBuilderHfss:
    def __init__(self):
        self.modeler = _FakeModeler()
        self.materials = _FakeMaterials()
        self.field_setups = []
        self.boundaries = []
        self.setup_names = []
        self.excitation_names = []
        self.existing_analysis_sweeps = []
        self.sweep_calls = []
        self.last_setup = None
        self.port_kwargs = None
        self.sphere_kwargs = None

    def __setitem__(self, name, value):
        pass

    def assign_perfecte_to_sheets(self, assignment, name, is_infinite_ground):
        boundary = _FakeNamedObject(name)
        self.boundaries.append(boundary)
        return boundary

    def assign_perfecte_to_objects(self, assignment, name):
        return self.assign_perfecte_to_sheets(assignment, name, False)

    def lumped_port(self, **kwargs):
        self.port_kwargs = kwargs
        self.excitation_names.append(kwargs["name"])
        return _FakeNamedObject(kwargs["name"])

    def assign_radiation_boundary_to_objects(self, assignment, name):
        boundary = _FakeNamedObject(name)
        self.boundaries.append(boundary)
        return boundary

    def insert_infinite_sphere(self, **kwargs):
        self.sphere_kwargs = kwargs
        sphere = _FakeNamedObject(kwargs["name"])
        self.field_setups.append(sphere)
        return sphere

    def create_setup(self, name, setup_type):
        self.setup_names.append(name)
        self.last_setup = _FakeSetup(name)
        return self.last_setup

    def get_setup(self, name):
        return self.last_setup if name in self.setup_names else None

    def create_linear_count_sweep(self, setup, name, **kwargs):
        self.sweep_calls.append({"setup": setup, "name": name, **kwargs})
        self.existing_analysis_sweeps.extend([f"{setup} : LastAdaptive", f"{setup} : {name}"])
        return _FakeNamedObject(name)


class CoaxBuilderTests(unittest.TestCase):
    @staticmethod
    def _client(task_file):
        client = PyAEDTHFSSClient()
        client.configure_task(AntennaTaskSpec.load(task_file))
        client._hfss = _FakeBuilderHfss()
        client._project_path = Path("candidate_001.aedt").resolve()
        return client

    def test_dual_band_builder_creates_probe_and_both_sweeps(self):
        client = self._client(DUAL_BAND_TASK)

        result = client.create_coax_fed_patch_antenna(dual_band_design())

        self.assertTrue(result.success, result.message)
        backend = client._hfss
        self.assertIn("CoaxProbe", backend.modeler.created)
        self.assertEqual(backend.port_kwargs["impedance"], 50.0)
        self.assertEqual(len(backend.sweep_calls), 2)
        self.assertEqual([call["name"] for call in backend.sweep_calls], ["Sweep1", "EfficiencySweep"])

    def test_dual_band_builder_falls_back_when_object_boundary_api_is_missing(self):
        client = self._client(DUAL_BAND_TASK)
        client._hfss.assign_perfecte_to_objects = None

        result = client.create_coax_fed_patch_antenna(dual_band_design())

        self.assertTrue(result.success, result.message)
        self.assertIn("CoaxProbePEC", [boundary.name for boundary in client._hfss.boundaries])

    def test_cp_builder_creates_one_sweep_with_saved_radiation_fields(self):
        client = self._client(CP_TASK)

        result = client.create_coax_fed_patch_antenna(cp_design())

        self.assertTrue(result.success, result.message)
        backend = client._hfss
        self.assertEqual(len(backend.sweep_calls), 1)
        self.assertEqual(backend.sweep_calls[0]["name"], "Sweep1")
        self.assertTrue(backend.sweep_calls[0]["save_fields"])
        self.assertTrue(backend.sweep_calls[0]["save_rad_fields"])


class _FakeSolutionData:
    def __init__(self, values, frequencies):
        self._values = values
        self.primary_sweep_values = frequencies
        self.units_sweeps = {"Freq": "GHz"}
        self.intrinsics = {"Freq": frequencies}

    def data_real(self, expression=None):
        if expression is None:
            return self._values["dB(S(1,1))"]
        return self._values.get(expression, [])


class _FakeAntennaReport:
    def __init__(self, values, frequencies):
        self._values = values
        self._frequencies = frequencies
        self.variations = {}

    def get_solution_data(self):
        return _FakeSolutionData(self._values, self._frequencies)


class _FakeReports:
    def __init__(self, values, frequencies):
        self.values = values
        self.frequencies = frequencies

    def antenna_parameters(self, expressions, setup, infinite_sphere):
        return _FakeAntennaReport(
            {expressions: self.values[expressions]},
            self.frequencies[expressions],
        )


class _FakePost:
    def __init__(self, s11, values, frequencies):
        self.s11 = s11
        self.reports_by_category = _FakeReports(values, frequencies)

    def get_solution_data(self, **kwargs):
        return _FakeSolutionData({"dB(S(1,1))": self.s11[1]}, self.s11[0])


class _FakeMetricHfss:
    def __init__(self, s11, values, frequencies):
        self.post = _FakePost(s11, values, frequencies)
        self.field_setups = [_FakeNamedObject("InfiniteSphere1")]


class NewMetricReaderTests(unittest.TestCase):
    def test_dual_band_reader_marks_missing_low_band_resonance(self):
        freq = np.linspace(1.5, 3.0, 151)
        s11 = np.full_like(freq, -2.0)
        s11[np.argmin(np.abs(freq - 2.45))] = -25.0
        values = {
            "dB(PeakGain)": [4.0, 3.0],
            "RadiationEfficiency": [0.8, 0.8],
        }
        frequencies = {
            "dB(PeakGain)": [1.9, 2.45],
            "RadiationEfficiency": [1.875, 2.465],
        }
        client = PyAEDTHFSSClient()
        client.configure_task(AntennaTaskSpec.load(DUAL_BAND_TASK))
        client._hfss = _FakeMetricHfss((freq, s11), values, frequencies)

        metrics = client._dual_band_metrics()

        self.assertTrue(metrics["no_resonance_in_window_low"])
        self.assertNotIn("no_resonance_in_window_high", metrics)
        self.assertAlmostEqual(metrics["resonance_high_ghz"], 2.45)

    def test_cp_reader_parses_axial_ratio_and_polarization_gains(self):
        freq = np.linspace(1.45, 1.70, 251)
        s11 = np.full_like(freq, -5.0)
        s11[np.argmin(np.abs(freq - 1.575))] = -18.0
        ar_freq = np.linspace(1.55, 1.60, 101)
        axial_ratio = 0.7 + 0.614 * np.abs(ar_freq - 1.575) * 1000.0
        values = {
            "dB(AxialRatioValue)": axial_ratio,
            "dB(GainRHCP)": np.full_like(ar_freq, 1.3),
            "dB(GainLHCP)": np.full_like(ar_freq, -17.0),
        }
        frequencies = {name: ar_freq for name in values}
        client = PyAEDTHFSSClient()
        client.configure_task(AntennaTaskSpec.load(CP_TASK))
        client._hfss = _FakeMetricHfss((freq, s11), values, frequencies)

        metrics = client._circular_polarization_metrics()

        self.assertAlmostEqual(metrics["axial_ratio_center_db"], 0.7)
        self.assertAlmostEqual(metrics["rhcp_dominance_db"], 18.3)
        self.assertAlmostEqual(metrics["gain_rhcp_dbi"], 1.3)
        self.assertAlmostEqual(metrics["axial_ratio_bandwidth_mhz"], 7.5, delta=0.01)
        self.assertFalse(metrics["axial_ratio_bandwidth_truncated"])


if __name__ == "__main__":
    unittest.main()
