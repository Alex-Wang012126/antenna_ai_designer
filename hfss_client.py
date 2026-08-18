"""HFSS / AEDT 调用接口占位

这里封装所有与 HFSS/AEDT 交互的操作。当前为占位实现，仅打印调用并返回示例数据。
后续请替换为真实 API（例如 PyAEDT、win32com、IronPython 脚本等）。

Ansys Electronics Desktop Student 2025 R2 常见接入方式：
1. PyAEDT（推荐）：pip install pyaedt
   from ansys.aedt.core import Hfss
   hfss = Hfss(version="2025.2", non_graphical=False, student_version=True)
2. Windows COM：使用 win32com 启动 Ansoft.ElectronicsDesktop.xxxx。
3. 脚本文件：生成 .py/vbs/js 脚本，通过 AEDT 的 Run Script 命令执行。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .config import Config, cfg


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
    def update_geometry(self, script: str) -> HFSSResult:
        """执行一段修改几何/材料/边界/激励的脚本。"""
        raise NotImplementedError

    @abstractmethod
    def solve(self, solution_name: Optional[str] = None) -> HFSSResult:
        """运行仿真求解。"""
        raise NotImplementedError

    @abstractmethod
    def get_result(self, report_name: str, solution_name: str) -> HFSSResult:
        """读取报告数据。"""
        raise NotImplementedError

    @abstractmethod
    def export_design(self, file_path: Union[str, Path]) -> HFSSResult:
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

    def connect(self, config: Optional[Config] = None) -> HFSSResult:
        if config is None:
            config = cfg
        self._project_dir = config.project_dir
        self._project_dir.mkdir(parents=True, exist_ok=True)
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

    def update_geometry(self, script: str) -> HFSSResult:
        self._check_connected()
        print(f"[HFSS 占位] update_geometry:\n{script[:200]}...")
        # 模拟一个常见的脚本错误示例
        if "invalid_script" in script.lower():
            return HFSSResult(success=False, message="SyntaxError: invalid_script not found")
        return HFSSResult(success=True, message="Geometry updated (placeholder)")

    def solve(self, solution_name: Optional[str] = None) -> HFSSResult:
        self._check_connected()
        print(f"[HFSS 占位] solve(solution_name={solution_name})")
        return HFSSResult(
            success=True,
            data={"solution": solution_name or cfg.default_setup_sweep_name, "status": "converged"},
            message="Simulation completed (placeholder)",
        )

    def get_result(self, report_name: str, solution_name: str) -> HFSSResult:
        self._check_connected()
        print(f"[HFSS 占位] get_result(report={report_name}, solution={solution_name})")
        # 返回模拟数据：频率 [GHz] 与 S11 [dB]
        data = {
            "freq_ghz": [2.30, 2.40, 2.45, 2.50, 2.60],
            "s11_db": [-5.0, -9.5, -21.0, -8.0, -4.0],
        }
        return HFSSResult(success=True, data=data, message="Report data retrieved (placeholder)")

    def export_design(self, file_path: Union[str, Path]) -> HFSSResult:
        self._check_connected()
        path = Path(file_path)
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
    """基于 PyAEDT 的真实 HFSS 客户端（AEDT Student 2025 R2）。"""

    def __init__(self):
        self._hfss = None
        self._project_path: Optional[Path] = None
        self._setup_name: str = "Setup1"
        self._sweep_name: str = "Sweep1"

    # ---------- 连接管理 ----------

    def connect(self, config: Optional[Config] = None) -> HFSSResult:
        config = config or cfg
        try:
            from ansys.aedt.core import Hfss
        except ImportError:
            return HFSSResult(success=False, message="未安装 pyaedt：pip install pyaedt")

        project_dir = Path(config.project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        self._project_path = project_dir / "eval_design.aedt"
        self._setup_name = config.default_setup_name
        self._sweep_name = config.default_sweep_name

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
            return HFSSResult(success=False, message=f"AEDT 启动失败: {type(e).__name__}: {e}")

        return HFSSResult(success=True,
                          message=f"已连接 AEDT {self._hfss.aedt_version_id}, 项目: {self._project_path}")

    def disconnect(self) -> HFSSResult:
        if self._hfss is not None:
            try:
                self._hfss.save_project()
                self._hfss.release_desktop(close_projects=True, close_desktop=True)
            finally:
                self._hfss = None
        return HFSSResult(success=True, message="已断开并保存项目")

    def _check_connected(self) -> None:
        if self._hfss is None:
            raise RuntimeError("HFSS client is not connected. Call connect() first.")

    # ---------- 变量读写 ----------

    def set_design_variable(self, name: str, value) -> HFSSResult:
        self._check_connected()
        try:
            # 字符串原样传入（须带单位，如 "30mm"）；数字按 SI 单位处理
            self._hfss[name] = value if isinstance(value, str) else float(value)
            return HFSSResult(success=True, data={name: value}, message=f"Set {name}={value}")
        except Exception as e:
            return HFSSResult(success=False, message=f"set_variable 失败: {e}")

    def get_design_variable(self, name: str) -> HFSSResult:
        self._check_connected()
        try:
            expr = self._hfss.variable_manager[name].expression
            return HFSSResult(success=True, data=expr, message=f"{name}={expr}")
        except KeyError:
            return HFSSResult(success=False, message=f"变量 {name} 不存在")

    # ---------- 建模（Agent 多轮迭代的核心入口） ----------

    def update_geometry(self, script: str) -> HFSSResult:
        """执行模型生成的 pyaedt 脚本。脚本中可直接使用变量 `hfss`。"""
        self._check_connected()
        try:
            exec(script, {"hfss": self._hfss})
            return HFSSResult(success=True, message="脚本执行成功")
        except Exception as e:
            # 把完整异常反馈给模型，这是迭代修正的关键信息
            import traceback
            return HFSSResult(success=False,
                              message=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")

    # ---------- 求解 ----------

    def solve(self, solution_name: Optional[str] = None) -> HFSSResult:
        self._check_connected()
        name = solution_name or self._setup_name
        try:
            self._hfss.analyze_setup(name)   # 阻塞式，直到求解完成
            return HFSSResult(success=True, data={"solution": name},
                              message=f"求解完成: {name}")
        except Exception as e:
            return HFSSResult(success=False, message=f"求解失败: {type(e).__name__}: {e}")

    # ---------- 结果读取 ----------

    def _s11_curve(self, setup_sweep_name: Optional[str] = None):
        """返回 (freq_hz_array, s11_db_array)，供 get_result / get_metrics 复用。"""
        setup_sweep = setup_sweep_name or f"{self._setup_name} : {self._sweep_name}"
        data = self._hfss.post.get_solution_data(
            expressions="dB(S(1,1))",
            setup_sweep_name=setup_sweep,
        )
        if data is None:
            raise RuntimeError(
                f"未取到 S(1,1) 数据（{setup_sweep}），请确认已完成求解且 Setup/Sweep 名称正确"
            )
        s11_vals = data.data_real()
        if not s11_vals:
            raise RuntimeError(
                f"S(1,1) 数据为空（{setup_sweep}），请确认已完成求解且扫参设置正确"
            )
        import numpy as np
        # 优先使用 primary_sweep_values；旧版本 pyaedt 回退到 intrinsics["Freq"]
        freq_vals = getattr(data, "primary_sweep_values", None)
        if freq_vals is None or len(freq_vals) == 0:
            freq_vals = data.intrinsics["Freq"]
        freq = np.asarray(freq_vals, dtype=float)
        s11 = np.asarray(s11_vals, dtype=float)
        return freq, s11

    def get_result(self, report_name: str, solution_name: str) -> HFSSResult:
        self._check_connected()
        try:
            freq, s11 = self._s11_curve(solution_name or None)
            return HFSSResult(success=True,
                              data={"freq_ghz": (freq / 1e9).tolist(), "s11_db": s11.tolist()},
                              message="S11 曲线读取成功")
        except Exception as e:
            return HFSSResult(success=False, message=f"读取结果失败: {e}")

    def export_design(self, file_path) -> HFSSResult:
        self._check_connected()
        try:
            self._hfss.save_project()
            # .aedt 是“文件+同名文件夹”结构；评测时直接以项目路径为准
            return HFSSResult(success=True,
                              data={"file_path": str(self._project_path)},
                              message=f"设计已保存: {self._project_path}")
        except Exception as e:
            return HFSSResult(success=False, message=f"保存失败: {e}")

    # ---------- 评测指标 ----------

    def get_metrics(self) -> HFSSResult:
        self._check_connected()
        try:
            import numpy as np
            freq, s11 = self._s11_curve()
            i_min = int(np.argmin(s11))
            # -10 dB 带宽：以谐振点为中心向两侧扩展
            below = np.where(s11 <= -10.0)[0]
            if len(below) > 0:
                lo = below[below <= i_min].min()
                hi = below[below >= i_min].max()
                bw_hz = float(freq[hi] - freq[lo])
            else:
                bw_hz = 0.0
            metrics = {
                "center_freq_ghz": float(freq[i_min] / 1e9),
                "s11_min_db": float(s11[i_min]),
                "bandwidth_mhz": bw_hz / 1e6,
            }
            return HFSSResult(success=True, data=metrics, message="指标计算完成")
        except Exception as e:
            return HFSSResult(success=False, message=f"指标计算失败: {e}")