import os
# 学生版 2025 R2 必须：强制使用 legacy "-grpcsrv <port>" 启动方式（官方 workaround）
os.environ.setdefault("PYAEDT_USE_PRE_GRPC_ARGS", "True")
# 正确的学生版安装路径变量名（原来写的是 ANSYSEM_ROOTSV252，顺序反了）
os.environ.setdefault(
    "ANSYSEMSV_ROOT252",
    os.getenv("AEDT_ROOT", r"C:\ANSYS Inc\ANSYS Student\v252\AnsysEM"),
)

from ansys.aedt.core import Hfss, settings
from config import cfg
settings.grpc_secure_mode = False
settings.enable_debug_logger = True

hfss = Hfss(
    version=cfg.aedt_version,
    student_version=cfg.aedt_student,
    non_graphical=cfg.aedt_non_graphical,
    new_desktop=True,
    close_on_exit=False,
)
try:
    print("连接成功，AEDT 版本:", hfss.aedt_version_id)
finally:
    hfss.release_desktop(
        close_projects=not cfg.aedt_keep_open,
        close_desktop=not cfg.aedt_keep_open,
    )
    if cfg.aedt_keep_open:
        print("验证结束，AEDT 项目和窗口保持打开。")
