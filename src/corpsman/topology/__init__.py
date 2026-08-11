"""Active system state resolution, dispatched by platform."""
from .. import platform_


def system_devices(root="/"):
    # type: (str) -> dict
    plat = platform_.detect()
    if plat == "linux":
        from . import linux
        return linux.system_devices(root=root)
    raise RuntimeError(
        "no topology backend for platform '%s'; refusing to guess" % plat
    )
