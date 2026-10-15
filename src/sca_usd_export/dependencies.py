"""Keep optional USD imports out of SCA and give an actionable error."""


def require_usd():
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt
    except ImportError as exc:
        raise ValueError(
            "OpenUSD Python API is missing or cannot load. Install the extension dependencies: "
            "run the installed add-on's scripts/use.ps1 with -Arguments @('provision'). "
            "Dependency requirements are declared in pyproject.toml. Original error: " + str(exc)
        ) from exc
    return Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt
