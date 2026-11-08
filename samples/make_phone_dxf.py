"""(가상 SAMPLE) 고객 2D 제품도면(DXF) 생성기 4종.

실제 고객 도면이 아니다. 레이어·개체 구성을 일부러 제각각으로 만든다.
  clean      레이어 정상
  messy      레이어 이름 혼용(한글·영문·오타), 수동소자는 블록 INSERT
  layer0     전부 0번 레이어 + 모든 사각형·외곽을 낱개 LINE/ARC + 외곽 0.005 미세 틈
  unlabeled  layer0 과 같고 뷰 이름만 VIEW A / VIEW B (Bottom 표시 없음)
"""

from __future__ import annotations

import math
from pathlib import Path

import cadquery as cq
import ezdxf

from make_phone_pcb import BOTTOM_PARTS, T, TOP_PARTS, make_board

W = 70.0
TOP_AT = (40.0, 170.0)
BOT_AT = (180.0, 170.0)
SIDE_AT = (40.0, 120.0)

VIEW_NAMES = {
    "clean": ["BOTTOM VIEW"],
    "messy": ["TOP VIEW", "BOTTOM VIEW (1차면)"],
    "layer0": ["TOP VIEW", "BOTTOM VIEW"],
    "unlabeled": ["VIEW A", "VIEW B"],
}

MESSY_COMP_LAYERS = ["Bot부품", "COMP-B", "bottom_comp", "0", "외형선"]


def _layers(kind: str) -> dict:
    if kind == "clean":
        return {
            "outline": "OUTLINE",
            "hole": "HOLE",
            "comp": "COMP_BOT",
            "detail": "COMP_DETAIL",
            "text": "TEXT",
            "frame": "FRAME",
            "dim": "DIM",
        }
    if kind == "messy":
        return {
            "outline": "외형선",
            "hole": "0",
            "comp": None,  # 순환
            "detail": "0",
            "text": "문자",
            "frame": "TITLE",
            "dim": "치수",
        }
    return {k: "0" for k in ("outline", "hole", "comp", "detail", "text", "frame", "dim")}


def bx(x: float) -> float:
    """Bottom 뷰는 좌우 반전 — 기판 x → 도면 x."""
    return BOT_AT[0] + W - x


def by(y: float) -> float:
    return BOT_AT[1] + y


def _bottom_face(solid: cq.Solid) -> cq.Face:
    faces = [
        f
        for f in solid.Faces()
        if f.geomType() == "PLANE" and f.normalAt().z < -0.9 and abs(f.Center().z) < 1e-6
    ]
    return max(faces, key=lambda f: f.Area())


def _add_edge(msp, e: cq.Edge, layer: str, shorten: float = 0.0):
    if e.geomType() == "LINE":
        p1, p2 = e.startPoint(), e.endPoint()
        if shorten:
            d = p2 - p1
            n = d.Length
            p2 = p1 + d * ((n - shorten) / n)
        msp.add_line((p1.x, p1.y), (p2.x, p2.y), dxfattribs={"layer": layer})
        return
    if e.geomType() == "CIRCLE":
        c, r = e.arcCenter(), e.radius()
        if e.IsClosed():
            msp.add_circle((c.x, c.y), r, dxfattribs={"layer": layer})
            return
        p1, p2, pm = e.startPoint(), e.endPoint(), e.positionAt(0.5)
        a1 = math.degrees(math.atan2(p1.y - c.y, p1.x - c.x)) % 360
        a2 = math.degrees(math.atan2(p2.y - c.y, p2.x - c.x)) % 360
        am = math.degrees(math.atan2(pm.y - c.y, pm.x - c.x)) % 360
        span = (a2 - a1) % 360
        if not (0 < (am - a1) % 360 < span):
            a1, a2 = a2, a1
        msp.add_arc((c.x, c.y), r, a1, a2, dxfattribs={"layer": layer})
        return
    raise ValueError(f"DXF 로 옮길 수 없는 edge 종류: {e.geomType()}")


def _add_wires(msp, face: cq.Face, layers: dict, gap_on_first: bool = False):
    outer = face.outerWire()
    for i, e in enumerate(outer.Edges()):
        _add_edge(
            msp,
            e,
            layers["outline"],
            shorten=0.005 if (gap_on_first and i == 0 and e.geomType() == "LINE") else 0.0,
        )
    for w in face.innerWires():
        edges = w.Edges()
        layer = layers["hole"] if len(edges) == 1 and edges[0].geomType() == "CIRCLE" else layers["outline"]
        for e in edges:
            _add_edge(msp, e, layer)


def _rect(msp, x0, y0, x1, y1, layer, split: bool, skip_side: int | None = None):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if not split and skip_side is None:
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
        return
    for i in range(4):
        if skip_side is not None and i == skip_side:
            continue
        msp.add_line(pts[i], pts[(i + 1) % 4], dxfattribs={"layer": layer})


