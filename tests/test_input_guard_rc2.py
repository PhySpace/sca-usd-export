"""Prevent exporting the raw package or a same-named, different revision."""
import hashlib
import json

import pytest
import simplecadapi as scad

from sca_physics.package_io import enrich_package
from sca_usd_export.exporter import export_package


@pytest.fixture
def raw():
    @scad.part(id='rc2-input-guard-box')
    def box():
        return scad.make_box_rsolid(width=10, height=10, depth=10)
    return scad.build_product_package(box())


def test_wrong_digest_rejects_before_outputs(raw, tmp_path):
    path=tmp_path/'final.scadpkg'
    path.write_bytes(scad.encode_product_package(raw))
    with pytest.raises(ValueError,match='SHA256 differs'):
        export_package(path,tmp_path/'wrong.usda',expected_input_sha256='0'*64)
    assert not (tmp_path/'wrong.usda').exists()
    assert not (tmp_path/'wrong.history').exists()


def test_raw_package_cannot_be_mistaken_for_final(raw, tmp_path):
    path=tmp_path/'raw.scadpkg'
    path.write_bytes(scad.encode_product_package(raw))
    with pytest.raises(ValueError,match='no metadata.sca_physics'):
        export_package(path,tmp_path/'raw.usda',require_physics=True)
    assert not (tmp_path/'raw.usda').exists()


def test_selected_final_identity_and_sources_are_reported(raw,tmp_path):
    final,_=enrich_package(raw)
    path=tmp_path/'final.scadpkg'
    data=scad.encode_product_package(final);path.write_bytes(data)
    digest=hashlib.sha256(data).hexdigest()
    report=export_package(path,tmp_path/'final.usda',expected_input_sha256=digest,require_physics=True)
    assert report['input_package_sha256']==digest
    embedded=final.root_definition.metadata['sca_physics']
    encoded=json.dumps(embedded,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    assert report['physics_metadata_sha256']==hashlib.sha256(encoded).hexdigest()
    assert path.read_bytes()==data
    assert report['status']['physics_static_validated'] is True
