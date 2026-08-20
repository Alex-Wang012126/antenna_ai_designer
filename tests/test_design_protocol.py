import json
import math
import tempfile
import unittest
from pathlib import Path

from config import Config
from design_loop import DesignAgent
from design_spec import PatchAntennaSpec
from evaluator import AntennaEvaluator
from hfss_client import PlaceholderHFSSClient, PyAEDTHFSSClient
from model_client import PlaceholderModelClient, ResponsesModelClient, ToolCall
from prompts import build_tools_description


def valid_spec():
    return {
        "center_frequency_ghz": 2.45,
        "substrate_material": "Rogers5880_custom",
        "substrate_permittivity": 2.2,
        "substrate_loss_tangent": 0.0009,
        "substrate_width_mm": 100.0,
        "substrate_length_mm": 100.0,
        "substrate_height_mm": 3.175,
        "patch_width_mm": 48.5,
        "patch_length_mm": 39.0,
        "feed_width_mm": 9.6,
        "inset_depth_mm": 13.5,
        "inset_gap_mm": 1.0,
        "air_margin_xy_mm": 40.0,
        "air_above_mm": 55.0,
        "air_below_mm": 25.0,
        "sweep_start_ghz": 2.2,
        "sweep_stop_ghz": 2.7,
        "sweep_points": 251,
    }


class PatchAntennaSpecTests(unittest.TestCase):
    def test_accepts_complete_valid_spec(self):
        spec = PatchAntennaSpec.from_mapping(valid_spec())
        self.assertEqual(spec.center_frequency_ghz, 2.45)
        self.assertEqual(spec.sweep_points, 251)

    def test_rejects_missing_and_unknown_fields(self):
        missing = valid_spec()
        del missing["feed_width_mm"]
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            PatchAntennaSpec.from_mapping(missing)

        extra = valid_spec()
        extra["script"] = "unsafe"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            PatchAntennaSpec.from_mapping(extra)

    def test_rejects_non_finite_and_invalid_geometry(self):
        non_finite = valid_spec()
        non_finite["patch_width_mm"] = math.nan
        with self.assertRaises(ValueError):
            PatchAntennaSpec.from_mapping(non_finite)

        invalid = valid_spec()
        invalid["feed_width_mm"] = invalid["patch_width_mm"]
        with self.assertRaisesRegex(ValueError, "inset gaps"):
            PatchAntennaSpec.from_mapping(invalid)

        for field_name in ("inset_depth_mm", "inset_gap_mm"):
            degenerate = valid_spec()
            degenerate[field_name] = 0.0
            with self.assertRaisesRegex(ValueError, "greater than zero"):
                PatchAntennaSpec.from_mapping(degenerate)


