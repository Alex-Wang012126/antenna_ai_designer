import json
import math
import tempfile
import unittest
from pathlib import Path

from config import Config
from design_loop import DesignAgent
from design_spec import PatchAntennaDesign
from evaluator import AntennaEvaluator
from hfss_client import HFSSResult, PlaceholderHFSSClient, PyAEDTHFSSClient
from model_client import PlaceholderModelClient, ResponsesModelClient, ToolCall, _normalized_usage
from prompts import build_tools_description
from task_spec import AntennaTaskSpec, load_default_task


def valid_spec():
    return {
        "substrate_width_mm": 100.0,
        "substrate_length_mm": 85.0,
        "substrate_height_mm": 5.0,
        "patch_width_mm": 58.0,
        "patch_length_mm": 38.4,
        "feed_width_mm": 12.0,
        "inset_depth_mm": 10.5,
        "inset_gap_mm": 1.4,
    }


class PatchAntennaSpecTests(unittest.TestCase):
    def test_accepts_complete_valid_spec(self):
        task = load_default_task()
        spec = task.validate_design(valid_spec())
        self.assertIsInstance(spec, PatchAntennaDesign)
        self.assertEqual(spec.patch_length_mm, 38.4)
        resolved = task.resolve_patch_spec(valid_spec())
        self.assertEqual(resolved.center_frequency_ghz, 2.45)
        self.assertEqual(resolved.sweep_points, 301)

    def test_rejects_missing_and_unknown_fields(self):
        missing = valid_spec()
        del missing["feed_width_mm"]
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            load_default_task().validate_design(missing)

        extra = valid_spec()
        extra["script"] = "unsafe"
        with self.assertRaisesRegex(ValueError, "unknown or read-only fields"):
            load_default_task().validate_design(extra)

    def test_rejects_non_finite_and_invalid_geometry(self):
        non_finite = valid_spec()
        non_finite["patch_width_mm"] = math.nan
        with self.assertRaises(ValueError):
            load_default_task().validate_design(non_finite)

        invalid = valid_spec()
        invalid["patch_width_mm"] = 30.0
        invalid["feed_width_mm"] = 20.0
        invalid["inset_gap_mm"] = 5.0
        with self.assertRaisesRegex(ValueError, "inset gaps"):
            load_default_task().validate_design(invalid)

        for field_name in ("inset_depth_mm", "inset_gap_mm"):
            degenerate = valid_spec()
            degenerate[field_name] = 0.0
            with self.assertRaises(ValueError):
                load_default_task().validate_design(degenerate)

    def test_model_tool_excludes_all_read_only_controls(self):
        properties = build_tools_description()[0]["function"]["parameters"]["properties"]
        self.assertEqual(set(properties), set(PatchAntennaDesign.FIELD_NAMES))
        for read_only in (
            "center_frequency_ghz",
            "substrate_permittivity",
            "air_margin_xy_mm",
            "sweep_points",
        ):
            self.assertNotIn(read_only, properties)

    def test_task_cannot_claim_calibration_without_witness_metadata(self):
        task_data = load_default_task().to_dict()
        task_data["calibration"].update(
            {
                "status": "verified",
                "full_score_witness_verified": True,
                "verification_date": None,
                "solver": None,
            }
        )

        with self.assertRaisesRegex(ValueError, "verification_date"):
            AntennaTaskSpec.from_mapping(task_data)

    def test_efficiency_sweep_must_cover_fixed_target_frequency_window(self):
        task_data = load_default_task().to_dict()
        task_data["simulation_control"]["efficiency_sweep"]["start"]["value"] = 2.426

        with self.assertRaisesRegex(ValueError, "fixed target-frequency efficiency window"):
            AntennaTaskSpec.from_mapping(task_data)

    def test_efficiency_sweep_must_save_solution_fields(self):
        task_data = load_default_task().to_dict()
        task_data["simulation_control"]["efficiency_sweep"]["save_fields"] = False

        with self.assertRaisesRegex(ValueError, "save_fields must be true"):
            AntennaTaskSpec.from_mapping(task_data)


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

    def test_model_usage_is_normalized_for_both_api_styles(self):
        responses_usage = _normalized_usage(
            {
                "input_tokens": 100,
                "output_tokens": 40,
                "total_tokens": 140,
                "output_tokens_details": {"reasoning_tokens": 30},
            }
        )
        chat_usage = _normalized_usage(
            {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100}
        )
        self.assertEqual(responses_usage["reasoning_tokens"], 30)
        self.assertEqual(chat_usage["input_tokens"], 80)
        self.assertEqual(chat_usage["output_tokens"], 20)

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
                ["build", "validation", "solve", "save", "metrics"],
            )
            self.assertTrue(result.log_file.is_file())
            self.assertTrue(result.manifest_file.is_file())
            self.assertTrue(result.metrics_file.is_file())
            self.assertTrue(result.task_file.is_file())
            self.assertTrue(result.resource_file.is_file())
            self.assertTrue(Path(result.selected_candidate["project_file"]).is_file())
            artifact_parents = {
                result.log_file.parent,
                result.manifest_file.parent,
                result.metrics_file.parent,
                result.task_file.parent,
                result.resource_file.parent,
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
            self.assertEqual(manifest["protocol_version"], 3)
            resources = json.loads(result.resource_file.read_text(encoding="utf-8"))
            self.assertEqual(resources["model"]["attempted_calls"], 2)
            self.assertEqual(resources["model"]["token_totals"]["total_tokens"], 0)

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

    def test_solved_project_is_saved_before_metrics_failure(self):
        class FailMetricsClient(PlaceholderHFSSClient):
            def get_result(self, metric):
                if metric == "all":
                    return HFSSResult(
                        success=False,
                        data={"_fatal_backend_error": True},
                        message="simulated report failure",
                    )
                return super().get_result(metric)

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            config = Config(project_dir=run_dir, log_dir=run_dir)
            hfss = FailMetricsClient()
            hfss.connect(config)
            agent = DesignAgent(
                model_client=PlaceholderModelClient(),
                hfss_client=hfss,
                requirements="test",
                config=config,
            )

            result = agent._execute_tool(ToolCall("create_patch_antenna", valid_spec()))

            self.assertFalse(result.success)
            candidate = agent._candidates[-1]
            self.assertEqual(candidate["status"], "metrics_failed")
            self.assertEqual(
                list(candidate["stages"]),
                ["build", "validation", "solve", "save", "metrics"],
            )
            self.assertTrue(candidate["stages"]["save"]["success"])
            self.assertTrue(Path(candidate["project_file"]).is_file())
            self.assertTrue(agent._finalized)
            self.assertEqual(agent._stop_reason, "hfss_metrics_backend_failure")
            hfss.disconnect()

    def test_keyboard_interrupt_still_writes_run_artifacts(self):
        class InterruptingModel(PlaceholderModelClient):
            def chat(self, messages, tools=None):
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            result = DesignAgent(
                model_client=InterruptingModel(),
                hfss_client=PlaceholderHFSSClient(),
                requirements="test",
                config=Config(project_dir=run_dir, log_dir=run_dir),
            ).run(max_iterations=1)

            self.assertFalse(result.success)
            manifest = json.loads(result.manifest_file.read_text(encoding="utf-8"))
            resources = json.loads(result.resource_file.read_text(encoding="utf-8"))
            self.assertEqual(manifest["stop_reason"], "keyboard_interrupt")
            self.assertTrue(result.log_file.is_file())
            self.assertTrue(resources["model"]["calls"][0]["interrupted"])


class EvaluatorFallbackTests(unittest.TestCase):
    def test_evaluator_scores_latest_successful_candidate_even_when_worse(self):
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
                            "s11_min_db": -22.0,
                            "bandwidth_mhz": 120.0,
                            "peak_gain_dbi": 8.0,
                            "total_efficiency_mean_percent": 90.0,
                        },
                        "specification": valid_spec(),
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
                            "total_efficiency_mean_percent": 60.0,
                        },
                        "specification": {**valid_spec(), "patch_length_mm": 40.0},
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
                "This text is non-authoritative and may contain S11 < 0 dB.",
                manifest_file=manifest_file,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.selected_iteration, 2)
            self.assertEqual(result.selected_project_file, projects[1])
            self.assertLess(result.score, 100.0)
            self.assertEqual(
                [item["iteration"] for item in result.candidate_evaluations],
                [1, 2, 3],
            )
            self.assertTrue(result.candidate_evaluations[1]["selected_for_scoring"])
            self.assertFalse(result.candidate_evaluations[2]["pipeline_success"])
            self.assertEqual(result.report_file.parent, run_dir)
            self.assertTrue(result.verification_file.is_file())
            verification = json.loads(result.verification_file.read_text(encoding="utf-8"))
            self.assertEqual(verification["project_file"], str(projects[1]))
            self.assertEqual(verification["aedt_objects"]["frequency_sweep"], "Sweep1")
            self.assertEqual(
                verification["aedt_objects"]["efficiency_sweep"], "EfficiencySweep"
            )


