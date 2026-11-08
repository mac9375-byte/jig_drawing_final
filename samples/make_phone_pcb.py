"""(가상 SAMPLE) 휴대폰 메인 PCB STEP 생성기.

실제 고객 데이터가 아니다. 좌표·부품은 전부 가상이다.
2D 도면 생성기(make_phone_dxf.py)가 이 모듈의 배치를 재사용한다.
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq

T = 0.8  # 기판 두께

OUTLINE = [(0.0, 0.0), (70.0, 0.0), (70.0, 30.0), (55.0, 30.0), (55.0, 52.0), (0.0, 52.0)]

TOOLING = [(3.0, 3.0), (67.0, 3.0), (3.0, 49.0)]   # Ø2.0
SCREW = [(52.0, 27.0), (30.0, 49.0)]               # Ø1.6
TOOLING_D = 2.0
SCREW_D = 1.6
CAMERA = (8.0, 38.0, 20.0, 48.0)                   # 12x10 개구부, 중심 (14,43)

# (x0, y0, x1, y1, 높이, 번호)
TOP_PARTS = [
    (25.0, 8.0, 47.0, 30.0, 1.1, "SH2"),
    (5.0, 30.0, 20.0, 37.0, 1.0, "SH3"),
    (30.0, 40.0, 38.0, 43.0, 0.7, "J1"),
    (58.0, 10.0, 66.0, 13.0, 0.9, "J2"),
]

BOTTOM_PARTS = [
    (30.0, 8.0, 48.0, 22.0, 1.0, "SH1"),
    (8.0, 30.0, 14.0, 32.5, 0.6, "J3"),
    (58.0, 15.0, 68.0, 27.0, 1.4, "J5"),
    (58.0, 4.0, 60.0, 6.0, 1.5, "CL1"),
    (45.0, 45.0, 47.0, 47.0, 1.5, "CL2"),
]
for _i in range(8):
    _x = 5.0 + 2.0 * _i
    for _k, _y in enumerate((8.0, 10.0)):   # 0402
        BOTTOM_PARTS.append((_x, _y, _x + 1.0, _y + 0.5, 0.35, f"R{2 * _i + _k + 1}"))
    for _k, _y in enumerate((13.0, 15.0)):  # 0201
        BOTTOM_PARTS.append((_x, _y, _x + 0.6, _y + 0.3, 0.30, f"C{2 * _i + _k + 1}"))


def make_board() -> cq.Solid:
    """기판 (외곽 + 홀 + 카메라 개구부)."""
    board = (
        cq.Workplane("XY")
        .polyline(OUTLINE)
        .close()
        .extrude(T)
        .edges("|Z")
        .fillet(1.0)
    )
    solid = board.val()
    for x, y in TOOLING:
        solid = solid.cut(
            cq.Solid.makeCylinder(TOOLING_D / 2, T + 2, pnt=cq.Vector(x, y, -1.0))
        )
    for x, y in SCREW:
        solid = solid.cut(
            cq.Solid.makeCylinder(SCREW_D / 2, T + 2, pnt=cq.Vector(x, y, -1.0))
        )
    x0, y0, x1, y1 = CAMERA
    solid = solid.cut(
        cq.Solid.makeBox(x1 - x0, y1 - y0, T + 2, pnt=cq.Vector(x0, y0, -1.0))
    )
    return solid


def make_phone_pcb(path) -> str:
    shapes = [make_board()]
    for x0, y0, x1, y1, h, _name in TOP_PARTS:
        shapes.append(
            cq.Solid.makeBox(x1 - x0, y1 - y0, h, pnt=cq.Vector(x0, y0, T))
        )
    for x0, y0, x1, y1, h, _name in BOTTOM_PARTS:
        shapes.append(
            cq.Solid.makeBox(x1 - x0, y1 - y0, h, pnt=cq.Vector(x0, y0, -h))
        )
    cq.exporters.export(cq.Compound.makeCompound(shapes), str(path))
    return str(path)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "phone_main_pcb.step"
    make_phone_pcb(out)
    print(f"(가상 SAMPLE) {out} / 부품 {len(TOP_PARTS)} + {len(BOTTOM_PARTS)}")
