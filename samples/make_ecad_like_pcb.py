"""(가상 SAMPLE) 실제 ECAD 출력 구조를 흉내 낸 PCB STEP.

실제 고객 데이터가 아니다. 다음을 일부러 섞는다:
- 이름 붙은 어셈블리 계층 (PCB_ASSY / COMPONENTS)
- 다중 솔리드 부품 (칩 단자, SOT-23 리드, B2B 핀)
- 아랫면 부품은 X축 180° 뒤집어 배치 (ECAD 방식)
- 비아 207개, 파묻힌 부품, 90° 회전 부품, THT 관통 핀
- 셸 부품 (닫힌 1, 열린 2) — Solids() 에 안 잡히는 조용한 결손 재현용
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq

from make_phone_pcb import CAMERA, OUTLINE, SCREW, SCREW_D, T, TOOLING, TOOLING_D

THT = [(34.0, 47.0), (36.0, 47.0)]
THT_D = 0.9
VIA_D = 0.3


def _vias() -> list[tuple[float, float]]:
    pts = [(50.0 + i, 32.0 + j) for i in range(5) for j in range(19)]
    pts += [(22.0 + i, 35.0 + j) for i in range(7) for j in range(16)]
    return pts


def make_board() -> cq.Solid:
    board = (
        cq.Workplane("XY").polyline(OUTLINE).close().extrude(T).edges("|Z").fillet(1.0)
    )
    solid = board.val()

    cyls = []
    for x, y in TOOLING:
        cyls.append(cq.Solid.makeCylinder(TOOLING_D / 2, T + 2, pnt=cq.Vector(x, y, -1.0)))
    for x, y in SCREW:
        cyls.append(cq.Solid.makeCylinder(SCREW_D / 2, T + 2, pnt=cq.Vector(x, y, -1.0)))
    for x, y in THT:
        cyls.append(cq.Solid.makeCylinder(THT_D / 2, T + 2, pnt=cq.Vector(x, y, -1.0)))
    for x, y in _vias():
        cyls.append(cq.Solid.makeCylinder(VIA_D / 2, T + 2, pnt=cq.Vector(x, y, -1.0)))
    solid = solid.cut(cq.Compound.makeCompound(cyls))

    x0, y0, x1, y1 = CAMERA
    return solid.cut(
        cq.Solid.makeBox(x1 - x0, y1 - y0, T + 2, pnt=cq.Vector(x0, y0, -1.0))
    )


# --- 부품 (로컬 좌표: 실장면 z=0, XY 중심 원점) -----------------------------

def _box(l, w, h, z0=0.0) -> cq.Solid:
    return cq.Solid.makeBox(l, w, h, pnt=cq.Vector(-l / 2, -w / 2, z0))


def chip(l: float, w: float, h: float) -> list[cq.Solid]:
    """칩 저항·콘덴서: 본체 + 양끝 단자 (3 솔리드)."""
    t = 0.2 * l
    return [
        _box(l, w, h),
        cq.Solid.makeBox(t, w, h, pnt=cq.Vector(-l / 2, -w / 2, 0.0)),
        cq.Solid.makeBox(t, w, h, pnt=cq.Vector(l / 2 - t, -w / 2, 0.0)),
    ]


def sot23() -> list[cq.Solid]:
    """SOT-23: 본체 2.9x1.3 (z 0.1..1.0) + 리드 3."""
    out = [_box(2.9, 1.3, 0.9, z0=0.1)]
    for x, y in ((-0.95, -0.9), (0.95, -0.9), (0.0, 0.9)):
        out.append(cq.Solid.makeBox(0.5, 0.4, 0.1, pnt=cq.Vector(x - 0.25, y - 0.2, 0.0)))
    return out


def b2b(l: float, w: float, h: float, n: int) -> list[cq.Solid]:
    """보드-투-보드 커넥터: 본체(z 0.05..h) + 핀 2n."""
    out = [_box(l, w, h - 0.05, z0=0.05)]
    step = 0.8 * l / max(n - 1, 1)
    for i in range(n):
        x = -0.4 * l + i * step
        for sign in (-1, 1):
            y = sign * (w / 2 - 0.2)
            out.append(
                cq.Solid.makeBox(0.3, 0.4, 0.05, pnt=cq.Vector(x - 0.15, y - 0.2, 0.0))
            )
    return out


def shield(l: float, w: float, h: float) -> list[cq.Solid]:
    """실드캔: 바닥 열린 캔 (벽 0.1, 천장 0.1)."""
    can = _box(l, w, h)
    inner = cq.Solid.makeBox(
        l - 0.2, w - 0.2, h - 0.1, pnt=cq.Vector(-(l - 0.2) / 2, -(w - 0.2) / 2, 0.0)
    )
    return [can.cut(inner)]


def closed_shell(l: float, w: float, h: float) -> list[cq.Shell]:
    """닫힌 셸 (박스 6면) — 부피 계산에 안 잡히는 부품."""
    return [cq.Shell.makeShell(_box(l, w, h).Faces())]


def open_shell(l: float, w: float, h: float) -> list[cq.Shell]:
    """열린 셸 (아랫면 뺀 박스)."""
    faces = [f for f in _box(l, w, h).Faces() if f.normalAt().z >= -0.5]
    return [cq.Shell.makeShell(faces)]


def tht_conn() -> list[cq.Solid]:
    """THT 커넥터: 본체 4x2x1.0 + 관통 핀 Ø0.5 두 개 (z -2.3..1.0)."""
    out = [_box(4.0, 2.0, 1.0)]
    for x in (-1.0, 1.0):
        out.append(cq.Solid.makeCylinder(0.25, 3.3, pnt=cq.Vector(x, 0.0, -2.3)))
    return out


def _place(shapes, x, y, z=0.0, rot=0.0, flip=False):
    out = []
    for s in shapes:
        if flip:
            s = s.rotate((0, 0, 0), (1, 0, 0), 180)
        if rot:
            s = s.rotate((0, 0, 0), (0, 0, 1), rot)
        out.append(s.translate((x, y, z)))
    return out


def components() -> list[tuple[str, list]]:
    """(부품명, 솔리드/셸 목록)."""
    parts: list[tuple[str, list]] = []

    # 윗면 (z = T 위로)
    parts.append(("U1", _place(shield(22, 22, 1.1), 36.0, 19.0, T)))
    parts.append(("U2", _place(shield(15, 7, 1.0), 12.5, 33.5, T)))
    parts.append(("J1", _place(b2b(8, 3, 0.7, 10), 34.0, 41.5, T)))
    parts.append(("J2", _place(b2b(8, 3, 0.9, 6), 62.0, 11.5, T, rot=90)))
    parts.append(("J3", _place(tht_conn(), 35.0, 47.0, T)))

    # 아랫면 (X축 180° 뒤집어 배치)
    parts.append(("SH1", _place(shield(18, 14, 1.0), 39.0, 15.0, 0.05, flip=True)))
    parts.append(("J4", _place(b2b(6, 2.5, 0.6, 8), 11.0, 31.25, 0.0, rot=90, flip=True)))
    parts.append(("J5", _place(closed_shell(10, 12, 1.4), 63.0, 21.0, 0.0, flip=True)))
    parts.append(("CL1", _place(open_shell(2, 2, 1.5), 59.0, 5.0, 0.0, flip=True)))
    parts.append(("CL2", _place(open_shell(2, 2, 1.5), 46.0, 46.0, 0.0, flip=True)))
    parts.append(("Q1", _place(sot23(), 25.0, 20.0, 0.0, flip=True)))

    for i in range(8):
        x = 5.0 + 2.0 * i
        for k, y in enumerate((8.0, 10.0)):
            parts.append(
                (
                    f"R{2 * i + k + 1}",
                    _place(chip(1.0, 0.5, 0.35), x + 0.5, y + 0.25, 0.0, flip=True),
                )
            )
        for k, y in enumerate((13.0, 15.0)):
            parts.append(
                (
                    f"C{2 * i + k + 1}",
                    _place(chip(0.6, 0.3, 0.30), x + 0.3, y + 0.15, 0.0, flip=True),
                )
            )
    return parts


def make_ecad_like_pcb(path) -> str:
    assy = cq.Assembly(name="PCB_ASSY")
    assy.add(make_board(), name="PCB")

    comps = cq.Assembly(name="COMPONENTS")
    for name, shapes in components():
        if len(shapes) == 1:
            comps.add(shapes[0], name=name)
        else:
            comps.add(cq.Compound.makeCompound(shapes), name=name)
    assy.add(comps, name="COMPONENTS")

    assy.save(str(path))
    return str(path)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "ecad_like_pcb.step"
    make_ecad_like_pcb(out)
    print(f"(가상 SAMPLE) {out} / 부품 {len(components())}")