class _FakeNamedObject:
    def __init__(self, name, collection=None, on_delete=None):
        self.name = name
        self._collection = collection
        self._on_delete = on_delete

    def delete(self):
        if self._collection is not None and self in self._collection:
            self._collection.remove(self)
        if self._on_delete is not None:
            self._on_delete()
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
    def __init__(self, values, frequencies_ghz=None):
        self._values = values
        self.primary_sweep_values = frequencies_ghz or []
        self.units_sweeps = {"Freq": "GHz"}
        self.intrinsics = {"Freq": self.primary_sweep_values}

    def data_real(self, expression):
        return self._values.get(expression, [])


class _FakeAntennaReport:
    def __init__(self, expression, values, frequencies_ghz):
        self._expression = expression
        self._values = values
        self._frequencies_ghz = frequencies_ghz

    def get_solution_data(self):
        return _FakeSolutionData(
            {self._expression: self._values[self._expression]},
            self._frequencies_ghz.get(self._expression),
        )


class _FakeReportsByCategory:
    def __init__(self):
        self.calls = []
        self.values = {
            "dB(PeakGain)": [5.25],
            "RadiationEfficiency": [0.91, 0.91, 0.91, 0.91, 0.91],
        }
        self.frequencies_ghz = {
            "dB(PeakGain)": [2.45],
            "RadiationEfficiency": [2.4, 2.425, 2.45, 2.475, 2.5],
        }

    def antenna_parameters(self, expressions, setup, infinite_sphere):
        self.calls.append((expressions, setup, infinite_sphere))
        return _FakeAntennaReport(expressions, self.values, self.frequencies_ghz)


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
        self.port_kwargs = None
        self.sphere_kwargs = None
        self.sweep_kwargs = None
        self.sweep_calls = []
        self.last_setup = None
        self.axis_directions = type("AxisDirections", (), {"ZPos": 5})()
        self.release_calls = []
        self.post = _FakePost()
        self.logger = _FakeLogger(aedt_errors)
        self.project_name = "test_project"
        self.design_name = "antenna"
        self.solve_ok = True
        self.saved_paths = []
        self.save_refresh_ids = []
        self.new_project_calls = []
        self.insert_design_calls = []
        self.delete_design_calls = []
        self.rename_design_calls = []
        self.close_project_calls = []

    def __setitem__(self, name, value):
        pass

    def create_new_project(self, name):
        self.new_project_calls.append(name)
        self.project_name = name
        self.modeler = _FakeModeler()
        self.materials = _FakeMaterials()
        self.field_setups = []
        self.boundaries = []
        self.setup_names = []
        self.excitation_names = []
        self.existing_analysis_sweeps = []
        self.last_setup = None
        return True

    def insert_design(self, name, solution_type):
        self.insert_design_calls.append((name, solution_type))
        self.design_name = name
        self.modeler = _FakeModeler()
        self.field_setups = []
        self.boundaries = []
        self.setup_names = []
        self.excitation_names = []
        self.existing_analysis_sweeps = []
        self.last_setup = None
        return name

    def delete_design(self, name=None, fallback_design=None):
        self.delete_design_calls.append((name, fallback_design))
        return True

    def rename_design(self, name, save=True):
        self.rename_design_calls.append((name, save))
        self.design_name = name
        return True

    def close_project(self, name=None, save=True):
        self.close_project_calls.append((name, save))
        return True

    def assign_perfecte_to_sheets(self, assignment, name, is_infinite_ground):
        boundary = _FakeNamedObject(name, self.boundaries)
        self.boundaries.append(boundary)
        return boundary

    def lumped_port(self, **kwargs):
        self.port_integration_line = kwargs["integration_line"]
        self.port_kwargs = kwargs
        if self.fail_port:
            return False
        self.excitation_names.append(kwargs["name"])
        boundary = _FakeNamedObject(
            kwargs["name"],
            self.boundaries,
            on_delete=lambda: self.excitation_names.remove(kwargs["name"])
            if kwargs["name"] in self.excitation_names
            else None,
        )
        self.boundaries.append(boundary)
        return boundary

    def assign_radiation_boundary_to_objects(self, assignment, name):
        boundary = _FakeNamedObject(name, self.boundaries)
        self.boundaries.append(boundary)
        return boundary

    def insert_infinite_sphere(self, **kwargs):
        self.sphere_kwargs = kwargs
        sphere = _FakeNamedObject(kwargs["name"], self.field_setups)
        self.field_setups.append(sphere)
        return sphere

    def create_setup(self, name, setup_type):
        self.setup_names.append(name)
        self.last_setup = _FakeSetup(name)
        return self.last_setup

    def delete_setup(self, name):
        if name in self.setup_names:
            self.setup_names.remove(name)
        self.existing_analysis_sweeps = [
            sweep for sweep in self.existing_analysis_sweeps
            if not sweep.startswith(f"{name} :")
        ]
        self.last_setup = None
        return True

    def create_linear_count_sweep(self, setup, name, **kwargs):
        self.sweep_kwargs = {"setup": setup, "name": name, **kwargs}
        self.sweep_calls.append(self.sweep_kwargs)
        self.existing_analysis_sweeps.extend([f"{setup} : LastAdaptive", f"{setup} : {name}"])
        return _FakeNamedObject(name)

    def validate_full_design(self, ports):
        return ["Design validation check PASSED."], True

    def analyze_setup(self, name):
        return self.solve_ok

    def save_project(self, path=None, refresh_ids=False):
        self.saved_paths.append(path)
        self.save_refresh_ids.append(refresh_ids)
        return True

    def release_desktop(self, close_projects, close_desktop):
        self.release_calls.append((close_projects, close_desktop))
        return True


