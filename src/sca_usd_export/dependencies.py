"""Keep optional imports lazy and give actionable, fail-closed errors."""

PHYSICS_VERSION = "0.1.8rc1"


def require_physics():
    """Saved physics needs the tested contract implementation, never fallback."""
    try:
        import sca_physics
    except ImportError as exc:
        raise ValueError(
            "metadata.sca_physics requires optional sca-physics==" + PHYSICS_VERSION +
            ". Set SCA_PHYSICS_SOURCE to that repository or wheel and run provision; "
            "plain geometry export does not require sca-physics. Original error: " + str(exc)
        ) from exc
    if getattr(sca_physics, "__version__", None) != PHYSICS_VERSION:
        raise ValueError(
            "Physics implementation mismatch: this USD candidate requires sca-physics==" +
            PHYSICS_VERSION + "; found " + str(getattr(sca_physics, "__version__", None)) +
            ". Select the matching artifact and run provision."
        )
    return sca_physics


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
