"""Make an optional offline, interactive HTML preview of visible USD meshes."""

from pathlib import Path

import numpy as np

from .dependencies import require_usd
from .model import transformed_points


def write_preview(usd_path, output_path):
    _, _, Usd, UsdGeom, _, _ = require_usd()
    try:
        import plotly.graph_objects as go
    except ImportError as exc:
        raise ValueError(
            "HTML preview requires plotly (an existing SCA dependency)"
        ) from exc
    source = Path(usd_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.suffix.lower() != ".html":
        raise ValueError("preview output must end in .html")
    if destination.exists():
        raise FileExistsError(f"Preview already exists: {destination}")
    stage = Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError(f"Cannot open USD: {source}")
    cache = UsdGeom.XformCache()
    figure = go.Figure()
    colours = ("#b7c5d6", "#d3a875", "#86b3ac", "#be9cbc")
    for prim in stage.Traverse():
        if (
            not prim.IsA(UsdGeom.Mesh)
            or UsdGeom.Imageable(prim).ComputeVisibility() == "invisible"
        ):
            continue
        mesh = UsdGeom.Mesh(prim)
        points = transformed_points(
            np.asarray(mesh.GetPointsAttr().Get()),
            np.asarray(cache.GetLocalToWorldTransform(prim)).T,
        )
        counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get())
        if not np.all(counts == 3):
            raise ValueError("HTML preview currently requires triangle meshes")
        triangles = np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape((-1, 3))
        name = prim.GetDisplayName() or prim.GetName()
        figure.add_trace(
            go.Mesh3d(
                x=points[:, 0],
                y=points[:, 1],
                z=points[:, 2],
                i=triangles[:, 0],
                j=triangles[:, 1],
                k=triangles[:, 2],
                name=name,
                text=name,
                hovertemplate="%{text}<extra></extra>",
                color=colours[len(figure.data) % len(colours)],
                flatshading=False,
                showlegend=True,
            )
        )
    if not figure.data:
        raise ValueError("USD has no visible triangle meshes")
    up = {"x": 0, "y": 0, "z": 0}
    up[str(UsdGeom.GetStageUpAxis(stage)).lower()] = 1
    figure.update_layout(
        title=stage.GetDefaultPrim().GetDisplayName() or source.stem,
        scene=dict(
            aspectmode="data",
            xaxis_title="X (m)",
            yaxis_title="Y (m)",
            zaxis_title="Z (m)",
            camera=dict(up=up),
        ),
        margin=dict(l=0, r=0, b=0, t=50),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(figure.to_html(full_html=True, include_plotlyjs=True))
    return {
        "output": str(destination),
        "visible_part_count": len(figure.data),
        "geometry_source": str(source),
        "simulator_validated": False,
    }
