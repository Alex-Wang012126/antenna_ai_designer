import os
from pathlib import Path

# 1) 当前进程实际看到的 ANSYS 相关环境变量
print("=== 进程内环境变量 ===")
found = {k: v for k, v in os.environ.items() if "ANSYS" in k.upper() or "AWP" in k.upper()}
print(found if found else "（一个都没有！说明 setx 没生效或终端没重开）")

# 2) 候选路径是否存在
print("\n=== 路径检查 ===")
for c in [
    r"C:\ANSYS Inc\ANSYS Student\v252\AnsysEM",
    r"C:\ANSYS Inc\v252\AnsysEM",
]:
    p = Path(c)
    print(f"{c}\n  目录存在: {p.exists()},  ansysedtsv.exe 存在: {(p / 'ansysedtsv.exe').exists()}")

# 3) 设置后验证 pyaedt 能否检测到
print("\n=== pyaedt 检测结果 ===")
from ansys.aedt.core.internal.aedt_versions import AedtVersions
print(AedtVersions().installed_versions)