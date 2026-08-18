from ansys.aedt.core import Hfss, settings
settings.grpc_secure_mode = False

hfss = Hfss(
    version="2025.2",        # 学生版 2025 R2 对应的版本号
    student_version=True,    # 关键：声明学生版
    non_graphical=False,     # 调试期先开界面，能直观看到模型操作
    new_desktop=True,
    close_on_exit=False,
)
print("连接成功，AEDT 版本:", hfss.aedt_version_id)
hfss.release_desktop()