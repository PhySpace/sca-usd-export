"""The geometry contract stays independent; saved physics fails closed."""
import builtins
import sys
from types import SimpleNamespace

import pytest

from sca_usd_export.dependencies import require_physics
from sca_usd_export.embedded_physics import package_settings
from sca_usd_export import ExportConfig


def test_legacy_metadata_free_settings_never_import_physics(monkeypatch):
    original = builtins.__import__

    def without_physics(name, *args, **kwargs):
        if name == "sca_physics" or name.startswith("sca_physics."):
            raise ImportError("physics intentionally absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_physics)
    package = SimpleNamespace(root_definition=SimpleNamespace(metadata={}),
                              occurrence_graph=SimpleNamespace(joints=[]))
    settings, report = package_settings(package, [], ExportConfig())
    assert settings["schema_version"] == "1.0"
    assert report["origin"] == "legacy_exporter_defaults"
    package.root_definition.metadata["sca_physics"] = {"schema_version": "1.8"}
    with pytest.raises(ValueError, match="requires optional sca-physics==0.1.8rc1"):
        package_settings(package, [], ExportConfig())


def test_wrong_physics_implementation_is_rejected(monkeypatch):
    monkeypatch.setitem(sys.modules, "sca_physics", SimpleNamespace(__version__="0.1.7rc2"))
    with pytest.raises(ValueError, match="Physics implementation mismatch"):
        require_physics()
