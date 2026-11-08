"""STEP 입력 → PcbModel.

좌표 약속: 기판 아랫면(지그를 향하는 면)이 z=0 이 되도록 정렬한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

TOL = 1e-3


@dataclass
class Hole:
    x: float
    y: float
    d: float


@dataclass
class Protrusion:
    """기판 아랫면(z=0) 아래로 나온 솔리드의 XY bbox 와 돌출 높이."""

    xmin: float
    ymin: float
    xmax: float
    ymax: float
    height: float


@dataclass
class PcbModel:
    shape: cq.Shape          # 정렬된 PCB 전체 (간섭 검증용)
    outline: cq.Wire         # 기판 외곽 (z=0)
    thickness: float
    holes: list[Hole]
    protrusions: list[Protrusion]
    notes: list[str] = field(default_factory=list)


def _shells_to_solids(shape: cq.Shape) -> tuple[cq.Shape, list[str]]:
    """솔리드에 속하지 않은 셸을 솔리드로 바꾼다.

    셸 부품은 Solids() 에 안 잡히고 부피가 없어 간섭 검증에서도 안 보인다 (조용한 결손).
    """
    exp = TopExp_Explorer(shape.wrapped, TopAbs_ShapeEnum.TopAbs_SHELL, TopAbs_ShapeEnum.TopAbs_SOLID)
    closed, opened = [], []
    while exp.More():
        shell = cq.Shell(TopoDS.Shell_s(exp.Current()))
        if BRep_Tool.IsClosed_s(shell.wrapped):
            closed.append(cq.Solid.makeSolid(shell))
        else:
            bb = shell.BoundingBox()
            opened.append(
                cq.Solid.makeBox(
                    bb.xlen, bb.ylen, bb.zlen, pnt=cq.Vector(bb.xmin, bb.ymin, bb.zmin)
                )
            )
        exp.Next()

    n = len(closed) + len(opened)
    if n == 0:
        return shape, []

    note = (
        f"솔리드가 아닌 셸 부품 {n}개 — "
        f"닫힌 셸 {len(closed)}개는 솔리드로 변환, 열린 셸 {len(opened)}개는 바운딩박스로 대체"
    )
    return cq.Compound.makeCompound(shape.Solids() + closed + opened), [note]


def _board_index(solids: list[cq.Solid], board_max_thickness: float, path) -> int:
    """기판 = bbox 높이가 board_max_thickness 이하인 솔리드 중 XY 면적 최대."""
    best, best_area = -1, -1.0
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        if bb.zlen > board_max_thickness:
            continue
        area = bb.xlen * bb.ylen
        if area > best_area:
            best, best_area = i, area
    if best < 0:
        raise ValueError(
            f"기판 미검출: 두께 {board_max_thickness}mm 이하 솔리드가 없음 ({path})"
        )
    return best


def _bottom_face(board: cq.Solid) -> cq.Face:
    """기판 아랫면 = 평면 · 법선 z < -0.99 · 중심 |z| < TOL 중 면적 최대."""
    best, best_area = None, -1.0
    for f in board.Faces():
        if f.geomType() != "PLANE":
            continue
        if f.normalAt().z >= -0.99:
            continue
        if abs(f.Center().z) >= TOL:
            continue
        if f.Area() > best_area:
            best, best_area = f, f.Area()
    if best is None:
        raise ValueError("기판 아랫면 미검출: z=0 평면(법선 -Z)을 찾을 수 없음")
    return best


def _circle_hole(wire: cq.Wire) -> Hole | None:
    """모든 edge 가 같은 원의 호인 wire → Hole. (사각 개구부는 홀이 아니다)"""
    edges = wire.Edges()
    if not edges:
        return None
    r0, c0 = None, None
    for e in edges:
        if e.geomType() != "CIRCLE":
            return None
        r, c = e.radius(), e.arcCenter()
        if r0 is None:
            r0, c0 = r, c
        elif abs(r - r0) > TOL or (c - c0).Length > TOL:
            return None
    return Hole(c0.x, c0.y, 2 * r0)


def load_pcb(path, face_down: str = "bottom", board_max_thickness: float = 3.2) -> PcbModel:
    path = Path(path)
    wp = cq.importers.importStep(str(path))
    shape = cq.Compound.makeCompound(wp.vals())

    if face_down == "top":
        shape = shape.rotate((0, 0, 0), (1, 0, 0), 180)
    elif face_down != "bottom":
        raise ValueError(f'face_down 은 "bottom" 또는 "top" 이어야 함 (받은 값: {face_down!r})')

    shape, notes = _shells_to_solids(shape)

    solids = shape.Solids()
    idx = _board_index(solids, board_max_thickness, path)

    # 기판 아랫면이 z=0 이 되도록 전체 이동
    dz = -solids[idx].BoundingBox().zmin
    shape = shape.translate((0, 0, dz))
    solids = shape.Solids()
    board = solids[idx]

    thickness = board.BoundingBox().zlen
    face = _bottom_face(board)

    holes = []
    for w in face.innerWires():
        h = _circle_hole(w)
        if h is not None:
            holes.append(h)

    protrusions = []
    for i, s in enumerate(solids):
        if i == idx:
            continue
        bb = s.BoundingBox()
        if bb.zmin < -TOL:
            protrusions.append(Protrusion(bb.xmin, bb.ymin, bb.xmax, bb.ymax, -bb.zmin))

    return PcbModel(
        shape=shape,
        outline=face.outerWire(),
        thickness=thickness,
        holes=holes,
        protrusions=protrusions,
        notes=notes,
    )
