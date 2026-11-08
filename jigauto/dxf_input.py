"""고객 2D 제품도면(DXF) → PcbModel.

원칙
- 레이어 이름을 보지 않는다 (개체 종류·형상으로 분류). 레이어는 설정의 별칭과 맞을 때만 힌트.
- 애매하면 포켓이 생기는 쪽으로 판정한다 (포켓 누락 = 충돌, 포켓 추가 = 무해).
- 뷰가 애매하면 ERROR 로 멈춘다 (아무거나 고르는 폴백 금지).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cadquery as cq
import ezdxf

from .pcb import Hole, PcbModel, Protrusion

TOL = 1e-3

# 형상에서 빼는 개체 (문자·치수·지시선·해치 등)
IGNORED = {
    "TEXT",
    "MTEXT",
    "DIMENSION",
    "LEADER",
    "MULTILEADER",
    "HATCH",
    "ATTRIB",
    "ATTDEF",
    "POINT",
    "SOLID",
    "WIPEOUT",
    "IMAGE",
    "VIEWPORT",
    "MLINE",
    "XLINE",
    "RAY",
    "TOLERANCE",
}


@dataclass(eq=False)
class Loop:
    """닫힌 루프 — 비교는 객체 동일성으로 한다."""

    wire: cq.Wire
    face: cq.Face
    area: float
    bbox: object
    circle: tuple | None = None


# --- 읽기 -------------------------------------------------------------------

def _arc_edge(e) -> cq.Edge:
    ocs = e.ocs()
    cx, cy, cz = e.dxf.center
    r = e.dxf.radius
    a1, a2 = e.dxf.start_angle, e.dxf.end_angle
    span = (a2 - a1) % 360
    if span == 0:
        span = 360
    pts = []
    for a in (a1, a1 + span / 2, a1 + span):
        rad = math.radians(a)
        p = ocs.to_wcs((cx + r * math.cos(rad), cy + r * math.sin(rad), cz))
        pts.append(cq.Vector(p[0], p[1], 0))
    return cq.Edge.makeThreePointArc(*pts)


def _circle_edge(e) -> cq.Edge:
    ocs = e.ocs()
    c = ocs.to_wcs(e.dxf.center)
    return cq.Edge.makeCircle(e.dxf.radius, pnt=cq.Vector(c[0], c[1], 0))


def _line_edge(e) -> cq.Edge | None:
    p1 = cq.Vector(e.dxf.start[0], e.dxf.start[1], 0)
    p2 = cq.Vector(e.dxf.end[0], e.dxf.end[1], 0)
    if (p2 - p1).Length < TOL:
        return None
    return cq.Edge.makeLine(p1, p2)


def _read(msp) -> tuple[list[list[cq.Edge]], list[cq.Edge], list[tuple[str, tuple]], dict]:
    """(닫힌 루프 edge 묶음, 낱개 edge, (문자, 위치), 읽지 않은 개체 종류별 개수)."""
    groups: list[list[cq.Edge]] = []
    free: list[cq.Edge] = []
    texts: list[tuple[str, tuple]] = []
    skipped: dict = {}

    def walk(entities, depth=0):
        for e in entities:
            t = e.dxftype()
            if t == "INSERT":
                if depth > 8:
                    continue
                walk(e.virtual_entities(), depth + 1)
                try:
                    walk(e.attribs, depth + 1)
                except AttributeError:
                    pass
                continue
            if t in ("TEXT", "ATTRIB"):
                texts.append((str(e.dxf.text), tuple(e.dxf.insert)[:2]))
                continue
            if t == "MTEXT":
                texts.append((e.plain_text(), tuple(e.dxf.insert)[:2]))
                continue
            if t in IGNORED:
                continue
            if t == "LINE":
                edge = _line_edge(e)
                if edge is not None:
                    free.append(edge)
                continue
            if t == "ARC":
                free.append(_arc_edge(e))
                continue
            if t == "CIRCLE":
                groups.append([_circle_edge(e)])
                continue
            if t in ("LWPOLYLINE", "POLYLINE"):
                edges = []
                for sub in e.virtual_entities():
                    if sub.dxftype() == "LINE":
                        edge = _line_edge(sub)
                        if edge is not None:
                            edges.append(edge)
                    elif sub.dxftype() == "ARC":
                        edges.append(_arc_edge(sub))
                if not edges:
                    continue
                if getattr(e, "closed", False) or bool(e.dxf.get("flags", 0) & 1):
                    groups.append(edges)
                else:
                    free.extend(edges)
                continue
            skipped[t] = skipped.get(t, 0) + 1

    walk(msp)
    return groups, free, texts, skipped


# --- 낱개 선 잇기 -----------------------------------------------------------

def _heal(e: cq.Edge, pa: tuple, pb: tuple) -> cq.Edge:
    """끝점을 노드 좌표로 스냅 — 도면의 미세 틈(≤ join_tol)을 메워 wire 가 닫히게 한다."""
    a, b = cq.Vector(pa[0], pa[1], 0), cq.Vector(pb[0], pb[1], 0)
    try:
        if e.geomType() == "LINE":
            return cq.Edge.makeLine(a, b)
        if e.geomType() == "CIRCLE":
            return cq.Edge.makeThreePointArc(a, e.positionAt(0.5), b)
    except Exception:
        return e
    return e


def _chains(
    edges: list[cq.Edge], tol: float
) -> tuple[list[list[cq.Edge]], list[list[cq.Edge]]]:
    """끝점 차수가 모두 2 인 덩어리만 닫힌 루프로 인정한다 (나머지는 열린 덩어리).

    전부 한꺼번에 이으면 표제란·테두리가 엉뚱한 고리가 돼 잘못된 뷰를 조용히 고른다.
    """
    cells: dict = {}
    nodes: list[tuple[float, float]] = []

    def node(p) -> int:
        key = (round(p.x / tol), round(p.y / tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                hit = cells.get((key[0] + dx, key[1] + dy))
                if hit is not None:
                    return hit
        idx = len(nodes)
        nodes.append((p.x, p.y))
        cells[key] = idx
        return idx

    seen = set()
    pairs = []
    for e in edges:
        a, b = node(e.startPoint()), node(e.endPoint())
        mid = e.positionAt(0.5)
        key = (min(a, b), max(a, b), round(mid.x / tol), round(mid.y / tol))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((e, a, b))

    parent = list(range(len(nodes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for _e, a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    degree: dict = {}
    comps: dict = {}
    comp_nodes: dict = {}
    for e, a, b in pairs:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
        root = find(a)
        comps.setdefault(root, []).append((e, a, b))
        comp_nodes.setdefault(root, set()).update((a, b))

    loops, open_groups = [], []
    for root, group in comps.items():
        if len(group) >= 2 and all(degree.get(n) == 2 for n in comp_nodes[root]):
            loops.append([_heal(e, nodes[a], nodes[b]) for e, a, b in group])
        else:
            open_groups.append([e for e, _a, _b in group])
    return loops, open_groups


# --- 루프 만들기 ------------------------------------------------------------

def _make_loop(edges: list[cq.Edge]) -> Loop | None:
    try:
        wire = cq.Wire.assembleEdges(edges)
        face = cq.Face.makeFromWires(wire)
        area = face.Area()
    except Exception:
        return None
    if area <= TOL:
        return None
    circle = None
    if len(edges) == 1 and edges[0].geomType() == "CIRCLE" and edges[0].IsClosed():
        c = edges[0].arcCenter()
        circle = (c.x, c.y, 2 * edges[0].radius())
    return Loop(wire=wire, face=face, area=area, bbox=wire.BoundingBox(), circle=circle)


def _bbox_holds(a, b, tol: float = 1e-6) -> bool:
    return (
        a.xmin - tol <= b.xmin
        and a.ymin - tol <= b.ymin
        and a.xmax + tol >= b.xmax
        and a.ymax + tol >= b.ymax
    )


def _holds(a: Loop, b: Loop) -> bool:
    if a is b or a.area <= b.area:
        return False
    if not _bbox_holds(a.bbox, b.bbox):
        return False
    try:
        return a.face.intersect(b.face).Area() >= b.area * (1 - 1e-6)
    except Exception:
        return False


def _is_rect(loop: Loop) -> bool:
    bb = loop.bbox
    return abs(loop.area - bb.xlen * bb.ylen) <= max(1e-6, 1e-6 * loop.area)


def _group_bbox(edges: list[cq.Edge]):
    return cq.Compound.makeCompound(edges).BoundingBox()


def _strip_border(loops: list[Loop], open_groups: list[list[cq.Edge]]) -> list[Loop]:
    """다른 모든 형상을 감싸는 직사각형 = 도면 테두리 → 제거 (이중 테두리도 반복)."""
    boxes = [_group_bbox(g) for g in open_groups]
    changed = True
    while changed:
        changed = False
        for cand in sorted(loops, key=lambda l: -l.area):
            if not _is_rect(cand):
                continue
            rest = [l for l in loops if l is not cand]
            if all(_bbox_holds(cand.bbox, l.bbox) for l in rest) and all(
                _bbox_holds(cand.bbox, b) for b in boxes
            ):
                loops = rest
                changed = True
                break
    return loops


def _parents(loops: list[Loop]) -> dict:
    """각 루프를 감싸는 가장 작은 루프."""
    parent = {}
    for l in loops:
        best = None
        for other in loops:
            if _holds(other, l) and (best is None or other.area < best.area):
                best = other
        parent[id(l)] = best
    return parent


# --- 뷰 고르기 --------------------------------------------------------------

def _dist(bb, p) -> float:
    dx = max(bb.xmin - p[0], 0.0, p[0] - bb.xmax)
    dy = max(bb.ymin - p[1], 0.0, p[1] - bb.ymax)
    return (dx * dx + dy * dy) ** 0.5


def _tag_views(cands: list[Loop], texts, d2) -> dict:
    tags: dict = {}
    bottom = [s.upper() for s in d2["bottom_labels"]]
    top = [s.upper() for s in d2["top_labels"]]
    for raw, pos in texts:
        s = raw.upper()
        kind = None
        if any(lab in s for lab in bottom):
            kind = "bottom"
        elif any(lab in s for lab in top):
            kind = "top"
        if kind is None or not cands:
            continue
        near = min(cands, key=lambda l: _dist(l.bbox, pos))
        tags.setdefault(id(near), set()).add(kind)
    return tags


def _describe(cands: list[Loop]) -> str:
    out = []
    for i, l in enumerate(cands, 1):
        bb = l.bbox
        out.append(
            f"{i}번 x {bb.xmin:.1f}..{bb.xmax:.1f}, y {bb.ymin:.1f}..{bb.ymax:.1f}"
        )
    return "; ".join(out)


def _pick(cands: list[Loop], tags: dict, view: int | None, path) -> tuple[Loop, list[str]]:
    if not cands:
        raise ValueError(
            f"기판 미검출: 닫힌 루프 안에 부품·홀이 있는 평면 뷰를 찾지 못함 ({path})"
        )
    if view is not None:
        if not 1 <= view <= len(cands):
            raise ValueError(
                f"--view {view} 는 범위 밖 (후보 {len(cands)}개): {_describe(cands)}"
            )
        return cands[view - 1], []

    bottoms = [l for l in cands if "bottom" in tags.get(id(l), set())]
    if len(bottoms) == 1:
        return bottoms[0], []
    if not bottoms and len(cands) == 1 and "top" not in tags.get(id(cands[0]), set()):
        return cands[0], [
            "뷰 이름 표시가 없어 평면 뷰 1개를 Bottom 뷰로 사용 — 도면 확인 필요 [가정]"
        ]
    raise ValueError(
        f"Bottom 뷰를 정할 수 없음 (후보 {len(cands)}개, Bottom 표시 {len(bottoms)}개). "
        f"--view 번호로 지정: {_describe(cands)}"
    )


# --- 좌표 변환 --------------------------------------------------------------

def _to_jig(shape, bb, mirrored: bool):
    if mirrored:
        return shape.mirror("YZ").translate((bb.xmax, -bb.ymin, 0))
    return shape.translate((-bb.xmin, -bb.ymin, 0))


def _board_face(board: Loop, children: list[Loop], max_hole_d: float) -> cq.Face:
    inner = [l.wire for l in children if l.circle and l.circle[2] <= max_hole_d]
    return cq.Face.makeFromWires(board.wire, inner)


def _sym_diff(a: cq.Face, b: cq.Face) -> float:
    try:
        return a.cut(b).Area() + b.cut(a).Area()
    except Exception:
        return float("inf")


def _check_mirror(board, b_children, top, t_children, d2, notes) -> tuple[bool, bool]:
    """Bottom 뷰 좌우 반전 설정을 Top 뷰로 대조한다 (간섭 검증으로는 못 잡는 오류)."""
    mirrored = bool(d2["bottom_view_mirrored"])
    if top is None:
        notes.append(
            f"Bottom 뷰 좌우 반전 설정(bottom_view_mirrored={str(mirrored).lower()})을 "
            f"대조할 Top 뷰 없음 — 반전이 틀리면 포켓이 거울상으로 생기고 간섭 검증으로도 "
            f"드러나지 않는다. 도면 확인 필요 [가정]"
        )
        return mirrored, False

    max_d = d2["max_hole_d"]
    bf = _board_face(board, b_children, max_d)
    tf = _to_jig(_board_face(top, t_children, max_d), top.bbox, False)
    t_area = tf.Area()

    as_set = _sym_diff(_to_jig(bf, board.bbox, mirrored), tf)
    as_opp = _sym_diff(_to_jig(bf, board.bbox, not mirrored), tf)
    limit = d2["mirror_tol"] * t_area

    if as_set <= limit < as_opp:
        return mirrored, True
    if as_opp <= limit:
        raise ValueError(
            f"Bottom 뷰 좌우 반전 설정이 도면과 반대: bottom_view_mirrored="
            f"{str(mirrored).lower()} 로는 Top 뷰와 대칭차 {as_set:.2f}mm², "
            f"반대로 하면 {as_opp:.2f}mm². 설정을 {str(not mirrored).lower()} 로 바꿔야 한다"
        )
    notes.append(
        f"Top 뷰 대조가 애매함 (대칭차 설정대로 {as_set:.2f}mm², 반대 {as_opp:.2f}mm²) — "
        f"반전 설정을 확인 필요 [가정]"
    )
    return mirrored, False


# --- 원 분류 ----------------------------------------------------------------

def _split_circles(
    board, children, top, t_children, mirrored, top_ok, d2, notes
) -> tuple[list[Loop], list[Loop]]:
    """기판 안 원 → (홀, 원형 부품)."""
    circles = [l for l in children if l.circle]
    max_d, tol = d2["max_hole_d"], d2["join_tol"]

    if top_ok and top is not None:
        top_circles = []
        for l in t_children:
            if not l.circle:
                continue
            w = _to_jig(l.wire, top.bbox, False)
            c = w.Edges()[0].arcCenter()
            top_circles.append((c.x, c.y, l.circle[2]))
        holes, parts = [], []
        for l in circles:
            w = _to_jig(l.wire, board.bbox, mirrored)
            c = w.Edges()[0].arcCenter()
            hit = any(
                abs(c.x - tx) <= tol and abs(c.y - ty) <= tol and abs(l.circle[2] - td) <= tol
                for tx, ty, td in top_circles
            )
            (holes if hit else parts).append(l)
        return holes, parts

    holes = [l for l in circles if l.circle[2] <= max_d]
    parts = [l for l in circles if l.circle[2] > max_d]
    if holes:
        notes.append(
            f"기판 안 원 {len(holes)}개를 지름 {max_d}mm 이하라 홀로 처리 — "
            f"Top 뷰 대조 불가로 원형 부품과 구분 못 함. 원형 부품이면 회피 포켓이 빠진다 [가정]"
        )
    return holes, parts


# --- 본체 -------------------------------------------------------------------

def load_dxf(path, cfg: dict, view: int | None = None) -> PcbModel:
    if cfg["pcb"]["face_down"] != "bottom":
        raise ValueError(
            f'2D 도면 입력은 face_down="bottom" 만 지원 (받은 값: {cfg["pcb"]["face_down"]!r})'
        )
    d2 = cfg["drawing2d"]
    path = Path(path)
    notes: list[str] = []

    doc = ezdxf.readfile(str(path))
    groups, free, texts, skipped = _read(doc.modelspace())
    if skipped:
        kinds = ", ".join(f"{k} {v}개" for k, v in sorted(skipped.items()))
        notes.append(f"읽지 않은 개체 {{{kinds}}} — 이 형상은 분류에서 빠짐")

    loops: list[Loop] = []
    open_groups: list[list[cq.Edge]] = []
    for g in groups:
        loop = _make_loop(g)
        if loop is None:
            open_groups.append(g)
        else:
            loops.append(loop)

    chain_groups, chain_open = _chains(free, d2["join_tol"])
    open_groups.extend(chain_open)
    for g in chain_groups:
        loop = _make_loop(g)
        if loop is None:
            open_groups.append(g)
        else:
            loops.append(loop)

    loops = _strip_border(loops, open_groups)
    parent = _parents(loops)

    def children_of(l: Loop) -> list[Loop]:
        return [c for c in loops if parent[id(c)] is l]

    cands = []
    for l in loops:
        if parent[id(l)] is not None:
            continue
        kids = children_of(l)
        if not kids:
            continue
        bb = l.bbox
        if min(bb.xlen, bb.ylen) < d2["min_board_size"]:
            continue
        if sum(k.area for k in kids) >= d2["tile_ratio"] * l.area:
            continue  # 표제란 같은 칸 묶음
        cands.append(l)
    cands.sort(key=lambda l: (round(l.bbox.xmin, 3), round(l.bbox.ymin, 3)))

    tags = _tag_views(cands, texts, d2)
    board, pick_notes = _pick(cands, tags, view, path)
    notes.extend(pick_notes)

    tops = [l for l in cands if "top" in tags.get(id(l), set()) and l is not board]
    top = tops[0] if len(tops) == 1 else None

    b_children = children_of(board)
    t_children = children_of(top) if top is not None else []

    mirrored, top_ok = _check_mirror(board, b_children, top, t_children, d2, notes)

    hole_loops, round_parts = _split_circles(
        board, b_children, top, t_children, mirrored, top_ok, d2, notes
    )

    comps = [l for l in b_children if l not in hole_loops]

    # 기판 외곽에 걸친 부품 외곽
    crossing = []
    for l in loops:
        if l is board or l in b_children or parent[id(l)] is not None:
            continue
        if _bbox_holds(l.bbox, board.bbox):
            continue
        try:
            if l.face.intersect(board.face).Area() > TOL:
                crossing.append(l)
        except Exception:
            pass
    if crossing:
        notes.append(
            f"기판 외곽에 걸친 부품 외곽 {len(crossing)}개 — 기판 밖으로 나온 부품으로 보고 "
            f"회피 포켓을 만든다 [가정]"
        )
    comps += crossing

    # 지그 좌표로 변환
    bb = board.bbox
    outline = _to_jig(board.wire, bb, mirrored)
    holes = []
    for l in hole_loops:
        c = _to_jig(l.wire, bb, mirrored).Edges()[0].arcCenter()
        holes.append(Hole(c.x, c.y, l.circle[2]))

    h = d2["default_height"]
    t = d2["board_thickness"]
    protrusions, part_solids = [], []
    for l in comps:
        cb = _to_jig(l.wire, bb, mirrored).BoundingBox()
        protrusions.append(Protrusion(cb.xmin, cb.ymin, cb.xmax, cb.ymax, h))
        part_solids.append(
            cq.Solid.makeBox(
                cb.xlen, cb.ylen, h, pnt=cq.Vector(cb.xmin, cb.ymin, -h)
            )
        )

    inner = [_to_jig(l.wire, bb, mirrored) for l in hole_loops]
    board_solid = cq.Solid.extrudeLinear(
        outline, inner, cq.Vector(0, 0, t)
    )

    stray = [g for g in open_groups if _bbox_holds(bb, _group_bbox(g))]
    if stray:
        sample = ", ".join(
            f"({g[0].startPoint().x:.1f}, {g[0].startPoint().y:.1f})" for g in stray[:3]
        )
        notes.append(
            f"기판 영역에 닫히지 않은 선 {len(stray)}개 (예: {sample}) — "
            f"끊긴 부품 외곽이면 회피 포켓 누락. 도면 확인 필요"
        )

    notes.append(
        f"2D 도면 입력: 기판 두께 {t}mm, 부품 높이 {h}mm 일괄 적용 (도면에 없음) [가정]"
    )

    return PcbModel(
        shape=cq.Compound.makeCompound([board_solid] + part_solids),
        outline=outline,
        thickness=t,
        holes=holes,
        protrusions=protrusions,
        notes=notes,
    )
