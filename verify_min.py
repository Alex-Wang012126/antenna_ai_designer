import os
# 学生版 2025 R2 必须：强制使用 legacy "-grpcsrv <port>" 启动方式（官方 workaround）
os.environ["PYAEDT_USE_PRE_GRPC_ARGS"] = "True"
# 正确的学生版安装路径变量名（原来写的是 ANSYSEM_ROOTSV252，顺序反了）
os.environ["ANSYSEMSV_ROOT252"] = r"C:\ANSYS Inc\ANSYS Student\v252\AnsysEM"

from ansys.aedt.core import Hfss, settings
settings.grpc_secure_mode = False
settings.enable_debug_logger = True

hfss = Hfss(
    version="2025.2",
    student_version=True,
    non_graphical=False,
    new_desktop=True,
    close_on_exit=True,
)
print("连接成功，AEDT 版本:", hfss.aedt_version_id)
hfss.release_desktop()