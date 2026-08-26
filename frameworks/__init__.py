"""Topology framework registry for independent antenna task contracts."""

from importlib import import_module
from pkgutil import iter_modules
from typing import Any, Mapping, Protocol


class TopologyFramework(Protocol):
    """Interface implemented by each independently stored topology framework."""

    topology_id: str
    tool_name: str
    tool_description: str
    prompt_guidance: str

    def validate_task(self, data: Mapping[str, Any]) -> None:
        ...

    def validate_objectives(
        self, objectives: Mapping[str, Any], simulation: Mapping[str, Any]
    ) -> None:
        ...

    def validate_design(self, values: Mapping[str, Any], data: Mapping[str, Any]) -> Any:
        ...

    def resolve_design_spec(self, task: Any, values: Mapping[str, Any]) -> Any:
        ...

    def create_candidate(self, hfss_client: Any, values: Mapping[str, Any]) -> Any:
        ...


_FRAMEWORK_MODULES = {}


def _discover_frameworks():
    if _FRAMEWORK_MODULES:
        return _FRAMEWORK_MODULES
    for module_info in iter_modules(__path__):
        module = import_module(f"{__name__}.{module_info.name}")
        framework = getattr(module, "framework", None)
        topology_id = getattr(framework, "topology_id", None)
        if topology_id is None:
            continue
        if topology_id in _FRAMEWORK_MODULES:
            raise ValueError(f"duplicate topology framework: {topology_id}")
        _FRAMEWORK_MODULES[topology_id] = framework
    return _FRAMEWORK_MODULES


def get_framework(topology_id: str) -> TopologyFramework:
    frameworks = _discover_frameworks()
    framework = frameworks.get(topology_id)
    if framework is None:
        supported = ", ".join(sorted(_FRAMEWORK_MODULES))
        raise ValueError(
            f"unsupported topology.id='{topology_id}'; supported frameworks: {supported}"
        )
    return framework


__all__ = ["TopologyFramework", "get_framework"]
