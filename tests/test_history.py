"""History preservation and integrity checks using genuine SCA definitions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import simplecadapi as scad

from sca_usd_export.history import (
    read_feature_graph,
    read_history_bundle,
    write_history_bundle,
)


def client_native_path(path):
    """Independent test-side Win32 I/O, not the writer's private helper.

    Keep all byte assertions and deliberate mutations even when pytest's
    result directory plus a content digest exceeds legacy MAX_PATH.
    """
    path = Path(path).absolute()
    if os.name != 'nt':
        return path
    text = str(path)
    if text.startswith('\\\\?\\'):
        return path
    if text.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + text[2:])
    return Path('\\\\?\\' + text)


@pytest.fixture
def part_definition():
    @scad.part(id="history-box")
    def build():
        return scad.make_box_rsolid(width=2.0, height=3.0, depth=4.0)

    return build().definition


def test_preserves_exact_graph_and_source_bytes(tmp_path, part_definition):
    manifest_path = write_history_bundle(
        part_definition,
        tmp_path / "box.history",
        package_content_hash="sha256:original",
    )
    manifest = read_history_bundle(manifest_path)
    record = manifest["definitions"][0]
    original = part_definition.blobs[part_definition.feature_graph_ref.path]
    assert (
        client_native_path(manifest_path.parent / record["feature_graph"]["path"])
    ).read_bytes() == original
    assert (
        client_native_path(manifest_path.parent / record["definition"]["path"])
    ).read_bytes() == part_definition.canonical_bytes
    artifact = read_feature_graph(manifest_path, "history-box")
    assert artifact.source_files
    source = next(
        item
        for item in artifact.source_files
        if item.display_path.endswith("test_history.py")
    )
    assert artifact.blobs[source.uri] == Path(__file__).read_bytes()
    assert record["operation_count"] == len(artifact.graph["nodes"])
    assert record[
        "operations_missing_source"
    ]  # The implicit Part wrapper has no source.


def test_preserves_instances_transforms_and_definition_reuse(tmp_path, part_definition):
    from simplecadapi.artifacts.assembly_io import materialize_definition

    part = materialize_definition(part_definition)

    @scad.assemble(id="history-assembly", definitions=(part_definition,))
    def build():
        assembly = scad.make_assembly_rassembly(assembly_id="history-assembly")
        for component_id, offset in (("first", 1.0), ("second", 10.0)):
            assembly = scad.add_component_rassembly(
                assembly=assembly,
                item=part,
                component_id=component_id,
                placement=scad.make_placement_rplacement(origin=(offset, 2.0, 3.0)),
            )
        return assembly

    definition = build().definition
    manifest_path = write_history_bundle(definition, tmp_path / "assembly.history")
    manifest = read_history_bundle(manifest_path)
    assert len(manifest["definitions"]) == 2
    record = next(
        item
        for item in manifest["definitions"]
        if item["definition_id"] == "history-assembly"
    )
    raw = json.loads(client_native_path(manifest_path.parent / record["definition"]["path"]).read_bytes())
    assert raw == definition.to_dict()
    assert [item["instance_id"] for item in raw["instances"]] == ["first", "second"]
    assert {item["definition_id"] for item in raw["instances"]} == {"history-box"}
    assert raw["instances"][0]["placement"] != raw["instances"][1]["placement"]


def test_detects_corrupt_resource(tmp_path, part_definition):
    manifest_path = write_history_bundle(part_definition, tmp_path / "box.history")
    manifest = read_history_bundle(manifest_path)
    resource = client_native_path(
        manifest_path.parent / manifest["definitions"][0]["feature_graph"]["path"]
    )
    resource.write_bytes(resource.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="integrity check failed"):
        read_history_bundle(manifest_path)


def test_refuses_existing_output(tmp_path, part_definition):
    output = tmp_path / "existing.history"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        write_history_bundle(part_definition, output)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_unknown_definition_is_clear(tmp_path, part_definition):
    manifest_path = write_history_bundle(part_definition, tmp_path / "box.history")
    with pytest.raises(KeyError, match="absent"):
        read_feature_graph(manifest_path, "missing")


def test_long_windows_history_paths_remain_portable(tmp_path, part_definition):
    destination = tmp_path
    while len(str(destination)) < 280:
        destination = destination / ("material-" * 6)
    destination = destination / "long.history"
    assert len(str(destination)) > 260
    path = write_history_bundle(part_definition, destination)
    manifest = read_history_bundle(path)
    assert manifest["definitions"][0]["definition"]["path"].startswith("definitions/")
    assert "\\" not in manifest["definitions"][0]["definition"]["path"]
    assert read_feature_graph(path, "history-box").owner_definition_id == "history-box"
    with pytest.raises(FileExistsError):
        write_history_bundle(part_definition, destination)
