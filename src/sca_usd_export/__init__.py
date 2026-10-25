"""Independent SimpleCADAPI to OpenUSD extension; no SCA source changes."""

from .config import ExportConfig


def export_package(data, output_path, *, config=None, explicit_config_fields=None,
                   expected_input_sha256=None, require_physics=False):
    """Export a validated .scadpkg or in-memory ProductPackage."""
    from .exporter import export_package as export

    return export(data, output_path, config=config,
                  explicit_config_fields=explicit_config_fields,
                  expected_input_sha256=expected_input_sha256, require_physics=require_physics)


def update_drive_targets(stage, joint_id, *, target_position=None, target_velocity=None):
    """Update an already exported active force drive using SI targets."""
    from .joint_drives import update_drive_targets as update
    return update(stage, joint_id, target_position=target_position, target_velocity=target_velocity)


__all__ = ["ExportConfig", "export_package", "update_drive_targets"]

__version__ = "0.1.8rc2"
