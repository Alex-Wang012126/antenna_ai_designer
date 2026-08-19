from ansys.aedt.core import Hfss, settings
settings.grpc_secure_mode = False
hfss = Hfss(
    version="2025.2", student_version=True,
    machine="127.0.0.1", port=50051,
    new_desktop=False, close_on_exit=False,
)
print("attach 成功，AEDT 版本:", hfss.aedt_version_id)
hfss.release_desktop(close_projects=False, close_on_exit=False)