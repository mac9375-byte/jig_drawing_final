"""규칙 — 함수 하나 = 규칙 하나. 결과마다 "왜 이 형상인가"(reason)를 남긴다.

R1 안착 포켓 / R2 회피 포켓 / R3 위치결정 핀 / R4 몸체 범위 / R5 포켓 병합
R6 PCB 받침 비율 검사 / R7 안착 벽 손실 검사 (R6·R7 은 형상이 아니라 검사)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cadquery as cq
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from .pcb import PcbModel

TOL = 1e-3


@dataclass
class Feature:
    kind: str
    rule_id: str
    reason: str
    params: dict = field(default_factory=dict)


def _rect_face(xmin: float, ymin: float, xmax: float, ymax: float) -> cq.Face:
    pts = [
        cq.Vector(xmin, ymin, 0),
        cq.Vector(xmax, ymin, 0),
        cq.Vector(xmax, ymax, 0),
        cq.Vector(xmin, ymax, 0),
        cq.Vector(xmin, ymin, 0),
    ]
    return cq.Face.makeFromWires(cq.Wire.makePolygon(pts))


def _area(shape) -> float:
    try:
        return shape.Area()
    except Exception:
        return 0.0


def _common_area(a, b) -> float:
    try:
        return _area(a.intersect(b))
    except Exception:
        return 0.0


def _common_length(wire: cq.Wire, face: cq.Face) -> float:
    try:
        res = wire.intersect(face)
    except Exception:
        return 0.0
    return sum(e.Length() for e in res.Edges())


def _rect(f: Feature) -> tuple[float, float, float, float]:
    p = f.params
    return p["xmin"], p["ymin"], p["xmax"], p["ymax"]


# --- R1 ---------------------------------------------------------------------

def seat_pocket(pcb: PcbModel, cfg: dict) -> Feature:
    j = cfg["jig"]
    c, d = j["outline_clearance"], j["pocket_depth"]
    return Feature(
        kind="seat_pocket",
        rule_id="R1",
        reason=(
            f"PCB 외곽을 {c}mm 간격으로 둘러싸는 깊이 {d}mm 안착 포켓 — "
            f"기판 외곽이 위치결정과 안착 기준면 역할"
        ),
        params={"clearance": c, "depth": d},
    )


# --- R2 ---------------------------------------------------------------------

def reliefs(pcb: PcbModel, cfg: dict) -> list[Feature]:
    j = cfg["jig"]
    c, pd, dc = j["comp_clearance"], j["pocket_depth"], j["depth_clearance"]
    out = []
    for p in pcb.protrusions:
        depth = pd + p.height + dc
        out.append(
            Feature(
                kind="relief",
                rule_id="R2",
                reason=(
                    f"기판 아래로 {p.height:.2f}mm 돌출된 부품 회피 — "
                    f"부품 bbox 에 여유 {c}mm, 깊이 = 안착 포켓 {pd} + 돌출 {p.height:.2f} + 여유 {dc}"
                ),
                params={
                    "xmin": p.xmin - c,
                    "ymin": p.ymin - c,
                    "xmax": p.xmax + c,
                    "ymax": p.ymax + c,
                    "depth": depth,
                },
            )
        )
    return out


# --- R5 ---------------------------------------------------------------------

def _separation(a: tuple, b: tuple) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    sep_x = max(ax0, bx0) - min(ax1, bx1)
    sep_y = max(ay0, by0) - min(ay1, by1)
    return max(sep_x, sep_y)


def _span(a0: float, a1: float, b0: float, b1: float) -> tuple[float, float]:
    """한 축의 브리지 구간 — 떨어져 있으면 틈 구간, 겹치면 겹친 구간(= 벽 자리)."""
    lo, hi = max(a0, b0), min(a1, b1)
    if lo > hi:
        return hi, lo  # 틈 구간
    return lo, hi      # 겹친 구간


def _bridge_rect(a: tuple, b: tuple) -> tuple | None:
    """두 사각형 사이 얇은 벽 자리 사각형. 대각선이면 모서리 기둥 자리."""
    x0, x1 = _span(a[0], a[2], b[0], b[2])
    y0, y1 = _span(a[1], a[3], b[1], b[3])
    if x1 - x0 <= TOL or y1 - y0 <= TOL:
        return None  # 꼭짓점만 스치는 경우 — 벽이라 할 구간이 없다
    return x0, y0, x1, y1


def merge_reliefs(rel: list[Feature], gap: float) -> list[Feature]:
    """얇은 벽 자리만 브리지로 잇는다 (바운딩박스 병합이 아니다).

    원래 포켓은 각자 깊이를 그대로 유지하고, 사이 틈이 0 < 틈 < gap 인 곳에만
    그 틈 자리 사각형을 더한다. 브리지 깊이 = 두 포켓 중 얕은 쪽 → 단차가 남는다.
    (얕은 쪽 바닥 아래는 그 포켓 밑 덩어리와 이어져 있어 얇은 벽이 아니다)
    """
    rects = [(_rect(f), f.params["depth"]) for f in rel]
    bridges: list[tuple[tuple, float]] = []
    seen = {tuple(round(v, 6) for v in r) for r, _d in rects}

    work = list(range(len(rects)))
    guard = 0
    while work and guard < 10000:
        i = work.pop(0)
        guard += 1
        ri, di = rects[i]
        for k in range(len(rects)):
            if k == i:
                continue
            rk, dk = rects[k]
            sep = _separation(ri, rk)
            if not 0 < sep < gap:
                continue
            box = _bridge_rect(ri, rk)
            if box is None:
                continue
            key = tuple(round(v, 6) for v in box)
            if key in seen:
                continue
            seen.add(key)
            depth = min(di, dk)
            rects.append((box, depth))
            bridges.append((box, depth))
            work.append(len(rects) - 1)

    out = list(rel)
    for (x0, y0, x1, y1), depth in bridges:
        out.append(
            Feature(
                kind="relief",
                rule_id="R5",
                reason=(
                    f"인접 회피 포켓 사이 얇은 벽({gap}mm 미만) 자리를 브리지로 제거 — "
                    f"깊이는 얕은 쪽 {depth:.2f}mm 로 두어 단차를 남긴다 "
                    f"(바닥 아래가 이어져 얇은 벽이 아님)"
                ),
                params={"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1, "depth": depth},
            )
        )
    return out


# --- R3 ---------------------------------------------------------------------

def pins(pcb: PcbModel, rel: list[Feature], cfg: dict) -> tuple[list[Feature], list[str]]:
    p_cfg, j = cfg["pins"], cfg["jig"]
    lo, hi = p_cfg["hole_min_d"], p_cfg["hole_max_d"]

    def covered(h) -> bool:
        for f in rel:
            x0, y0, x1, y1 = _rect(f)
            if x0 <= h.x <= x1 and y0 <= h.y <= y1:
                return True
        return False

    cand = [h for h in pcb.holes if lo <= h.d <= hi and not covered(h)]
    if len(cand) < 2:
        return [], [
            f"툴링 홀 후보 {len(cand)}개 (<2) — 위치결정 핀 생략, 외곽 포켓만으로 위치결정"
        ]

    best, best_dist = (cand[0], cand[1]), -1.0
    for i in range(len(cand)):
        for k in range(i + 1, len(cand)):
            d = ((cand[i].x - cand[k].x) ** 2 + (cand[i].y - cand[k].y) ** 2) ** 0.5
            if d > best_dist:
                best, best_dist = (cand[i], cand[k]), d

    out = []
    for h in sorted(best, key=lambda h: (h.x, h.y)):
        d = h.d - p_cfg["pin_clearance"]
        z_top = -j["pocket_depth"] + pcb.thickness + p_cfg["pin_protrusion"]
        out.append(
            Feature(
                kind="pin",
                rule_id="R3",
                reason=(
                    f"툴링 홀 Ø{h.d:.2f} 에 위치결정 핀 — 후보 홀 중 가장 먼 "
                    f"두 개({best_dist:.1f}mm)로 회전까지 고정. "
                    f"핀 Ø = 홀 Ø − {p_cfg['pin_clearance']}, "
                    f"기판 윗면 위로 {p_cfg['pin_protrusion']}mm"
                ),
                params={"x": h.x, "y": h.y, "d": d, "z_top": z_top},
            )
        )
    return out, []


# --- R4 ---------------------------------------------------------------------

def base(pcb: PcbModel, rel: list[Feature], cfg: dict) -> Feature:
    j = cfg["jig"]
    bb = pcb.outline.BoundingBox()
    xs = [bb.xmin, bb.xmax]
    ys = [bb.ymin, bb.ymax]
    for f in rel:
        x0, y0, x1, y1 = _rect(f)
        xs += [x0, x1]
        ys += [y0, y1]
    m = j["wall_margin"]
    depth = max([j["pocket_depth"]] + [f.params["depth"] for f in rel])
    zmin = -(depth + j["min_floor"])
    return Feature(
        kind="base",
        rule_id="R4",
        reason=(
            f"기판·회피 포켓 범위 바깥으로 살 {m}mm — 가장 깊은 포켓 {depth:.2f}mm 아래로 "
            f"최소 바닥 {j['min_floor']}mm 확보"
        ),
        params={
            "xmin": min(xs) - m,
            "ymin": min(ys) - m,
            "xmax": max(xs) + m,
            "ymax": max(ys) + m,
            "zmin": zmin,
        },
    )


# --- R6 ---------------------------------------------------------------------

def unsupported_regions(pcb: PcbModel, rel: list[Feature]) -> list[dict]:
    """받침 없는 **연결 영역** 목록 (면적 큰 순).

    포켓 하나하나가 아니라 이어진 영역으로 본다. 포켓마다 보면 ② 이후 가장 큰 포켓이
    개별 부품 포켓(수 mm)이라 받침이 어디서 끊겼는지 알 수 없다.
    꼭짓점만 스치는 포켓들은 OCC 가 따로 떼어 주므로 점 접촉은 이어진 것으로 보지 않는다.
    """
    if not rel:
        return []
    board = cq.Face.makeFromWires(pcb.outline)
    faces = [_rect_face(*_rect(f)) for f in rel]
    # 사각형들을 먼저 하나로 합친 뒤 기판과 교집합 — 브리지가 포켓과 맞닿아도 면적 중복 없음
    union = faces[0] if len(faces) == 1 else faces[0].fuse(*faces[1:]).clean()
    try:
        common = board.intersect(union).clean()
    except Exception:
        return []

    bb = pcb.outline.BoundingBox()
    out = []
    for f in common.Faces():
        fb = f.BoundingBox()
        out.append(
            {
                "face": f,
                "area": f.Area(),
                "xmin": fb.xmin,
                "ymin": fb.ymin,
                "xmax": fb.xmax,
                "ymax": fb.ymax,
                "span_ratio": max(
                    fb.xlen / bb.xlen if bb.xlen else 0.0,
                    fb.ylen / bb.ylen if bb.ylen else 0.0,
                ),
            }
        )
    out.sort(key=lambda r: -r["area"])
    return out


def support_bands(pcb: PcbModel, rel: list[Feature], grow: float = 0.0) -> list[dict]:
    """남은 받침띠 = 기판 면 − 회피 포켓 (연결된 면마다 하나, 면적 큰 순).

    grow > 0 이면 포켓을 그만큼 키우고 기판을 그만큼 줄여 띠를 **침식**한다.
    침식 후 조각 수가 1 이 아니면 그 띠에는 2·grow 보다 좁은 목이 있다는 뜻이다
    (받침띠의 폭을 직접 재는 대신 쓰는 방법 — 포켓이 전부 사각형이라 정확하다).
    """
    outline = pcb.outline
    if grow:
        try:
            wires = outline.offset2D(-grow, "arc")
        except Exception:
            return []
        if not wires:
            return []
        outline = max(wires, key=lambda w: w.Length())
    try:
        board = cq.Face.makeFromWires(outline)
    except Exception:
        return []

    rest = board
    if rel:
        faces = []
        for f in rel:
            x0, y0, x1, y1 = _rect(f)
            faces.append(_rect_face(x0 - grow, y0 - grow, x1 + grow, y1 + grow))
        union = faces[0] if len(faces) == 1 else faces[0].fuse(*faces[1:]).clean()
        try:
            rest = board.cut(union).clean()
        except Exception:
            return []

    out = []
    for f in rest.Faces():
        if f.Area() <= TOL:
            continue
        fb = f.BoundingBox()
        out.append(
            {
                "face": f,
                "area": f.Area(),
                "xmin": fb.xmin,
                "ymin": fb.ymin,
                "xmax": fb.xmax,
                "ymax": fb.ymax,
            }
        )
    out.sort(key=lambda b: -b["area"])
    return out


def _bbox_gap(a: dict, b: dict) -> float:
    """두 bbox 의 분리거리 — 실제 형상 거리의 하한 (먼저 걸러내는 용도)."""
    return _separation(
        (a["xmin"], a["ymin"], a["xmax"], a["ymax"]),
        (b["xmin"], b["ymin"], b["xmax"], b["ymax"]),
    )


def thin_ribs(regions: list[dict], min_band: float) -> list[dict]:
    """받침 없는 영역 사이에 남은 받침 리브가 min_band 보다 좁은 곳 (좁은 순).

    침식 검사(support_bands)는 받침띠가 **끊기는 목**만 잡는다. 영역 두 개 사이의 얇은 리브는
    띠를 돌아가는 길이 남아 있어 연결성이 안 깨지므로 그쪽으로는 안 걸린다 → 거리로 직접 본다.
    재는 대상은 **포켓 사각형이 아니라 연결 영역**이다 — ② 의 브리지가 이미 없앤 사각형 사이 틈을
    받침으로 세면 안 된다 (그 자리는 파여 있다).
    """
    out: list[dict] = []
    for i in range(len(regions)):
        for k in range(i + 1, len(regions)):
            a, b = regions[i], regions[k]
            if _bbox_gap(a, b) >= min_band:
                continue
            try:
                ext = BRepExtrema_DistShapeShape(a["face"].wrapped, b["face"].wrapped)
                width = ext.Value()
                p1, p2 = ext.PointOnShape1(1), ext.PointOnShape2(1)
            except Exception:
                continue
            if not 0 < width < min_band:
                continue
            out.append(
                {
                    "width": width,
                    "xmin": min(p1.X(), p2.X()),
                    "ymin": min(p1.Y(), p2.Y()),
                    "xmax": max(p1.X(), p2.X()),
                    "ymax": max(p1.Y(), p2.Y()),
                }
            )
    out.sort(key=lambda r: r["width"])
    return out


def _numbers(d: dict) -> dict:
    """report.json 에 들어갈 수 있게 형상 객체를 뺀 사본."""
    return {k: v for k, v in d.items() if k != "face"}


def _pieces_in(band: dict, pieces: list[dict]) -> int:
    n = 0
    for p in pieces:
        if not _bbox_holds(band, p):
            continue
        try:
            if band["face"].intersect(p["face"]).Area() >= p["area"] * (1 - 1e-6):
                n += 1
        except Exception:
            continue
    return n


def _bbox_holds(a: dict, b: dict, tol: float = 1e-6) -> bool:
    return (
        a["xmin"] - tol <= b["xmin"]
        and a["ymin"] - tol <= b["ymin"]
        and a["xmax"] + tol >= b["xmax"]
        and a["ymax"] + tol >= b["ymax"]
    )


def _where(r: dict) -> str:
    return (
        f"{r['xmax'] - r['xmin']:.1f}x{r['ymax'] - r['ymin']:.1f}mm "
        f"(x {r['xmin']:.1f}..{r['xmax']:.1f}, y {r['ymin']:.1f}..{r['ymax']:.1f})"
    )


def support(pcb: PcbModel, rel: list[Feature], cfg: dict) -> tuple[Feature, list[str]]:
    j = cfg["jig"]
    need = j["min_support_ratio"]
    span_limit = j["max_unsupported_span_ratio"]
    board_area = cq.Face.makeFromWires(pcb.outline).Area()

    regions = unsupported_regions(pcb, rel)
    hollow = sum(r["area"] for r in regions)
    ratio = 1.0 - hollow / board_area if board_area > 0 else 1.0
    worst = max(regions, key=lambda r: r["span_ratio"], default=None)

    # 남은 받침띠: 개수·면적과 "제작·받침이 불안정한 좁은 목"
    min_band = j["min_support_band"]
    bands = support_bands(pcb, rel)
    band_area = sum(b["area"] for b in bands)
    ribs = thin_ribs(regions, min_band)
    eroded = support_bands(pcb, rel, grow=min_band / 2) if bands else []
    thin = []
    if bands and not eroded:
        thin_checked = False
    else:
        thin_checked = True
        for b in bands:
            n = _pieces_in(b, eroded)
            if n != 1:
                thin.append((b, n))

    feat = Feature(
        kind="support_check",
        rule_id="R6",
        reason=(
            f"기판 면적 {board_area:.1f}mm² 중 회피 포켓 위 {hollow:.1f}mm² 는 받침 없음 "
            f"→ 받침 {ratio:.0%} (기준 {need:.0%} 이상). 받침 없는 연결 영역 {len(regions)}곳, "
            f"가장 긴 곳은 기판의 {worst['span_ratio']:.0%} 를 가로지름 (기준 {span_limit:.0%} 이하). "
            f"남은 받침띠 {len(bands)}곳 {band_area:.1f}mm² (기준 폭 {min_band}mm): "
            f"좁은 목이 있는 띠 {len(thin)}곳, 좁은 받침 리브 {len(ribs)}곳"
            if regions
            else f"회피 포켓 없음 → 기판 면적 {board_area:.1f}mm² 전체가 받침 (받침 100%)"
        ),
        params={
            "board_area": board_area,
            "unsupported_area": hollow,
            "ratio": ratio,
            "min_ratio": need,
            "regions": len(regions),
            "max_region_area": regions[0]["area"] if regions else 0.0,
            "max_region": _numbers(regions[0]) if regions else None,
            "max_span_ratio": worst["span_ratio"] if worst else 0.0,
            "max_span_region": _numbers(worst) if worst else None,
            "max_span_limit": span_limit,
            "bands": len(bands),
            "band_area": band_area,
            "band_regions": [_numbers(b) for b in bands],
            "min_band": min_band,
            "thin_bands": len(thin),
            "thin_band_regions": [dict(_numbers(b), pieces=n) for b, n in thin],
            "thin_ribs": len(ribs),
            "thin_rib_regions": ribs,
        },
    )

    warns = []
    if ratio < need and regions:
        big = regions[0]
        warns.append(
            f"PCB 받침 {ratio:.0%} < 기준 {need:.0%} — 받침 없는 연결 영역 {len(regions)}곳 중 "
            f"가장 큰 곳 {big['area']:.1f}mm² {_where(big)}. "
            f"SMT 인쇄·실장 압력에 PCB 처짐 위험 → 부품 배치·포켓 병합 확인 필요"
        )
    if worst and worst["span_ratio"] > span_limit:
        warns.append(
            f"받침 없는 구간이 기판의 {worst['span_ratio']:.0%} 를 가로지름 "
            f"(기준 {span_limit:.0%} 이하) — {_where(worst)}. "
            f"그 구간은 양옆 받침이 없어 비율이 기준을 넘더라도 PCB 가 처질 수 있다"
        )
    if not thin_checked:
        warns.append(
            "받침띠 폭 검사를 하지 못함 (외곽 오프셋 실패) — 띠가 제작 가능한 폭인지 확인 필요"
        )
    for b, n in thin:
        if n == 0:
            why = f"띠 전체가 {min_band}mm 보다 좁다 — PCB 가 실제로 얹히지 못한다"
        else:
            why = (
                f"{min_band}mm 보다 좁은 목이 있어 {n}조각으로 끊긴다 — "
                f"그 목은 제작도 받침도 불안정하다"
            )
        warns.append(f"남은 받침띠 {b['area']:.1f}mm² {_where(b)}: {why}")
    if ribs:
        r = ribs[0]
        warns.append(
            f"포켓 사이 받침 리브 {len(ribs)}곳이 기준 {min_band}mm 보다 좁음 — "
            f"가장 좁은 곳 {r['width']:.2f}mm {_where(r)}. "
            f"제작은 되지만 받침으로는 좁다 → 부품 배치·포켓 병합 확인 필요"
        )
    return feat, warns


# --- R7 ---------------------------------------------------------------------

def wall(pcb: PcbModel, rel: list[Feature], cfg: dict) -> tuple[Feature, list[str]]:
    j = cfg["jig"]
    c = j["comp_clearance"]
    t = 1e-3
    seat_wires = pcb.outline.offset2D(j["outline_clearance"], "arc")
    seat = max(seat_wires, key=lambda w: w.Length())
    perimeter = seat.Length()

    lost = lost_part = lost_clear = 0.0
    warns = []
    for f in rel:
        x0, y0, x1, y1 = _rect(f)
        cut = _common_length(seat, _rect_face(x0 + t, y0 + t, x1 - t, y1 - t))
        if cut <= 0:
            continue
        lost += cut
        part = _common_length(seat, _rect_face(x0 + c + t, y0 + c + t, x1 - c - t, y1 - c - t)) > 0
        if part:
            lost_part += cut
            why = "부품이 기판 외곽 밖으로 나와 불가피. 그 구간 위치결정 확인 필요"
        else:
            lost_clear += cut
            why = f"부품은 기판 안쪽이나 여유 {c}mm 가 벽을 넘음(둥근 모서리 등)"
        warns.append(
            f"안착 포켓 벽 {cut:.2f}mm 없음 — {f.rule_id} 회피 포켓 "
            f"(x {x0:.2f}..{x1:.2f}, y {y0:.2f}..{y1:.2f}): {why}"
        )

    feat = Feature(
        kind="wall_check",
        rule_id="R7",
        reason=(
            f"안착 포켓 벽선 길이 {perimeter:.1f}mm 중 {lost:.2f}mm 가 회피 포켓에 잘림 "
            f"(부품 탓 {lost_part:.2f}mm, 여유 탓 {lost_clear:.2f}mm)"
        ),
        params={
            "perimeter": perimeter,
            "lost": lost,
            "lost_by_part": lost_part,
            "lost_by_clearance": lost_clear,
        },
    )
    return feat, warns


# --- 전체 ------------------------------------------------------------------

def apply_rules(pcb: PcbModel, cfg: dict) -> tuple[list[Feature], list[str]]:
    warnings = list(pcb.notes)
    if not pcb.protrusions:
        warnings.append(
            "기판 아래 돌출 부품 0건 — 회피 포켓 없음. 안착 방향(face_down) 확인 필요"
        )

    seat = seat_pocket(pcb, cfg)
    rel = merge_reliefs(reliefs(pcb, cfg), cfg["jig"]["merge_gap"])
    pin_feats, pin_warns = pins(pcb, rel, cfg)
    sup_feat, sup_warns = support(pcb, rel, cfg)
    wall_feat, wall_warns = wall(pcb, rel, cfg)
    base_feat = base(pcb, rel, cfg)

    feats = [base_feat, seat, *rel, *pin_feats, sup_feat, wall_feat]
    return feats, warnings + pin_warns + sup_warns + wall_warns
