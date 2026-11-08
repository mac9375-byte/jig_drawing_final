"""(가상 SAMPLE) 밀집·혼합 높이 PCB — 한 덩어리 병합 증상 재현용.

실제 고객 데이터가 아니다. KiCad StickHub 예제에서 나온 문제(아랫면 부품 60건이
포켓 하나로 병합돼 기판 폭 전체가 가장 깊은 부품 깊이로 파임)를 가상으로 재현한다.
낮은 부품 사이에 높은 부품 2개를 섞어 둬서 깊이 단차의 효과가 드러난다.
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq

T = 1.0
BOARD = (18.0, 36.0)
FILLET = 1.5
TOOLING = [(3.0, 3.0), (15.0, 33.0)]
TOOLING_D = 2.0

PART = 2.0          # 부품 한 변
PITCH = 3.5         # 부품 간격 (여유 0.3 을 더해도 사이 벽 0.9mm < merge_gap)
COLS, ROWS = 5, 6
ORIGIN = (1.0, 8.0)
LOW_H, TALL_H = 0.4, 3.0
TALL = {(2, 2), (4, 5)}  # (col, row) — 나머지는 낮은 부품


def parts() -> list[tuple[float, float, float, float, float, str]]:
    out = []
    for c in range(COLS):
        for r in range(ROWS):
            x0 = ORIGIN[0] + PITCH * c
            y0 = ORIGIN[1] + PITCH * r
            h = TALL_H if (c, r) in TALL else LOW_H
            out.append((x0, y0, x0 + PART, y0 + PART, h, f"U{c}{r}"))
    return out


def make_board() -> cq.Solid:
    board = (
        cq.Workplane("XY")
        .moveTo(BOARD[0] / 2, BOARD[1] / 2)
        .rect(*BOARD)
        .extrude(T)
        .edges("|Z")
        .fillet(FILLET)
        .val()
    )
    for x, y in TOOLING:
        board = board.cut(
            cq.Solid.makeCylinder(TOOLING_D / 2, T + 2, pnt=cq.Vector(x, y, -1.0))
        )
    return board


def make_dense_pcb(path) -> str:
    shapes = [make_board()]
    for x0, y0, x1, y1, h, _name in parts():
        shapes.append(cq.Solid.makeBox(x1 - x0, y1 - y0, h, pnt=cq.Vector(x0, y0, -h)))
    cq.exporters.export(cq.Compound.makeCompound(shapes), str(path))
    return str(path)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "dense_pcb.step"
    make_dense_pcb(out)
    print(f"(가상 SAMPLE) {out} / 아랫면 부품 {len(parts())}")
