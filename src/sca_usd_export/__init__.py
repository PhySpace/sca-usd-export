"""Independent SimpleCADAPI to OpenUSD extension; no SCA source changes."""

from .config import ExportConfig


def export_package(data, output_path, *, config=None):
    """Export a validated .scadpkg or in-memory ProductPackage."""
    from .exporter import export_package as export

    return export(data, output_path, config=config or ExportConfig())


__all__ = ["ExportConfig", "export_package"]
