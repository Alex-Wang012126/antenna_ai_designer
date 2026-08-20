"""HFSS / AEDT 调用接口

封装所有与 HFSS/AEDT 交互的操作，两个实现：
- PlaceholderHFSSClient：占位客户端，不连接真实软件，仅打印调用并返回演示数据；
- PyAEDTHFSSClient：基于 PyAEDT 的真实客户端（Ansys Electronics Desktop Student 2025 R2）。

真实客户端在关键节点打印 [PyAEDT] 前缀的调试输出，便于排查连接/求解/读数问题。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from config import Config, cfg
from design_spec import PatchAntennaSpec


def _dbg(msg: str) -> None:
    """真实客户端调试输出：统一 [PyAEDT] 前缀，便于在日志里检索定位。"""
    print(f"[PyAEDT] {msg}", flush=True)


@dataclass
class HFSSResult:
    """HFSS 操作返回的通用结果。"""
    success: bool
    data: Any = None
    message: str = ""


class HFSSClient(ABC):
    """HFSS 客户端抽象基类。"""

    @abstractmethod
    def connect(self, config: Optional[Config] = None) -> HFSSResult:
        """连接到 HFSS / AEDT。"""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> HFSSResult:
        """断开连接并清理资源。"""
        raise NotImplementedError

    @abstractmethod
    def set_design_variable(self, name: str, value: Union[str, float, int]) -> HFSSResult:
        """设置/更新设计变量。"""
        raise NotImplementedError

    @abstractmethod
    def get_design_variable(self, name: str) -> HFSSResult:
        """读取设计变量当前值。"""
        raise NotImplementedError

    @abstractmethod
    def create_patch_antenna(self, specification: Mapping[str, Any]) -> HFSSResult:
        """Create a complete patch antenna from a validated specification."""
        raise NotImplementedError

    @abstractmethod
    def validate_design(self) -> HFSSResult:
        """Validate all required model, excitation, boundary, and setup state."""
        raise NotImplementedError

    @abstractmethod
    def solve(self, solution_name: Optional[str] = None) -> HFSSResult:
        """运行仿真求解。"""
        raise NotImplementedError

    @abstractmethod
    def get_result(self, metric: str) -> HFSSResult:
        """Read one of the supported result families."""
        raise NotImplementedError

    @abstractmethod
    def export_design(self, file_path: Optional[Union[str, Path]] = None) -> HFSSResult:
        """导出/保存设计文件。"""
        raise NotImplementedError

    @abstractmethod
    def get_metrics(self) -> HFSSResult:
        """一次性读取常用指标（S11、增益、带宽等），供评测脚本使用。"""
        raise NotImplementedError


class PlaceholderHFSSClient(HFSSClient):
    """占位 HFSS 客户端：不连接真实软件，仅打印并返回演示数据。"""

    def __init__(self):
        self._connected = False
        self._variables: Dict[str, Any] = {}
        self._project_dir: Path = cfg.project_dir
        self._project_path: Optional[Path] = None
        self._candidate_index = 0
        self._has_design = False

    def connect(self, config: Optional[Config] = None) -> HFSSResult:
        if config is None:
            config = cfg
        self._project_dir = config.project_dir
        self._project_dir.mkdir(parents=True, exist_ok=True)
        self._project_path = None
        self._candidate_index = 0
        self._connected = True
        print(f"[HFSS 占位] 已连接到项目目录: {self._project_dir}")
        return HFSSResult(success=True, message="Connected (placeholder)")

    def disconnect(self) -> HFSSResult:
        self._connected = False
        print("[HFSS 占位] 已断开连接")
        return HFSSResult(success=True, message="Disconnected (placeholder)")

    def _check_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("HFSS client is not connected. Call connect() first.")

    def set_design_variable(self, name: str, value: Union[str, float, int]) -> HFSSResult:
        self._check_connected()
        self._variables[name] = value
        print(f"[HFSS 占位] set_variable: {name} = {value}")
        return HFSSResult(success=True, data={name: value}, message=f"Set {name}={value}")

    def get_design_variable(self, name: str) -> HFSSResult:
        self._check_connected()
        value = self._variables.get(name)
        print(f"[HFSS 占位] get_variable: {name} = {value}")
        return HFSSResult(success=True, data=value, message=f"{name}={value}")

    def create_patch_antenna(self, specification: Mapping[str, Any]) -> HFSSResult:
        self._check_connected()
        try:
            spec = PatchAntennaSpec.from_mapping(specification)
        except ValueError as exc:
            return HFSSResult(success=False, message=f"Invalid patch specification: {exc}")
        self._candidate_index += 1
        self._project_path = self._project_dir / f"candidate_{self._candidate_index:03d}.aedt"
        self._has_design = True
        self._variables = spec.to_dict()
        print(f"[HFSS 占位] create_patch_antenna: {spec.to_dict()}")
        return HFSSResult(
            success=True,
            data={"specification": spec.to_dict(), "project_path": str(self._project_path.resolve())},
            message="Patch antenna created (placeholder)",
        )

    def validate_design(self) -> HFSSResult:
        self._check_connected()
        checks = {
            "geometry": self._has_design,
            "port": self._has_design,
            "radiation_boundary": self._has_design,
            "far_field_setup": self._has_design,
            "solution_setup": self._has_design,
            "frequency_sweep": self._has_design,
        }
        return HFSSResult(
            success=all(checks.values()),
            data={"checks": checks},
            message="Design validation passed (placeholder)" if self._has_design else "No design exists",
        )

    def solve(self, solution_name: Optional[str] = None) -> HFSSResult:
        self._check_connected()
        validation = self.validate_design()
        if not validation.success:
            return validation
        print(f"[HFSS 占位] solve(solution_name={solution_name})")
        return HFSSResult(
            success=True,
            data={"solution": solution_name or cfg.default_setup_sweep_name, "status": "converged"},
            message="Simulation completed (placeholder)",
        )

    def get_result(self, metric: str) -> HFSSResult:
        self._check_connected()
        print(f"[HFSS 占位] get_result(metric={metric})")
        if metric == "s11":
            data = {
                "freq_ghz": [2.30, 2.40, 2.45, 2.50, 2.60],
                "s11_db": [-5.0, -9.5, -21.0, -8.0, -4.0],
            }
        elif metric == "far_field":
            data = {"peak_gain_dbi": 5.2, "radiation_efficiency_percent": 92.0}
        elif metric == "all":
            data = self.get_metrics().data
        else:
            return HFSSResult(success=False, message=f"Unsupported metric: {metric}")
        return HFSSResult(success=True, data=data, message="Report data retrieved (placeholder)")

    def export_design(self, file_path: Optional[Union[str, Path]] = None) -> HFSSResult:
        self._check_connected()
        path = self._project_path or self._project_dir / "candidate_001.aedt"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 创建空占位文件
        path.write_text("# Placeholder HFSS design file\n", encoding="utf-8")
        print(f"[HFSS 占位] export_design -> {path}")
        return HFSSResult(success=True, data={"file_path": str(path)}, message=f"Saved to {path}")

    def get_metrics(self) -> HFSSResult:
        self._check_connected()
        metrics = {
            "center_freq_ghz": 2.45,
            "s11_min_db": -21.0,
            "bandwidth_mhz": 120.0,
            "peak_gain_dbi": 5.2,
            "radiation_efficiency_percent": 92.0,
        }
        print(f"[HFSS 占位] get_metrics: {metrics}")
        return HFSSResult(success=True, data=metrics, message="Metrics computed (placeholder)")


class PyAEDTHFSSClient(HFSSClient):
    """基于 PyAEDT 的真实 HFSS 客户端（AEDT Student 2025 R2）。

    所有关键节点都有 [PyAEDT] 前缀的调试输出，便于排查连接/求解/读数问题。
    """

    def __init__(self):
        self._hfss = None
        self._project_path: Optional[Path] = None
        self._candidate_index = 0
        self._setup_name: str = "Setup1"
        self._sweep_name: str = "Sweep1"
        self._keep_open: bool = True

    # ---------- 连接管理 ----------

    def connect(self, config: Optional[Config] = None) -> HFSSResult:
        config = config or cfg
        _dbg(">>> connect: 导入 ansys.aedt.core ...")
        try:
            from ansys.aedt.core import Hfss, settings
        except ImportError:
            return HFSSResult(
                success=False,
                message="未安装 PyAEDT：pip install ansys-aedt-core==0.19.0",
            )
        import ansys.aedt.core
        _dbg(f"pyaedt 版本: {getattr(ansys.aedt.core, '__version__', '未知')}")

        # AEDT Student 2025 R2 的 gRPC server 以 insecure 模式启动，
        # 必须关闭安全模式（与 verify_min.py 一致），否则连接会被拒
        settings.grpc_secure_mode = False
        _dbg("settings.grpc_secure_mode = False")

        project_dir = Path(config.project_dir).expanduser().resolve()   # 关键：转绝对路径
        project_dir.mkdir(parents=True, exist_ok=True)
        self._project_path = project_dir / "candidate_001.aedt"
        self._candidate_index = 0
        self._setup_name = config.default_setup_name
        self._sweep_name = config.default_sweep_name
        self._keep_open = config.aedt_keep_open
        _dbg(f"项目文件: {self._project_path.resolve()}")
        _dbg(f">>> 启动 AEDT: version={config.aedt_version}, student={config.aedt_student}, "
             f"non_graphical={config.aedt_non_graphical}, design={config.default_design_name}")

        try:
            self._hfss = Hfss(
                project=str(self._project_path),
                design=config.default_design_name,
                solution_type="Modal",
                version=config.aedt_version,
                student_version=config.aedt_student,
                non_graphical=config.aedt_non_graphical,
                new_desktop=True,
                close_on_exit=False,
                # 学生版 2025 R2 gRPC 连不上时改用 COM：
                # use_grpc_api=False,
            )
        except Exception as e:
            _dbg(f"<<< AEDT 启动失败: {type(e).__name__}: {e}")
            return HFSSResult(success=False, message=f"AEDT 启动失败: {type(e).__name__}: {e}")

        _dbg(f"<<< AEDT 已启动: {self._hfss.aedt_version_id}")
        return HFSSResult(success=True,
                          message=f"已连接 AEDT {self._hfss.aedt_version_id}, 项目: {self._project_path}")

    def disconnect(self) -> HFSSResult:
        if self._hfss is None:
            return HFSSResult(success=True, message="AEDT 已断开")

        errors: List[str] = []
        _dbg(">>> disconnect: 保存项目并释放自动化连接 ...")
        try:
            try:
                saved = self._hfss.save_project()
                if saved is False:
                    errors.append("save_project returned False")
            except Exception as exc:
                errors.append(f"save_project: {type(exc).__name__}: {exc}")
            try:
                released = self._hfss.release_desktop(
                    close_projects=not self._keep_open,
                    close_desktop=not self._keep_open,
                )
                if released is False:
                    errors.append("release_desktop returned False")
            except Exception as exc:
                errors.append(f"release_desktop: {type(exc).__name__}: {exc}")
            if not errors:
                if self._keep_open:
                    _dbg("<<< 项目已保存，AEDT 项目和窗口保持打开")
                else:
                    _dbg("<<< 项目已保存并关闭 AEDT")
            else:
                _dbg(f"<<< AEDT 释放存在问题: {'; '.join(errors)}")
        finally:
            self._hfss = None
        return HFSSResult(
            success=not errors,
            message=(
                "项目已保存，AEDT 项目和窗口保持打开"
                if self._keep_open
                else "项目已保存并关闭 AEDT"
            ) if not errors else f"释放 AEDT 自动化连接时出错: {'; '.join(errors)}",
        )

    def _check_connected(self) -> None:
        if self._hfss is None:
            raise RuntimeError("HFSS client is not connected. Call connect() first.")

    # ---------- 变量读写 ----------

    def set_design_variable(self, name: str, value) -> HFSSResult:
        self._check_connected()
        _dbg(f">>> set_variable: {name} = {value}")
        try:
            # 字符串原样传入（须带单位，如 "30mm"）；数字按 SI 单位处理
            self._hfss[name] = value if isinstance(value, str) else float(value)
            _dbg(f"<<< set_variable 成功: {name} = {value}")
            return HFSSResult(success=True, data={name: value}, message=f"Set {name}={value}")
        except Exception as e:
            _dbg(f"<<< set_variable 失败: {type(e).__name__}: {e}")
            return HFSSResult(success=False, message=f"set_variable 失败: {e}")

    def get_design_variable(self, name: str) -> HFSSResult:
        self._check_connected()
        _dbg(f">>> get_variable: {name}")
        try:
            expr = self._hfss.variable_manager[name].expression
            _dbg(f"<<< get_variable: {name} = {expr}")
            return HFSSResult(success=True, data=expr, message=f"{name}={expr}")
        except KeyError:
            _dbg(f"<<< get_variable 失败: 变量 {name} 不存在")
            return HFSSResult(success=False, message=f"变量 {name} 不存在")

    # ---------- 受约束建模 ----------

    @staticmethod
    def _require_created(value: Any, label: str) -> Any:
        """Reject PyAEDT operations that report failure by returning False/None."""
        if value is False or value is None:
            raise RuntimeError(f"PyAEDT failed to create {label}")
        return value

    def _recent_aedt_errors(self, limit: int = 3) -> List[str]:
        """Read recent AEDT message-window errors without masking the original failure."""
        try:
            messages = self._hfss.logger.get_messages(
                project_name=getattr(self._hfss, "project_name", None),
                design_name=getattr(self._hfss, "design_name", None),
                level=2,
                aedt_messages=True,
            )
            errors = [str(message).strip() for message in messages.error_level]
            return errors[-limit:]
        except Exception:
            return []

    def _prepare_candidate_project(self) -> Path:
        """Move each candidate to its own project file before replacing geometry."""
        next_index = self._candidate_index + 1
        next_path = self._project_path.parent / f"candidate_{next_index:03d}.aedt"
        if next_index > 1:
            saved = self._hfss.save_project(str(next_path))
            if saved is False:
                raise RuntimeError(f"PyAEDT failed to create candidate project copy: {next_path}")
            self._project_path = next_path
        self._candidate_index = next_index
        return self._project_path

    def _clear_current_design(self) -> None:
        """Remove the previous candidate before constructing a complete replacement."""
        for field_setup in list(getattr(self._hfss, "field_setups", None) or []):
            self._require_created(field_setup.delete(), f"delete field setup {field_setup.name}")
        for boundary in list(getattr(self._hfss, "boundaries", None) or []):
            self._require_created(boundary.delete(), f"delete boundary {boundary.name}")
        for setup_name in list(getattr(self._hfss, "setup_names", None) or []):
            self._require_created(self._hfss.delete_setup(setup_name), f"delete setup {setup_name}")
        object_names = list(getattr(self._hfss.modeler, "object_names", None) or [])
        if object_names:
            self._require_created(self._hfss.modeler.delete(object_names), "delete previous geometry")

        if self._hfss.modeler.object_names or self._hfss.setup_names or self._hfss.excitation_names:
            raise RuntimeError("AEDT still contains geometry, setups, or excitations after cleanup")

    def create_patch_antenna(self, specification: Mapping[str, Any]) -> HFSSResult:
        """Build one complete inset-fed patch from validated numeric parameters."""
        self._check_connected()
        try:
            spec = PatchAntennaSpec.from_mapping(specification)
        except ValueError as exc:
            return HFSSResult(success=False, message=f"参数校验失败: {exc}")

        _dbg(f">>> create_patch_antenna: {spec.to_dict()}")
        try:
            candidate_path = self._prepare_candidate_project()
            self._clear_current_design()
            self._hfss.modeler.model_units = "mm"

            variables = {
                "freq0": f"{spec.center_frequency_ghz:.12g}GHz",
                "sub_w": f"{spec.substrate_width_mm:.12g}mm",
                "sub_l": f"{spec.substrate_length_mm:.12g}mm",
                "sub_h": f"{spec.substrate_height_mm:.12g}mm",
                "patch_w": f"{spec.patch_width_mm:.12g}mm",
                "patch_l": f"{spec.patch_length_mm:.12g}mm",
                "feed_w": f"{spec.feed_width_mm:.12g}mm",
                "inset_d": f"{spec.inset_depth_mm:.12g}mm",
                "inset_gap": f"{spec.inset_gap_mm:.12g}mm",
            }
            for name, value in variables.items():
                self._hfss[name] = value

            backend_material_name = "AntennaSubstrate"
            material_keys = {
                str(name).casefold(): str(name) for name in self._hfss.materials.material_keys
            }
            material_key = material_keys.get(backend_material_name.casefold())
            if material_key:
                material = self._hfss.materials[material_key]
            else:
                material = self._require_created(
                    self._hfss.materials.add_material(backend_material_name),
                    f"material {backend_material_name}",
                )
            material.permittivity = spec.substrate_permittivity
            material.dielectric_loss_tangent = spec.substrate_loss_tangent

            sub_w = spec.substrate_width_mm
            sub_l = spec.substrate_length_mm
            sub_h = spec.substrate_height_mm
            patch_w = spec.patch_width_mm
            patch_l = spec.patch_length_mm
            feed_w = spec.feed_width_mm
            inset_y = -patch_l / 2 + spec.inset_depth_mm
            notch_x = feed_w / 2 + spec.inset_gap_mm

            substrate = self._require_created(
                self._hfss.modeler.create_box(
                    origin=[-sub_w / 2, -sub_l / 2, 0],
                    sizes=[sub_w, sub_l, sub_h],
                    name="Substrate",
                    material=backend_material_name,
                ),
                "Substrate",
            )
            ground = self._require_created(
                self._hfss.modeler.create_rectangle(
                    orientation="XY",
                    origin=[-sub_w / 2, -sub_l / 2, 0],
                    sizes=[sub_w, sub_l],
                    name="Ground",
                    material="copper",
                ),
                "Ground",
            )

            patch_points = [
                [-patch_w / 2, patch_l / 2, sub_h],
                [patch_w / 2, patch_l / 2, sub_h],
                [patch_w / 2, -patch_l / 2, sub_h],
                [notch_x, -patch_l / 2, sub_h],
                [notch_x, inset_y, sub_h],
                [feed_w / 2, inset_y, sub_h],
                [feed_w / 2, -sub_l / 2, sub_h],
                [-feed_w / 2, -sub_l / 2, sub_h],
                [-feed_w / 2, inset_y, sub_h],
                [-notch_x, inset_y, sub_h],
                [-notch_x, -patch_l / 2, sub_h],
                [-patch_w / 2, -patch_l / 2, sub_h],
            ]
            patch = self._require_created(
                self._hfss.modeler.create_polyline(
                    points=patch_points,
                    cover_surface=True,
                    close_surface=True,
                    name="PatchFeed",
                    material="copper",
                ),
                "PatchFeed",
            )
            self._require_created(
                self._hfss.assign_perfecte_to_sheets(
                    [ground.name, patch.name], name="PEC_Metals", is_infinite_ground=False
                ),
                "PEC_Metals",
            )

            port_sheet = self._require_created(
                self._hfss.modeler.create_polyline(
                    points=[
                        [-feed_w / 2, -sub_l / 2, 0],
                        [feed_w / 2, -sub_l / 2, 0],
                        [feed_w / 2, -sub_l / 2, sub_h],
                        [-feed_w / 2, -sub_l / 2, sub_h],
                    ],
                    cover_surface=True,
                    close_surface=True,
                    name="Port1_sheet",
                    material="vacuum",
                ),
                "Port1_sheet",
            )
            # 由 PyAEDT 从端口面的真实边缘推导端点，避免计算坐标触发 AEDT 的面上容差判定。
            self._require_created(
                self._hfss.lumped_port(
                    assignment=port_sheet.name,
                    integration_line=self._hfss.axis_directions.ZPos,
                    impedance=50,
                    name="Port1",
                    renormalize=True,
                ),
                "Port1",
            )

            region = self._require_created(
                self._hfss.modeler.create_region(
                    pad_value=[
                        spec.air_margin_xy_mm,
                        spec.air_margin_xy_mm,
                        spec.air_margin_xy_mm,
                        spec.air_margin_xy_mm,
                        spec.air_above_mm,
                        spec.air_below_mm,
                    ],
                    pad_type="Absolute Offset",
                    name="AirRegion",
                ),
                "AirRegion",
            )
            self._require_created(
                self._hfss.assign_radiation_boundary_to_objects(region.name, name="Rad1"),
                "Rad1",
            )
            self._require_created(
                self._hfss.insert_infinite_sphere(
                    name="InfiniteSphere1",
                    x_start=0,
                    x_stop=180,
                    x_step=5,
                    y_start=-180,
                    y_stop=180,
                    y_step=5,
                ),
                "InfiniteSphere1",
            )

            setup = self._require_created(
                self._hfss.create_setup(name=self._setup_name, setup_type="HFSSDriven"),
                self._setup_name,
            )
            setup.props["Frequency"] = f"{spec.center_frequency_ghz:.12g}GHz"
            setup.props["MaxDeltaS"] = 0.02
            setup.props["MaximumPasses"] = 12
            setup.props["MinimumPasses"] = 2
            setup.props["MinimumConvergedPasses"] = 1
            self._require_created(setup.update(), f"update {self._setup_name}")

            self._require_created(
                self._hfss.create_linear_count_sweep(
                    setup=self._setup_name,
                    units="GHz",
                    start_frequency=spec.sweep_start_ghz,
                    stop_frequency=spec.sweep_stop_ghz,
                    num_of_freq_points=spec.sweep_points,
                    name=self._sweep_name,
                    sweep_type="Interpolating",
                    save_fields=False,
                    save_rad_fields=True,
                ),
                self._sweep_name,
            )

            _dbg("<<< create_patch_antenna 建模成功")
            return HFSSResult(
                success=True,
                data={"specification": spec.to_dict(), "project_path": str(candidate_path.resolve())},
                message="贴片天线、Port1、Rad1、InfiniteSphere1、Setup1 和 Sweep1 已创建。",
            )
        except Exception as exc:
            _dbg(f"<<< create_patch_antenna 失败: {type(exc).__name__}: {exc}")
            aedt_errors = self._recent_aedt_errors()
            detail = f"；AEDT: {' | '.join(aedt_errors)}" if aedt_errors else ""
            return HFSSResult(
                success=False,
                data={
                    "project_path": str(self._project_path.resolve()) if self._project_path else None,
                    "aedt_errors": aedt_errors,
                },
                message=f"建模失败: {type(exc).__name__}: {exc}{detail}",
            )

    @staticmethod
    def _named(items: Any) -> List[str]:
        return [str(getattr(item, "name", item)) for item in (items or [])]

    def validate_design(self) -> HFSSResult:
        self._check_connected()
        _dbg(">>> validate_design")
        try:
            objects = self._named(self._hfss.modeler.object_names)
            excitations = self._named(self._hfss.excitation_names)
            boundaries = self._named(self._hfss.boundaries)
            field_setups = self._named(self._hfss.field_setups)
            setups = self._named(self._hfss.setup_names)
            sweeps = self._named(self._hfss.existing_analysis_sweeps)

            folded_objects = {name.casefold() for name in objects}
            folded_excitations = {name.split(":", 1)[0].casefold() for name in excitations}
            folded_boundaries = {name.casefold() for name in boundaries}
            folded_fields = {name.casefold() for name in field_setups}
            folded_setups = {name.casefold() for name in setups}
            folded_sweeps = {" ".join(name.split()).casefold() for name in sweeps}
            required_objects = {"substrate", "ground", "patchfeed", "port1_sheet", "airregion"}

            checks = {
                "geometry": required_objects.issubset(folded_objects),
                "port": "port1" in folded_excitations,
                "radiation_boundary": "rad1" in folded_boundaries,
                "far_field_setup": "infinitesphere1" in folded_fields,
                "solution_setup": self._setup_name.casefold() in folded_setups,
                "frequency_sweep": (
                    f"{self._setup_name} : {self._sweep_name}".casefold() in folded_sweeps
                ),
            }

            validation_messages, native_ok = self._hfss.validate_full_design(ports=1)
            checks["native_validation"] = bool(native_ok)
            data = {
                "checks": checks,
                "objects": objects,
                "excitations": excitations,
                "boundaries": boundaries,
                "field_setups": field_setups,
                "setups": setups,
                "analysis_sweeps": sweeps,
                "native_messages": list(validation_messages)[-20:],
            }
            failed = [name for name, passed in checks.items() if not passed]
            if failed:
                message = f"设计校验失败: {', '.join(failed)}"
                _dbg(f"<<< {message}")
                return HFSSResult(success=False, data=data, message=message)
            _dbg("<<< validate_design 通过")
            return HFSSResult(success=True, data=data, message="设计校验通过")
        except Exception as exc:
            _dbg(f"<<< validate_design 异常: {type(exc).__name__}: {exc}")
            return HFSSResult(success=False, message=f"设计校验异常: {type(exc).__name__}: {exc}")

    # ---------- 求解 ----------

    def solve(self, solution_name: Optional[str] = None) -> HFSSResult:
        self._check_connected()
        name = solution_name or self._setup_name
        if name != self._setup_name:
            return HFSSResult(success=False, message=f"只允许求解 {self._setup_name}")
        validation = self.validate_design()
        if not validation.success:
            return HFSSResult(success=False, data=validation.data, message=f"求解前校验未通过: {validation.message}")
        _dbg(f">>> solve: analyze_setup({name})（阻塞式，可能需要几分钟）...")
        t0 = datetime.now()
        try:
            ok = self._hfss.analyze_setup(name)   # 返回 False 即求解失败
            elapsed = (datetime.now() - t0).total_seconds()
            if not ok:
                aedt_errors = self._recent_aedt_errors(limit=5)
                detail = " | ".join(aedt_errors) if aedt_errors else "AEDT 未返回详细错误"
                _dbg(f"<<< solve 失败: {name}，耗时 {elapsed:.1f}s；{detail}")
                return HFSSResult(
                    success=False,
                    data={"aedt_errors": aedt_errors},
                    message=f"求解失败: {name}；AEDT: {detail}",
                )
            freq, _ = self._s11_curve()
            _dbg(f"<<< solve 完成: {name}，耗时 {elapsed:.1f}s")
            return HFSSResult(
                success=True,
                data={"solution": name, "s11_samples": len(freq)},
                message=f"求解完成且已验证 S11 数据: {name}",
            )
        except Exception as e:
            _dbg(f"<<< solve 失败: {type(e).__name__}: {e}")
            aedt_errors = self._recent_aedt_errors(limit=5)
            detail = f"；AEDT: {' | '.join(aedt_errors)}" if aedt_errors else ""
            return HFSSResult(
                success=False,
                data={"aedt_errors": aedt_errors},
                message=f"求解失败: {type(e).__name__}: {e}{detail}",
            )

    # ---------- 结果读取 ----------

    @staticmethod
    def _frequency_values_hz(values: Any, unit: Optional[str]) -> List[float]:
        """Convert SolutionData sweep values to hertz without discarding their unit."""
        raw_values = list(values)
        if not raw_values:
            return []
        if not unit:
            unit = getattr(raw_values[0], "unit", None) or "Hz"
        scales = {
            "hz": 1.0,
            "khz": 1e3,
            "mhz": 1e6,
            "ghz": 1e9,
            "thz": 1e12,
        }
        scale = scales.get(str(unit).casefold())
        if scale is None:
            raise ValueError(f"不支持的频率单位: {unit}")
        return [float(value) * scale for value in raw_values]

    def _s11_curve(self, setup_sweep_name: Optional[str] = None):
        """返回 (freq_hz_array, s11_db_array)，供 get_result / get_metrics 复用。"""
        setup_sweep = setup_sweep_name or f"{self._setup_name} : {self._sweep_name}"
        _dbg(f">>> 读取 S(1,1): setup_sweep={setup_sweep!r}")
        data = self._hfss.post.get_solution_data(
            expressions="dB(S(1,1))",
            setup_sweep_name=setup_sweep,
        )
        if not data or not hasattr(data, "data_real"):
            raise RuntimeError(
                f"未取到 S(1,1) 数据（{setup_sweep}），请确认已完成求解且 Setup/Sweep 名称正确"
            )
        s11_vals = data.data_real()
        if s11_vals is None or len(s11_vals) == 0:
            raise RuntimeError(
                f"S(1,1) 数据为空（{setup_sweep}），请确认已完成求解且扫参设置正确"
            )
        import numpy as np
        # SolutionData keeps sweep values in units_sweeps (normally GHz). Convert
        # each value explicitly instead of assuming that float(value) is in hertz.
        freq_vals = getattr(data, "primary_sweep_values", None)
        if freq_vals is None or len(freq_vals) == 0:
            freq_vals = data.intrinsics["Freq"]
        freq_unit = (getattr(data, "units_sweeps", None) or {}).get("Freq")
        freq = np.asarray(self._frequency_values_hz(freq_vals, freq_unit), dtype=float)
        s11 = np.asarray(s11_vals, dtype=float)
        if len(freq) != len(s11):
            raise RuntimeError(f"S11 频率点数量不一致: freq={len(freq)}, s11={len(s11)}")
        if not np.all(np.isfinite(freq)) or not np.all(np.isfinite(s11)):
            raise RuntimeError("S11 结果包含 NaN 或无穷值")
        if np.any(freq <= 0):
            raise RuntimeError("S11 结果包含非正频率")
        order = np.argsort(freq)
        freq = freq[order]
        s11 = s11[order]
        _dbg(f"<<< S(1,1) 共 {len(freq)} 个采样点，频率范围 "
             f"{freq.min() / 1e9:.3f} ~ {freq.max() / 1e9:.3f} GHz，"
             f"S11 min = {s11.min():.2f} dB")
        return freq, s11

    def get_result(self, metric: str) -> HFSSResult:
        self._check_connected()
        try:
            if metric == "far_field":
                _dbg(">>> get_result: far_field")
                data = self._antenna_metrics()
                required = {"peak_gain_dbi", "radiation_efficiency_percent"}
                if not required.issubset(data):
                    _dbg("<<< get_result 失败: 未读取到增益/效率数据")
                    return HFSSResult(
                        success=False,
                        data=data,
                        message="增益或辐射效率缺失，请确认远场设置与求解结果完整",
                    )
                _dbg(f"<<< get_result 增益/效率: {data}")
                return HFSSResult(success=True, data=data, message="增益/效率读取成功")
            if metric == "s11":
                _dbg(">>> get_result: s11")
                freq, s11 = self._s11_curve()
                return HFSSResult(
                    success=True,
                    data={"freq_ghz": (freq / 1e9).tolist(), "s11_db": s11.tolist()},
                    message="S11 曲线读取成功",
                )
            if metric == "all":
                return self.get_metrics()
            return HFSSResult(success=False, message=f"不支持的指标类型: {metric}")
        except Exception as e:
            _dbg(f"<<< get_result 失败: {type(e).__name__}: {e}")
            return HFSSResult(success=False, message=f"读取结果失败: {e}")

    def export_design(self, file_path: Optional[Union[str, Path]] = None) -> HFSSResult:
        self._check_connected()
        try:
            if self._project_path is None:
                return HFSSResult(success=False, message="当前项目路径不存在")
            path = self._project_path.resolve()
            if file_path is not None and Path(file_path).expanduser().resolve() != path:
                return HFSSResult(success=False, message="不允许把模型导出到后端管理路径之外")
            _dbg(f">>> export_design: 保存当前项目 {path}")
            saved = self._hfss.save_project()
            if saved is False:
                return HFSSResult(success=False, message="PyAEDT save_project 返回 False")
            if not path.is_file():
                return HFSSResult(success=False, message=f"保存后未找到工程文件: {path}")
            _dbg("<<< export_design 成功")
            return HFSSResult(
                success=True,
                data={"file_path": str(path)},
                message=f"设计已保存: {path}",
            )
        except Exception as e:
            _dbg(f"<<< export_design 失败: {type(e).__name__}: {e}")
            return HFSSResult(success=False, message=f"保存失败: {e}")

    # ---------- 远场指标（增益 / 辐射效率） ----------

    def _far_field_sphere_name(self) -> Optional[str]:
        """Return the required sphere without mutating a solved design."""
        setups = list(getattr(self._hfss, "field_setups", None) or [])
        for setup in setups:
            name = str(getattr(setup, "name", setup))
            if name.casefold() == "infinitesphere1":
                return name
        return None

    @staticmethod
    def _finite_real_values(data: Any, expression: str) -> List[float]:
        """Extract finite real samples for one exact AEDT report expression."""
        if not data or not hasattr(data, "data_real"):
            return []
        values = data.data_real(expression)
        if values is None:
            return []
        import math

        return [float(value) for value in values if value is not None and math.isfinite(float(value))]

    def _antenna_parameter_values(self, expression: str, sphere: str) -> List[float]:
        """Read an Antenna Parameters expression with its required sphere context."""
        report = self._hfss.post.reports_by_category.antenna_parameters(
            expressions=expression,
            setup=f"{self._setup_name} : LastAdaptive",
            infinite_sphere=sphere,
        )
        if not report:
            return []
        return self._finite_real_values(report.get_solution_data(), expression)

    def _antenna_metrics(self) -> Dict[str, float]:
        """读取峰值增益 [dBi] 与辐射效率 [%]。

        Use the PyAEDT 0.19 Antenna Parameters report so that the infinite-sphere
        context is passed correctly. Missing data remains missing; no value is invented.
        """
        out: Dict[str, float] = {}
        sphere = self._far_field_sphere_name()
        if not sphere:
            _dbg("缺少 InfiniteSphere1，增益/效率读取中止")
            return out

        try:
            values = self._antenna_parameter_values("dB(PeakGain)", sphere)
            if values:
                out["peak_gain_dbi"] = max(values)
            _dbg(f"Antenna Parameters/PeakGain: peak_gain={out.get('peak_gain_dbi')}")
        except Exception as e:
            _dbg(f"Antenna Parameters/PeakGain 失败: {type(e).__name__}: {e}")

        try:
            values = self._antenna_parameter_values("RadiationEfficiency", sphere)
            if values:
                eff = values[0]
                eff_percent = eff * 100.0 if eff <= 1.0 else eff
                if 0.0 <= eff_percent <= 100.0:
                    out["radiation_efficiency_percent"] = eff_percent
            _dbg(f"Antenna Parameters/RadiationEfficiency: "
                 f"eff={out.get('radiation_efficiency_percent')}")
        except Exception as e:
            _dbg(f"Antenna Parameters/RadiationEfficiency 失败: {type(e).__name__}: {e}")

        return out

    # ---------- 评测指标 ----------

    @staticmethod
    def _bandwidth_hz(freq, s11, threshold_db: float = -10.0) -> float:
        """以谐振点为中心计算带宽，端点处对穿越频率做线性插值。

        S11 全程高于阈值时返回 0；扫频边缘仍低于阈值时以边缘频率为界。
        """
        import numpy as np

        i_min = int(np.argmin(s11))
        if s11[i_min] > threshold_db:
            return 0.0

        # 左侧穿越点：从谐振点向左找第一个高于阈值的采样点，再线性插值
        lo = float(freq[0])
        for i in range(i_min, 0, -1):
            if s11[i - 1] > threshold_db:
                t = (threshold_db - s11[i - 1]) / (s11[i] - s11[i - 1])
                lo = float(freq[i - 1] + t * (freq[i] - freq[i - 1]))
                break

        # 右侧穿越点
        hi = float(freq[-1])
        for i in range(i_min, len(s11) - 1):
            if s11[i + 1] > threshold_db:
                t = (threshold_db - s11[i]) / (s11[i + 1] - s11[i])
                hi = float(freq[i] + t * (freq[i + 1] - freq[i]))
                break

        return max(0.0, hi - lo)

    def get_metrics(self) -> HFSSResult:
        self._check_connected()
        _dbg(">>> get_metrics: 计算 S11 指标 + 读取增益/效率")
        try:
            import numpy as np
            freq, s11 = self._s11_curve()
            i_min = int(np.argmin(s11))
            metrics = {
                "center_freq_ghz": float(freq[i_min] / 1e9),
                "s11_min_db": float(s11[i_min]),
                "bandwidth_mhz": self._bandwidth_hz(freq, s11) / 1e6,
            }
            # 增益 / 辐射效率（读取失败时键缺失，评测按缺失判不通过）
            metrics.update(self._antenna_metrics())
            required = {
                "center_freq_ghz",
                "s11_min_db",
                "bandwidth_mhz",
                "peak_gain_dbi",
                "radiation_efficiency_percent",
            }
            missing = sorted(required - set(metrics))
            if missing:
                return HFSSResult(
                    success=False,
                    data=metrics,
                    message=f"指标不完整，缺少: {', '.join(missing)}",
                )
            _dbg(f"<<< get_metrics: {metrics}")
            return HFSSResult(success=True, data=metrics, message="指标计算完成")
        except Exception as e:
            _dbg(f"<<< get_metrics 失败: {type(e).__name__}: {e}")
            return HFSSResult(success=False, message=f"指标计算失败: {e}")
