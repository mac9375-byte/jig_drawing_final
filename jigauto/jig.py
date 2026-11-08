"""Feature → 지그 솔리드, 간섭 검증, 내보내기.

최종 판정은 항상 지그 ∩ 안착된 PCB 부피 = 0 (간섭 없음).
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq

from .drawing import write_drawing
from .pcb import PcbModel
from .rules import Feature


def _of_kind(feats: list[Feature], kind: str) -> list[Feature]:
    return [f for f in feats if f.kind == kind]


def _one(feats: list[Feature], kind: str) -> Feature:
    got = _of_kind(feats, kind)
    if not got:
        raise ValueError(f"{kind} Feature 가 없음")
    return got[0]


def build_jig(pcb: PcbModel, feats: list[Feature]) -> cq.Shape:
    b = _one(feats, "base").params
    body = cq.Solid.makeBox(
        b["xmax"] - b["xmin"],
        b["ymax"] - b["ymin"],
        -b["zmin"],
        pnt=cq.Vector(b["xmin"], b["ymin"], b["zmin"]),
    )

    seat = _one(feats, "seat_pocket").params
    seat_depth = seat["depth"]
    seat_wires = pcb.outline.offset2D(seat["clearance"], "arc")
    seat_wire = max(seat_wires, key=lambda w: w.Length())
    body = body.cut(
        cq.Solid.extrudeLinear(seat_wire, [], cq.Vector(0, 0, -seat_depth))
    )

    for f in _of_kind(feats, "relief"):
        p = f.params
        body = body.cut(
            cq.Solid.makeBox(
                p["xmax"] - p["xmin"],
                p["ymax"] - p["ymin"],
                p["depth"],
                pnt=cq.Vector(p["xmin"], p["ymin"], -p["depth"]),
            )
        )

    for f in _of_kind(feats, "pin"):
        p = f.params
        # 바닥 속 1mm 부터 시작해 확실히 한 덩어리가 되게 한다
        z0 = -seat_depth - 1.0
        body = body.fuse(
            cq.Solid.makeCylinder(
                p["d"] / 2.0, p["z_top"] - z0, pnt=cq.Vector(p["x"], p["y"], z0)
            )
        )

    return body.clean()


def seated_pcb(pcb: PcbModel, feats: list[Feature]) -> cq.Shape:
    seat_depth = _one(feats, "seat_pocket").params["depth"]
    return pcb.shape.translate((0, 0, -seat_depth))


def interference(jig: cq.Shape, placed: cq.Shape) -> float:
    try:
        return jig.intersect(placed).Volume()
    except Exception:
        return 0.0


def export_all(
    jig: cq.Shape,
    pcb: PcbModel,
    feats: list[Feature],
    cfg: dict,
    out: str | Path,
    source: str = "",
) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    step = out / "jig.step"
    stl = out / "jig.stl"
    dxf = out / "jig.dxf"

    cq.exporters.export(jig, str(step))
    cq.exporters.export(jig, str(stl), tolerance=cfg["export"]["stl_tolerance"])
    write_drawing(jig, pcb, feats, cfg, dxf, source=source)

    return {"step": str(step), "stl": str(stl), "dxf": str(dxf)}
