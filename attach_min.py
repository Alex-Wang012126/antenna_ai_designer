from ansys.aedt.core import Desktop
from ansys.aedt.core import settings

settings.grpc_secure_mode=False

d = Desktop(
    version="2025.2",
    student_version=True,
    new_desktop=True
)

print(d.aedt_version_id)