class ToolProtocolTests(unittest.TestCase):
    def test_tools_are_strict_and_do_not_expose_code_execution(self):
        tools = build_tools_description()
        names = [tool["function"]["name"] for tool in tools]
        self.assertEqual(
            names,
            ["create_patch_antenna", "finalize_design"],
        )
        self.assertNotIn("update_geometry", names)
        for tool in tools:
            function = tool["function"]
            self.assertTrue(function["strict"])
            self.assertFalse(function["parameters"]["additionalProperties"])

        converted = ResponsesModelClient._convert_tools(tools)
        self.assertTrue(all(tool["strict"] for tool in converted))

    def test_placeholder_workflow_obeys_state_machine(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run_test"
            config = Config(project_dir=run_dir, log_dir=run_dir)
            result = DesignAgent(
                model_client=PlaceholderModelClient(),
                hfss_client=PlaceholderHFSSClient(),
                requirements="2.45 GHz patch, S11 < -10 dB, bandwidth > 100 MHz, gain > 3 dBi",
                config=config,
            ).run(max_iterations=10)

            self.assertTrue(result.success)
            self.assertEqual(result.iterations_used, 1)
            self.assertEqual(result.model_calls, 2)
            self.assertEqual(result.selected_candidate["iteration"], 1)
            self.assertEqual(
                list(result.selected_candidate["stages"]),
                ["build", "validation", "solve", "metrics", "save"],
            )
            self.assertTrue(result.log_file.is_file())
            self.assertTrue(result.manifest_file.is_file())
            self.assertTrue(result.metrics_file.is_file())
            self.assertTrue(Path(result.selected_candidate["project_file"]).is_file())
            artifact_parents = {
                result.log_file.parent,
                result.manifest_file.parent,
                result.metrics_file.parent,
                Path(result.selected_candidate["project_file"]).parent,
            }
            self.assertEqual(artifact_parents, {run_dir})

    def test_iteration_limit_counts_complete_candidate_pipelines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = Config(
                project_dir=root / "run",
                log_dir=root / "run",
                max_design_iterations=2,
                max_solve_calls=2,
            )
            second_spec = valid_spec()
            second_spec["patch_width_mm"] = 49.0
            model = PlaceholderModelClient(
                [
                    {"name": "create_patch_antenna", "arguments": valid_spec()},
                    {"name": "create_patch_antenna", "arguments": second_spec},
                ]
            )

            result = DesignAgent(
                model_client=model,
                hfss_client=PlaceholderHFSSClient(),
                requirements="2.45 GHz patch, S11 < -10 dB, bandwidth > 100 MHz, gain > 3 dBi",
                config=config,
            ).run(max_iterations=2)

            self.assertTrue(result.success, result.final_summary)
            self.assertEqual(result.iterations_used, 2)
            self.assertEqual(result.model_calls, 2)
            self.assertEqual(
                [candidate["status"] for candidate in result.candidates],
                ["completed", "completed"],
            )
            self.assertEqual(result.selected_candidate["iteration"], 2)
            tool_result = json.loads(result.messages[-1]["content"])
            self.assertEqual(tool_result["remaining_design_iterations"], 0)
            manifest = json.loads(result.manifest_file.read_text(encoding="utf-8"))
            self.assertEqual(manifest["stop_reason"], "iteration_limit")
            self.assertEqual(manifest["solve_calls"], 2)

    def test_finalize_is_model_controlled_after_one_complete_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = Config(project_dir=root / "run", log_dir=root / "run")
            hfss = PlaceholderHFSSClient()
            hfss.connect(config)
            agent = DesignAgent(
                model_client=PlaceholderModelClient(),
                hfss_client=hfss,
                requirements="test",
                config=config,
            )
            candidate = agent._execute_tool(ToolCall("create_patch_antenna", valid_spec()))

            result = agent._execute_tool(ToolCall("finalize_design", {"summary": "done"}))

            self.assertTrue(candidate.success, candidate.message)
            self.assertTrue(result.success, result.message)
            self.assertTrue(agent._finalized)
            self.assertEqual(agent._stop_reason, "model_requested")
            self.assertTrue((root / "run" / "candidate_001.aedt").is_file())
            hfss.disconnect()

    def test_finalize_is_rejected_before_required_steps(self):
        agent = DesignAgent(
            model_client=PlaceholderModelClient(),
            hfss_client=PlaceholderHFSSClient(),
            requirements="test",
        )
        result = agent._execute_tool(ToolCall("finalize_design", {"summary": "too early"}))
        self.assertFalse(result.success)
        self.assertIn("full simulation pipeline", result.message)

    def test_failed_candidate_does_not_discard_previous_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            config = Config(project_dir=run_dir, log_dir=run_dir, max_design_iterations=2)
            hfss = PlaceholderHFSSClient()
            hfss.connect(config)
            agent = DesignAgent(
                model_client=PlaceholderModelClient(),
                hfss_client=hfss,
                requirements="test",
                config=config,
            )

            first = agent._execute_tool(ToolCall("create_patch_antenna", valid_spec()))
            second = agent._execute_tool(
                ToolCall("create_patch_antenna", {"center_frequency_ghz": 2.45})
            )

            self.assertTrue(first.success)
            self.assertFalse(second.success)
            self.assertEqual(agent._selected_candidate["iteration"], 1)
            self.assertEqual(agent._candidates[-1]["status"], "parameter_validation_failed")
            self.assertTrue(Path(agent._selected_candidate["project_file"]).is_file())
            hfss.disconnect()

    def test_backend_exception_is_recorded_and_previous_candidate_remains_selected(self):
        class FailSecondSolveClient(PlaceholderHFSSClient):
            def __init__(self):
                super().__init__()
                self.solve_calls = 0

            def solve(self, solution_name=None):
                self.solve_calls += 1
                if self.solve_calls == 2:
                    raise RuntimeError("simulated backend crash")
                return super().solve(solution_name)

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            config = Config(
                project_dir=run_dir,
                log_dir=run_dir,
                max_design_iterations=2,
                max_solve_calls=2,
            )
            hfss = FailSecondSolveClient()
            hfss.connect(config)
            agent = DesignAgent(
                model_client=PlaceholderModelClient(),
                hfss_client=hfss,
                requirements="test",
                config=config,
            )
            second_spec = valid_spec()
            second_spec["patch_length_mm"] = 40.0

            first = agent._execute_tool(ToolCall("create_patch_antenna", valid_spec()))
            second = agent._execute_tool(ToolCall("create_patch_antenna", second_spec))

            self.assertTrue(first.success)
            self.assertFalse(second.success)
            self.assertEqual(agent._selected_candidate["iteration"], 1)
            self.assertEqual(agent._candidates[-1]["status"], "solve_failed")
            self.assertIn("simulated backend crash", agent._candidates[-1]["message"])
            hfss.disconnect()


class EvaluatorFallbackTests(unittest.TestCase):
    def test_evaluator_searches_newest_to_oldest_for_a_passing_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            run_dir.mkdir()
            projects = []
            for index in range(1, 4):
                project = run_dir / f"candidate_{index:03d}.aedt"
                project.write_text("placeholder", encoding="utf-8")
                projects.append(project)

            manifest = {
                "protocol_version": 2,
                "candidates": [
                    {
                        "iteration": 1,
                        "success": True,
                        "status": "completed",
                        "project_file": str(projects[0]),
                        "metrics": {
                            "center_freq_ghz": 2.45,
                            "s11_min_db": -18.0,
                            "bandwidth_mhz": 130.0,
                            "peak_gain_dbi": 4.5,
                        },
                    },
                    {
                        "iteration": 2,
                        "success": True,
                        "status": "completed",
                        "project_file": str(projects[1]),
                        "metrics": {
                            "center_freq_ghz": 2.45,
                            "s11_min_db": -8.0,
                            "bandwidth_mhz": 70.0,
                            "peak_gain_dbi": 2.0,
                        },
                    },
                    {
                        "iteration": 3,
                        "success": False,
                        "status": "solve_failed",
                        "project_file": str(projects[2]),
                    },
                ],
            }
            manifest_file = run_dir / "run_manifest.json"
            manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
            evaluator = AntennaEvaluator(Config(project_dir=run_dir, log_dir=run_dir))

            result = evaluator.evaluate(
                "2.45 GHz patch, S11 < -10 dB, bandwidth > 100 MHz, gain > 3 dBi",
                manifest_file=manifest_file,
            )

            self.assertTrue(result.passed)
            self.assertEqual(result.selected_iteration, 1)
            self.assertEqual(result.selected_project_file, projects[0])
            self.assertEqual(
                [item["iteration"] for item in result.candidate_evaluations],
                [3, 2, 1],
            )
            self.assertFalse(result.candidate_evaluations[0]["pipeline_success"])
            self.assertEqual(result.report_file.parent, run_dir)


class _FakeNamedObject:
    def __init__(self, name, collection=None):
        self.name = name
        self._collection = collection

    def delete(self):
        if self._collection is not None and self in self._collection:
            self._collection.remove(self)
        return True


class _FakeModeler:
    def __init__(self):
        self.model_units = "mm"
        self.object_names = []
        self.created_materials = {}
        self.created_polylines = {}

    def _create(self, name):
        self.object_names.append(name)
        return _FakeNamedObject(name)

    def create_box(self, **kwargs):
        self.created_materials[kwargs["name"]] = kwargs["material"]
        return self._create(kwargs["name"])

    def create_rectangle(self, **kwargs):
        return self._create(kwargs["name"])

    def create_polyline(self, **kwargs):
        self.created_polylines[kwargs["name"]] = kwargs["points"]
        return self._create(kwargs["name"])

    def create_region(self, **kwargs):
        return self._create(kwargs["name"])

    def delete(self, assignment=None):
        self.object_names = []
        return True


class _FakeMaterial:
    def __init__(self, name):
        self.name = name
        self.permittivity = None
        self.dielectric_loss_tangent = None


class _FakeMaterials:
    def __init__(self):
        self._materials = {}

    @property
    def material_keys(self):
        return list(self._materials)

    def add_material(self, name):
        material = _FakeMaterial(name)
        self._materials[name.lower()] = material
        return material

    def __getitem__(self, name):
        return self._materials[name.lower()]


class _FakeSetup:
    def __init__(self, name):
        self.name = name
        self.props = {}

    def update(self):
        return True


class _FakeSolutionData:
    def __init__(self, values):
        self._values = values

    def data_real(self, expression):
        return self._values.get(expression, [])


class _FakeAntennaReport:
    def __init__(self, expression, values):
        self._expression = expression
        self._values = values

    def get_solution_data(self):
        return _FakeSolutionData({self._expression: self._values[self._expression]})


class _FakeReportsByCategory:
    def __init__(self):
        self.calls = []
        self.values = {
            "dB(PeakGain)": [5.25],
            "RadiationEfficiency": [0.91],
        }

    def antenna_parameters(self, expressions, setup, infinite_sphere):
        self.calls.append((expressions, setup, infinite_sphere))
        return _FakeAntennaReport(expressions, self.values)


class _FakePost:
    def __init__(self):
        self.reports_by_category = _FakeReportsByCategory()


class _FakeLogger:
    def __init__(self, errors=None):
        self._errors = errors or []

    def get_messages(self, **kwargs):
        return type("Messages", (), {"error_level": self._errors})()


class _FakeHfss:
    def __init__(self, fail_port=False, aedt_errors=None):
        self.modeler = _FakeModeler()
        self.materials = _FakeMaterials()
        self.field_setups = []
        self.boundaries = []
        self.setup_names = []
        self.excitation_names = []
        self.existing_analysis_sweeps = []
        self.fail_port = fail_port
        self.port_integration_line = None
        self.axis_directions = type("AxisDirections", (), {"ZPos": 5})()
        self.release_calls = []
        self.post = _FakePost()
        self.logger = _FakeLogger(aedt_errors)
        self.project_name = "test_project"
        self.design_name = "antenna"
        self.solve_ok = True
        self.saved_paths = []

    def __setitem__(self, name, value):
        pass

    def assign_perfecte_to_sheets(self, assignment, name, is_infinite_ground):
        boundary = _FakeNamedObject(name, self.boundaries)
        self.boundaries.append(boundary)
        return boundary

    def lumped_port(self, **kwargs):
        self.port_integration_line = kwargs["integration_line"]
        if self.fail_port:
            return False
        self.excitation_names.append(kwargs["name"])
        boundary = _FakeNamedObject(kwargs["name"], self.boundaries)
        self.boundaries.append(boundary)
        return boundary

    def assign_radiation_boundary_to_objects(self, assignment, name):
        boundary = _FakeNamedObject(name, self.boundaries)
        self.boundaries.append(boundary)
        return boundary

    def insert_infinite_sphere(self, **kwargs):
        sphere = _FakeNamedObject(kwargs["name"], self.field_setups)
        self.field_setups.append(sphere)
        return sphere

    def create_setup(self, name, setup_type):
        self.setup_names.append(name)
        return _FakeSetup(name)

    def create_linear_count_sweep(self, setup, name, **kwargs):
        self.existing_analysis_sweeps.extend([f"{setup} : LastAdaptive", f"{setup} : {name}"])
        return _FakeNamedObject(name)

    def validate_full_design(self, ports):
        return ["Design validation check PASSED."], True

    def analyze_setup(self, name):
        return self.solve_ok

    def save_project(self, path=None):
        self.saved_paths.append(path)
        return True

    def release_desktop(self, close_projects, close_desktop):
        self.release_calls.append((close_projects, close_desktop))
        return True


class TrustedBuilderTests(unittest.TestCase):
    @staticmethod
    def _client(backend):
        client = PyAEDTHFSSClient()
        client._hfss = backend
        client._project_path = Path("candidate_001.aedt").resolve()
        return client

    def test_builder_derives_lumped_port_line_from_sheet_geometry(self):
        backend = _FakeHfss()
        client = self._client(backend)

        result = client.create_patch_antenna(valid_spec())

        self.assertTrue(result.success, result.message)
        self.assertEqual(backend.port_integration_line, backend.axis_directions.ZPos)
        self.assertEqual(
            backend.modeler.created_polylines["Port1_sheet"],
            [
                [-4.8, -50.0, 0],
                [4.8, -50.0, 0],
                [4.8, -50.0, 3.175],
                [-4.8, -50.0, 3.175],
            ],
        )
        self.assertEqual(backend.modeler.created_materials["Substrate"], "AntennaSubstrate")

    def test_candidate_projects_are_saved_as_numbered_copies(self):
        backend = _FakeHfss()
        client = self._client(backend)

        first = client._prepare_candidate_project()
        second = client._prepare_candidate_project()

        self.assertEqual(first.name, "candidate_001.aedt")
        self.assertEqual(second.name, "candidate_002.aedt")
        self.assertEqual(backend.saved_paths, [str(second)])

    def test_disconnect_keeps_saved_aedt_project_open_by_default(self):
        backend = _FakeHfss()
        client = self._client(backend)

        result = client.disconnect()

        self.assertTrue(result.success, result.message)
        self.assertEqual(backend.release_calls, [(False, False)])
        self.assertIn("保持打开", result.message)

    def test_disconnect_can_close_aedt_when_configured(self):
        backend = _FakeHfss()
        client = PyAEDTHFSSClient()
        client._hfss = backend
        client._keep_open = False

        result = client.disconnect()

        self.assertTrue(result.success, result.message)
        self.assertEqual(backend.release_calls, [(True, True)])
        self.assertIn("关闭 AEDT", result.message)

    def test_builder_stops_when_pyaedt_returns_false_for_port(self):
        backend = _FakeHfss(
            fail_port=True,
            aedt_errors=["[error] Both endpoints of port lines must lie on the port."],
        )
        client = self._client(backend)

        result = client.create_patch_antenna(valid_spec())

        self.assertFalse(result.success)
        self.assertIn("Port1", result.message)
        self.assertIn("Both endpoints", result.message)
        self.assertEqual(len(result.data["aedt_errors"]), 1)
        self.assertEqual(backend.setup_names, [])

    def test_solve_failure_includes_recent_aedt_errors(self):
        backend = _FakeHfss(aedt_errors=["Port 'Port1' does not contain any valid triangles."])
        client = self._client(backend)
        built = client.create_patch_antenna(valid_spec())
        backend.solve_ok = False

        result = client.solve()

        self.assertTrue(built.success, built.message)
        self.assertFalse(result.success)
        self.assertIn("does not contain any valid triangles", result.message)
        self.assertEqual(result.data["aedt_errors"], backend.logger._errors)

    def test_antenna_metrics_use_specialized_report_with_sphere_context(self):
        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        client = PyAEDTHFSSClient()
        client._hfss = backend

        metrics = client._antenna_metrics()

        self.assertEqual(metrics["peak_gain_dbi"], 5.25)
        self.assertEqual(metrics["radiation_efficiency_percent"], 91.0)
        self.assertEqual(
            backend.post.reports_by_category.calls,
            [
                ("dB(PeakGain)", "Setup1 : LastAdaptive", "InfiniteSphere1"),
                ("RadiationEfficiency", "Setup1 : LastAdaptive", "InfiniteSphere1"),
            ],
        )


class MetricMathTests(unittest.TestCase):
    def test_frequency_values_are_converted_from_declared_unit(self):
        values = PyAEDTHFSSClient._frequency_values_hz([2.2, 2.45, 2.7], "GHz")
        self.assertEqual(values, [2.2e9, 2.45e9, 2.7e9])

    def test_bandwidth_uses_interpolated_threshold_crossings(self):
        import numpy as np

        freq = np.asarray([2.3e9, 2.4e9, 2.45e9, 2.5e9, 2.6e9])
        s11 = np.asarray([-5.0, -10.0, -20.0, -10.0, -5.0])
        self.assertEqual(PyAEDTHFSSClient._bandwidth_hz(freq, s11), 100e6)


if __name__ == "__main__":
    unittest.main()