def make_phone_dxf(path, kind: str = "clean", broken: bool = False, round_part: bool = False) -> str:
    if kind not in VIEW_NAMES:
        raise ValueError(f"kind 는 {sorted(VIEW_NAMES)} 중 하나여야 함 (받은 값: {kind!r})")

    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()
    lay = _layers(kind)
    split = kind in ("layer0", "unlabeled")

    for name in set(v for v in lay.values() if v) | set(MESSY_COMP_LAYERS):
        if name != "0" and name not in doc.layers:
            doc.layers.add(name)

    def comp_layer(i: int) -> str:
        if lay["comp"] is not None:
            return lay["comp"]
        return MESSY_COMP_LAYERS[i % len(MESSY_COMP_LAYERS)]

    def text(s, x, y, height=2.5, layer=None):
        msp.add_text(
            s, dxfattribs={"layer": layer or lay["text"], "height": height}
        ).set_placement((x, y))

    # --- 도면 테두리 + 표제란 ----------------------------------------------
    _rect(msp, 0, 0, 420, 297, lay["frame"], split)
    _rect(msp, 10, 10, 410, 287, lay["frame"], split)
    _rect(msp, 290, 10, 410, 50, lay["frame"], split)
    cells = [
        ("회사", "(가상) 고객사"),
        ("품명", "휴대폰 메인 PCB (SAMPLE)"),
        ("도번", "CUST-SAMPLE-0001 (가상)"),
        ("모델", "PHONE-MAIN-SAMPLE"),
        ("척도", "1:1 (가상)"),
        ("단위", "mm / SAMPLE"),
    ]
    for i, (label, value) in enumerate(cells):
        cx = 290 + 40 * (i % 3)
        cy = 30 - 20 * (i // 3)
        _rect(msp, cx, cy, cx + 40, cy + 20, lay["frame"], split)
        text(f"{label}: {value}", cx + 1, cy + 8, height=2.0)
    text("(가상 SAMPLE) 실제 고객 도면 아님", 20, 270, height=3.0)

    # --- Bottom 뷰 ----------------------------------------------------------
    face = _bottom_face(make_board())
    bot = face.mirror("YZ").translate((BOT_AT[0] + W, BOT_AT[1], 0))
    _add_wires(msp, bot, lay, gap_on_first=split)

    blocks = {}
    if kind == "messy":
        for name, (l, w) in (("PASSIVE_0402", (1.0, 0.5)), ("PASSIVE_0201", (0.6, 0.3))):
            blk = doc.blocks.new(name=name)
            blk.add_lwpolyline(
                [(0, 0), (l, 0), (l, w), (0, w)], close=True, dxfattribs={"layer": "0"}
            )
            blocks[name] = (l, w)

    for i, (x0, y0, x1, y1, _h, name) in enumerate(BOTTOM_PARTS):
        dx0, dx1 = bx(x1), bx(x0)
        dy0, dy1 = by(y0), by(y1)
        passive = name[0] in ("R", "C") and name[1:].isdigit()
        if kind == "messy" and passive:
            blk = "PASSIVE_0402" if (x1 - x0) > 0.8 else "PASSIVE_0201"
            msp.add_blockref(blk, (dx0, dy0), dxfattribs={"layer": comp_layer(i)})
        else:
            skip = 2 if (broken and name == "J5") else None
            _rect(msp, dx0, dy0, dx1, dy1, comp_layer(i), split, skip_side=skip)
        if not passive:
            text(name, dx0 + 0.2, dy0 + 0.2, height=0.3)

    # SH1 안쪽 벽선 (실드캔 상세) + SIM 1번 핀 표시
    sh1 = [p for p in BOTTOM_PARTS if p[5] == "SH1"][0]
    _rect(
        msp,
        bx(sh1[2]) + 0.3,
        by(sh1[1]) + 0.3,
        bx(sh1[0]) - 0.3,
        by(sh1[3]) - 0.3,
        lay["detail"],
        split,
    )
    msp.add_circle((bx(67.0), by(26.0)), 0.25, dxfattribs={"layer": lay["detail"]})

    if round_part:
        msp.add_circle((bx(40.0), by(35.0)), 1.5, dxfattribs={"layer": comp_layer(0)})

    msp.add_linear_dim(
        base=(bx(35.0), by(-8.0)),
        p1=(bx(0.0), by(0.0)),
        p2=(bx(70.0), by(0.0)),
        dimstyle="EZDXF",
        override={"dimlfac": 1.0},
        dxfattribs={"layer": lay["dim"]},
    ).render()

    names = VIEW_NAMES[kind]
    text(names[-1], BOT_AT[0], BOT_AT[1] - 12, height=3.0)

    # --- Top 뷰 · 측면 뷰 (clean 외) ---------------------------------------
    if kind != "clean":
        top = face.translate((TOP_AT[0], TOP_AT[1], 0))
        _add_wires(msp, top, lay)
        for i, (x0, y0, x1, y1, _h, name) in enumerate(TOP_PARTS):
            _rect(
                msp,
                TOP_AT[0] + x0,
                TOP_AT[1] + y0,
                TOP_AT[0] + x1,
                TOP_AT[1] + y1,
                comp_layer(i),
                split,
            )
            text(name, TOP_AT[0] + x0 + 0.2, TOP_AT[1] + y0 + 0.2, height=0.3)
        text(names[0], TOP_AT[0], TOP_AT[1] - 12, height=3.0)

        sx, sy = SIDE_AT
        _rect(msp, sx, sy, sx + W, sy + T, lay["outline"], split)
        for x0, _y0, x1, _y1, h, _name in TOP_PARTS:
            _rect(msp, sx + x0, sy + T, sx + x1, sy + T + h, lay["comp"] or "0", split)
        for x0, _y0, x1, _y1, h, _name in BOTTOM_PARTS[:5]:
            _rect(msp, sx + x0, sy - h, sx + x1, sy, lay["comp"] or "0", split)
        text("SIDE VIEW", sx, sy - 12, height=3.0)

    doc.saveas(str(path))
    return str(path)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    for kind in VIEW_NAMES:
        out = here / f"phone_main_{kind}.dxf"
        make_phone_dxf(out, kind=kind)
        print(f"(가상 SAMPLE) {out}")