class TrustedBuilderTests(unittest.TestCase):
    @staticmethod
    def _client(backend):
        client = PyAEDTHFSSClient()
        client.configure_task(load_default_task())
        client._hfss = backend
        client._project_path = Path("candidate_001.aedt").resolve()
        return client

    def test_builder_derives_lumped_port_line_from_sheet_geometry(self):
        backend = _FakeHfss()
        client = self._client(backend)

        result = client.create_patch_antenna(valid_spec())

        self.assertTrue(result.success, result.message)
        self.assertEqual(backend.port_integration_line, backend.axis_directions.ZPos)
        self.assertEqual(backend.port_kwargs["impedance"], 50.0)
        self.assertEqual(backend.last_setup.props["Frequency"], "2.45GHz")
        self.assertEqual(backend.last_setup.props["MaxDeltaS"], 0.02)
        self.assertEqual(len(backend.sweep_calls), 2)
        main_sweep, efficiency_sweep = backend.sweep_calls
        self.assertEqual(main_sweep["name"], "Sweep1")
        self.assertEqual(main_sweep["start_frequency"], 2.3)
        self.assertEqual(main_sweep["stop_frequency"], 2.6)
        self.assertEqual(main_sweep["num_of_freq_points"], 301)
        self.assertEqual(main_sweep["sweep_type"], "Interpolating")
        self.assertFalse(main_sweep["save_fields"])
        self.assertFalse(main_sweep["save_rad_fields"])
        self.assertEqual(efficiency_sweep["name"], "EfficiencySweep")
        self.assertEqual(efficiency_sweep["start_frequency"], 2.4)
        self.assertEqual(efficiency_sweep["stop_frequency"], 2.5)
        self.assertEqual(efficiency_sweep["num_of_freq_points"], 11)
        self.assertEqual(efficiency_sweep["sweep_type"], "Discrete")
        self.assertTrue(efficiency_sweep["save_fields"])
        self.assertTrue(efficiency_sweep["save_rad_fields"])
        self.assertEqual(backend.sphere_kwargs["x_step"], 5.0)
        self.assertEqual(
            backend.modeler.created_polylines["Port1_sheet"],
            [
                [-6.0, -42.5, 0],
                [6.0, -42.5, 0],
                [6.0, -42.5, 5.0],
                [-6.0, -42.5, 5.0],
            ],
        )
        self.assertEqual(backend.modeler.created_materials["Substrate"], "AntennaSubstrate")

    def test_candidate_projects_are_numbered_without_inserting_a_new_design(self):
        backend = _FakeHfss()
        client = self._client(backend)

        first = client._prepare_candidate_project()
        second = client._prepare_candidate_project()

        self.assertEqual(first.name, "candidate_001.aedt")
        self.assertEqual(second.name, "candidate_002.aedt")
        self.assertEqual(backend.saved_paths, [str(second)])
        self.assertEqual(backend.save_refresh_ids, [False])
        self.assertEqual(backend.new_project_calls, [])
        self.assertEqual(backend.insert_design_calls, [])
        self.assertEqual(backend.delete_design_calls, [])
        self.assertEqual(backend.rename_design_calls, [])
        self.assertEqual(backend.close_project_calls, [])

    def test_second_candidate_build_does_not_reuse_previous_sweeps(self):
        backend = _FakeHfss()
        client = self._client(backend)
        second_spec = valid_spec()
        second_spec["patch_length_mm"] = 39.0

        first = client.create_patch_antenna(valid_spec())
        second = client.create_patch_antenna(second_spec)

        self.assertTrue(first.success, first.message)
        self.assertTrue(second.success, second.message)
        self.assertEqual(
            backend.existing_analysis_sweeps,
            [
                "Setup1 : LastAdaptive",
                "Setup1 : Sweep1",
                "Setup1 : LastAdaptive",
                "Setup1 : EfficiencySweep",
            ],
        )
        self.assertEqual([call["name"] for call in backend.sweep_calls[-2:]], [
            "Sweep1",
            "EfficiencySweep",
        ])

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

    def test_disconnect_skips_redundant_save_after_fatal_postprocessing_failure(self):
        backend = _FakeHfss()
        client = PyAEDTHFSSClient()
        client._hfss = backend
        client._postprocessing_unhealthy = True

        result = client.disconnect()

        self.assertTrue(result.success, result.message)
        self.assertEqual(backend.saved_paths, [])
        self.assertEqual(backend.release_calls, [(False, False)])
        self.assertIn("工程检查点", result.message)

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
        import numpy as np

        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        client = PyAEDTHFSSClient()
        client._hfss = backend

        frequency_hz = np.asarray([2.4e9, 2.425e9, 2.45e9, 2.475e9, 2.5e9])
        s11_db = np.asarray([-10.0, -15.0, -20.0, -15.0, -10.0])
        metrics = client._antenna_metrics(frequency_hz, s11_db)

        self.assertEqual(metrics["peak_gain_dbi"], 5.25)
        expected = 100.0 * 0.91 * (
            2.0 * (1.0 - 10.0 ** (-15.0 / 10.0))
            + (1.0 - 10.0 ** (-20.0 / 10.0))
        ) / 3.0
        self.assertAlmostEqual(metrics["total_efficiency_mean_percent"], expected)
        self.assertEqual(metrics["total_efficiency_sample_count"], 3)
        self.assertAlmostEqual(metrics["radiation_efficiency_raw_mean_ratio"], 0.91)
        self.assertEqual(
            backend.post.reports_by_category.calls,
            [
                ("dB(PeakGain)", "Setup1 : LastAdaptive", "InfiniteSphere1"),
                ("RadiationEfficiency", "Setup1 : EfficiencySweep", "InfiniteSphere1"),
            ],
        )

    def test_total_efficiency_treats_radiation_efficiency_as_ratio_and_clips_roundoff(self):
        import numpy as np

        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        backend.post.reports_by_category.values["RadiationEfficiency"] = [1.03] * 5
        client = PyAEDTHFSSClient()
        client._hfss = backend

        frequency_hz = np.asarray([2.4e9, 2.425e9, 2.45e9, 2.475e9, 2.5e9])
        s11_db = np.asarray([-10.0, -15.0, -20.0, -15.0, -10.0])
        metrics = client._antenna_metrics(frequency_hz, s11_db)

        self.assertGreater(metrics["total_efficiency_mean_percent"], 99.0)
        self.assertLessEqual(metrics["total_efficiency_max_percent"], 100.0)
        self.assertGreater(metrics["total_efficiency_raw_max_percent"], 100.0)
        self.assertAlmostEqual(metrics["radiation_efficiency_raw_mean_ratio"], 1.03)

    def test_total_efficiency_rejects_efficiency_sweep_extrapolation(self):
        import numpy as np

        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        backend.post.reports_by_category.values["RadiationEfficiency"] = [0.9, 0.9, 0.9]
        backend.post.reports_by_category.frequencies_ghz["RadiationEfficiency"] = [
            2.44,
            2.45,
            2.46,
        ]
        client = PyAEDTHFSSClient()
        client._hfss = backend
        frequency_hz = np.asarray([2.4e9, 2.425e9, 2.45e9, 2.475e9, 2.5e9])
        s11_db = np.asarray([-10.0, -15.0, -20.0, -15.0, -10.0])

        with self.assertRaisesRegex(RuntimeError, "禁止外推"):
            client._total_efficiency_metrics(frequency_hz, s11_db, "InfiniteSphere1")

    def test_sweep_edge_resonance_uses_fixed_target_efficiency_window(self):
        import numpy as np

        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        client = self._client(backend)
        frequency_hz = np.asarray([2.3e9, 2.45e9, 2.576e9, 2.6e9])
        s11_db = np.asarray([-5.0, -8.0, -20.0, -10.0])
        client._s11_curve = lambda: (frequency_hz, s11_db)

        result = client.get_metrics()

        self.assertTrue(result.success, result.message)
        self.assertAlmostEqual(result.data["center_freq_ghz"], 2.576)
        self.assertIn("total_efficiency_mean_percent", result.data)
        self.assertEqual(result.data["total_efficiency_window_center_ghz"], 2.45)
        self.assertEqual(
            result.data["total_efficiency_window_reference"], "target_frequency"
        )
        self.assertNotIn("metric_errors", result.data)
        self.assertNotIn("_fatal_backend_error", result.data)
        self.assertEqual(
            backend.post.reports_by_category.calls,
            [
                ("dB(PeakGain)", "Setup1 : LastAdaptive", "InfiniteSphere1"),
                ("RadiationEfficiency", "Setup1 : EfficiencySweep", "InfiniteSphere1"),
            ],
        )

    def test_reasonably_detuned_candidate_remains_inside_efficiency_coverage(self):
        import numpy as np

        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        client = self._client(backend)
        frequency_hz = np.arange(2.3e9, 2.6001e9, 1e6)
        s11_db = -5.0 - 20.0 * np.exp(-((frequency_hz - 2.411e9) / 18e6) ** 2)
        client._s11_curve = lambda: (frequency_hz, s11_db)

        result = client.get_metrics()

        self.assertTrue(result.success, result.message)
        self.assertAlmostEqual(result.data["center_freq_ghz"], 2.411)
        self.assertIn("total_efficiency_mean_percent", result.data)
        self.assertEqual(result.data["total_efficiency_window_center_ghz"], 2.45)
        self.assertEqual(
            result.data["total_efficiency_window_reference"], "target_frequency"
        )
        self.assertNotIn("metric_errors", result.data)

    def test_missing_efficiency_report_marks_backend_unhealthy(self):
        import numpy as np

        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        backend.post.reports_by_category.values["RadiationEfficiency"] = []
        client = PyAEDTHFSSClient()
        client._hfss = backend
        frequency_hz = np.asarray([2.4e9, 2.425e9, 2.45e9, 2.475e9, 2.5e9])
        s11_db = np.asarray([-10.0, -15.0, -20.0, -15.0, -10.0])

        metrics = client._antenna_metrics(frequency_hz, s11_db)

        self.assertTrue(metrics["_fatal_backend_error"])
        self.assertIn("数据为空", metrics["metric_errors"]["total_efficiency"])
        self.assertTrue(client._postprocessing_unhealthy)

    def test_missing_peak_gain_report_marks_backend_unhealthy(self):
        import numpy as np

        backend = _FakeHfss()
        backend.field_setups.append(_FakeNamedObject("InfiniteSphere1"))
        backend.post.reports_by_category.values["dB(PeakGain)"] = []
        client = PyAEDTHFSSClient()
        client._hfss = backend
        frequency_hz = np.asarray([2.4e9, 2.425e9, 2.45e9, 2.475e9, 2.5e9])
        s11_db = np.asarray([-10.0, -15.0, -20.0, -15.0, -10.0])

        metrics = client._antenna_metrics(frequency_hz, s11_db)

        self.assertTrue(metrics["_fatal_backend_error"])
        self.assertIn("数据为空", metrics["metric_errors"]["peak_gain"])
        self.assertTrue(client._postprocessing_unhealthy)


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
