"""Device identity, dispatched by platform."""
from .. import platform_


def enumerate(root="/"):
    # type: (str) -> list
    plat = platform_.detect()
    if plat == "linux":
        from . import linux
        return linux.enumerate_devices(root=root)
    raise RuntimeError(
        "no device backend for platform '%s'; refusing to guess device "
        "conventions" % plat
    )
