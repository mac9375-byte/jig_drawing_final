"""AutoCAD 용 DXF 도면 — 평면도·치수·단면 A-A·깊이표·주기·표제란.

주의: ezdxf setup=True 의 EZDXF 치수 스타일은 dimlfac=100 이다.
mm 도면은 반드시 dimlfac=1 로 고친다 (안 그러면 치수가 100배로 찍힌다).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import cadquery as cq
import ezdxf
from cadquery.occ_impl.exporters.dxf import DxfDocument
from OCP.BRepTools import BRepTools_WireExplorer

from .pcb import PcbModel
from .rules import Feature, _rect_face, support_bands

LAYERS = {
    "OUTLINE": 7,
    "SEAT": 3,
    "RELIEF": 1,
    "PIN": 5,
    "SECTION": 7,
    "CENTER": 2,
    "DIM": 4,
    "TEXT": 7,
    "TITLE": 7,
    "SUPPORT": 6,  # 받침 없는 영역 경고 표시 (형상이 아니라 주의 표시 → 파선)
    "BAND": 8,     # 남은 받침띠 (PCB 가 실제로 얹히는 면) — 해치
}

DASHED = {"SUPPORT"}

KST = timezone(timedelta(hours=9))
TOL = 1e-6


def _of_kind(feats: list[Feature], kind: str) -> list[Feature]:
    return [f for f in feats if f.kind == kind]


def _rect(f: Feature):
    p = f.params
    return p["xmin"], p["ymin"], p["xmax"], p["ymax"]


def _fuse(faces: list[cq.Face]) -> cq.Shape:
    if len(faces) == 1:
        return faces[0]
    try:
        return faces[0].fuse(*faces[1:]).clean()
    except Exception:
        out = faces[0]
        for f in faces[1:]:
            out = out.fuse(f)
        return out.clean()


def depth_regions(rel: list[Feature]) -> list[tuple[float, cq.Face, list[Feature]]]:
    """깊이가 같은 회피 포켓·브리지를 합쳐 연결된 영역 하나로 묶는다.

    포켓마다 사각형을 따로 그리면 브리지가 원래 포켓과 변을 공유해 선이 겹치고
    깊이 태그가 사각형 수만큼 찍혀 읽을 수 없게 된다 (② 이후 휴대폰 샘플 80개).
    """
    out = []
    for dep in sorted({round(f.params["depth"], 6) for f in rel}):
        group = [f for f in rel if round(f.params["depth"], 6) == dep]
        union = _fuse([_rect_face(*_rect(f)) for f in group])
        for face in union.Faces():
            bb = face.BoundingBox()
            inside = [
                f
                for f in group
                if bb.xmin - TOL <= (_rect(f)[0] + _rect(f)[2]) / 2 <= bb.xmax + TOL
                and bb.ymin - TOL <= (_rect(f)[1] + _rect(f)[3]) / 2 <= bb.ymax + TOL
            ]
            out.append((dep, face, inside or group))
    return out


def support_flags(feats: list[Feature]) -> list[tuple[dict, list[str], bool]]:
    """R6 결과 중 **기준을 넘긴 받침 없는 영역**만 (영역, 라벨 줄들).

    정상인 지그에는 아무것도 그리지 않는다. 받침 없는 영역의 외곽선은 RELIEF 영역선과
    같은 선이라 다시 그리면 도면만 복잡해진다 → 경고 난 곳만 bbox 로 표시한다
    (횡단 검사도 bbox 로 재므로 표시와 판정 기준이 같다).
    """
    got = _of_kind(feats, "support_check")
    if not got:
        return []
    p = got[0].params
    out: list[tuple[dict, list[str], bool]] = []

    def key(r):
        return tuple(round(r[k], 4) for k in ("xmin", "ymin", "xmax", "ymax"))

    def add(region, label, box=True):
        if not region:
            return
        for r, labels, _box in out:
            if key(r) == key(region):
                labels.append(label)
                return
        out.append((region, [label], box))

    if p["ratio"] < p["min_ratio"]:
        r = p["max_region"]
        if r:
            add(
                r,
                f"받침없음 {r['area']:.0f}mm² — 받침 {p['ratio']:.0%} "
                f"< 기준 {p['min_ratio']:.0%}",
            )
    if p["max_span_ratio"] > p["max_span_limit"]:
        r = p["max_span_region"]
        if r:
            add(
                r,
                f"받침없음 — 기판 {r['span_ratio']:.0%} 횡단 "
                f"(기준 {p['max_span_limit']:.0%} 이하)",
            )
    for r in p.get("thin_band_regions", []):
        if r["pieces"] == 0:
            why = f"받침띠 전체가 {p['min_band']}mm 보다 좁음"
        else:
            why = f"받침띠에 {p['min_band']}mm 보다 좁은 목 ({r['pieces']}조각으로 끊김)"
        # 받침띠는 보통 기판 거의 전체라 bbox 사각형이 무의미하다 → 라벨만
        add(r, why, box=False)
    ribs = p.get("thin_rib_regions", [])
    if ribs:
        # 리브는 여러 곳일 수 있어 가장 좁은 한 곳만 표시한다 (개수는 라벨에)
        r = ribs[0]
        extra = f" 외 {len(ribs) - 1}곳" if len(ribs) > 1 else ""
        add(r, f"받침 리브 {r['width']:.2f}mm < 기준 {p['min_band']}mm{extra}")
    return out


def _wire_points(wire: cq.Wire) -> list[tuple[float, float]]:
    exp = BRepTools_WireExplorer(wire.wrapped)
    pts = []
    while exp.More():
        v = cq.Vertex(exp.CurrentVertex())
        pts.append((v.X, v.Y))
        exp.Next()
    return pts


def write_drawing(
    jig: cq.Shape,
    pcb: PcbModel,
    feats: list[Feature],
    cfg: dict,
    path: str | Path,
    source: str = "",
) -> str:
    dcfg = cfg["drawing"]
    h = dcfg["text_height"]

    doc = DxfDocument(setup=True)
    d = doc.document
    msp = doc.msp

    for name, color in LAYERS.items():
        if name == "CENTER":
            lt = "CENTER"
        elif name in DASHED:
            lt = "DASHED"
        else:
            lt = "CONTINUOUS"
        doc.add_layer(name, color=color, linetype=lt)

    d.styles.add("KOR", font="malgun.ttf")

    ds = d.dimstyles.get("EZDXF")
    ds.dxf.dimtxt = h
    ds.dxf.dimasz = 0.8 * h
    ds.dxf.dimdec = 2
    ds.dxf.dimlfac = 1.0  # EZDXF 기본 100 → mm 도면은 1 이어야 한다
    ds.dxf.dimtxsty = "KOR"

    def text(s, x, y, size=None, layer="TEXT"):
        msp.add_text(
            s,
            dxfattribs={"layer": layer, "style": "KOR", "height": size or h},
        ).set_placement((x, y))

    base = _of_kind(feats, "base")[0].params
    bx0, by0, bx1, by1 = base["xmin"], base["ymin"], base["xmax"], base["ymax"]
    zmin = base["zmin"]
    seat = _of_kind(feats, "seat_pocket")[0].params
    rel = _of_kind(feats, "relief")
    pin_feats = _of_kind(feats, "pin")

    # --- 평면도 -------------------------------------------------------------
    msp.add_lwpolyline(
        [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )

    seat_wire = max(pcb.outline.offset2D(seat["clearance"], "arc"), key=lambda w: w.Length())
    doc.add_shape(cq.Workplane("XY").add(seat_wire), "SEAT")

    depths = sorted({round(f.params["depth"], 6) for f in rel})
    tag_of = {dep: f"D{i + 1}" for i, dep in enumerate(depths)}

    # 깊이별 영역 하나당 외곽선 1개 + 깊이 태그 1개 (사각형마다 그리지 않는다)
    regions = depth_regions(rel)
    drawn: set = set()
    for dep, face, group in regions:
        for wire in [face.outerWire(), *face.innerWires()]:
            pts = _wire_points(wire)
            # 깊은 영역의 외곽선은 얕은 영역의 내부 루프와 같은 선이다 → 한 번만 그린다
            key = tuple(sorted((round(x, 4), round(y, 4)) for x, y in pts))
            if key in drawn:
                continue
            drawn.add(key)
            if len(pts) >= 3:
                msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "RELIEF"})
            else:
                doc.add_shape(cq.Workplane("XY").add(wire), "RELIEF")

        bb = face.BoundingBox()
        # 글자 크기는 영역 크기로 정한다 (좁은 브리지 하나 때문에 0.2mm 로 줄지 않게)
        size = max(0.3 * h, min(h, 0.6 * min(bb.xlen, bb.ylen)))
        big = max(group, key=lambda f: (_rect(f)[2] - _rect(f)[0]) * (_rect(f)[3] - _rect(f)[1]))
        x0, y0, x1, y1 = _rect(big)
        text(
            tag_of[dep],
            (x0 + x1) / 2 - size,
            (y0 + y1) / 2 - size / 2,
            size=size,
            layer="RELIEF",
        )

    for f in pin_feats:
        p = f.params
        msp.add_circle((p["x"], p["y"]), p["d"] / 2, dxfattribs={"layer": "PIN"})
        text(f"Ø{p['d']:.2f} PIN", p["x"] + p["d"], p["y"] + p["d"] / 2, layer="PIN")

    # --- 남은 받침띠 (PCB 가 실제로 얹히는 면) -----------------------------
    bands = support_bands(pcb, rel)
    for band in bands:
        hatch = msp.add_hatch(dxfattribs={"layer": "BAND"})
        hatch.set_pattern_fill("ANSI31", scale=2.0)
        hatch.dxf.color = ezdxf.colors.BYLAYER
        face = band["face"]
        # 해치 경계는 호를 현으로 근사한다 (외곽 R 모서리 — 1:1 에서 보이지 않는다)
        for wire, flag in [(face.outerWire(), ezdxf.const.BOUNDARY_PATH_EXTERNAL)] + [
            (w, ezdxf.const.BOUNDARY_PATH_DEFAULT) for w in face.innerWires()
        ]:
            pts = _wire_points(wire)
            if len(pts) >= 3:
                hatch.paths.add_polyline_path(pts, is_closed=True, flags=flag)

    # --- 받침 없는 영역 경고 표시 (경고 난 곳만) ---------------------------
    flags = support_flags(feats)
    for region, labels, box in flags:
        x0, y0 = region["xmin"], region["ymin"]
        x1, y1 = region["xmax"], region["ymax"]
        if box:
            msp.add_lwpolyline(
                [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                close=True,
                dxfattribs={"layer": "SUPPORT"},
            )
        for k, label in enumerate(labels):
            text(label, x0, y1 + (0.6 + 1.4 * k) * h, layer="SUPPORT")

    # --- 치수 ---------------------------------------------------------------
    def lin(p1, p2, base_pt, angle=0):
        msp.add_linear_dim(
            base=base_pt,
            p1=p1,
            p2=p2,
            angle=angle,
            dimstyle="EZDXF",
            dxfattribs={"layer": "DIM"},
        ).render()

    for i, f in enumerate(pin_feats):
        p = f.params
        lin((bx0, by1), (p["x"], by1), (0, by1 + (3 + 3 * i) * h), angle=0)
        lin((bx1, by0), (bx1, p["y"]), (bx1 + (3 + 3 * i) * h, 0), angle=90)

    lin((bx0, by0), (bx1, by0), (0, by0 - 3 * h), angle=0)
    lin((bx0, by0), (bx0, by1), (bx0 - 3 * h, 0), angle=90)

    if len(pin_feats) == 2:
        a, b = pin_feats[0].params, pin_feats[1].params
        msp.add_aligned_dim(
            p1=(a["x"], a["y"]),
            p2=(b["x"], b["y"]),
            distance=3 * h,
            dimstyle="EZDXF",
            dxfattribs={"layer": "DIM"},
        ).render()

    # --- 단면 A-A -----------------------------------------------------------
    deep = max(rel, key=lambda f: f.params["depth"]) if rel else None
    yc = (deep.params["ymin"] + deep.params["ymax"]) / 2 if deep else (by0 + by1) / 2
    sec0 = by0 - 8 * h

    plane = cq.Face.makePlane(None, None, basePnt=cq.Vector(0, yc, 0), dir=cq.Vector(0, 1, 0))
    section = jig.intersect(plane).rotate((0, 0, 0), (1, 0, 0), -90).translate((0, sec0, yc))
    doc.add_shape(cq.Workplane("XY").add(section), "SECTION")

    msp.add_line(
        (bx0 - 4 * h, yc), (bx1 + 4 * h, yc), dxfattribs={"layer": "CENTER"}
    )
    text("A", bx0 - 5 * h, yc, layer="CENTER")
    text("A", bx1 + 4.5 * h, yc, layer="CENTER")
    text("단면 A-A", (bx0 + bx1) / 2 - 3 * h, sec0 + zmin - 3 * h)

    lin((bx0, sec0 + zmin), (bx0, sec0), (bx0 - 3 * h, 0), angle=90)
    if deep:
        dx = (deep.params["xmin"] + deep.params["xmax"]) / 2
        dep = deep.params["depth"]
        lin((dx, sec0 - dep), (dx, sec0), (dx, 0), angle=90)

    # --- 깊이표 + 주기 ------------------------------------------------------
    tx = bx1 + 12 * h
    ty = by1
    line_h = 2 * h

    def row(s, i, size=None):
        text(s, tx, ty - i * line_h, size=size)

    counts = {}
    for dep, _face, _group in regions:
        counts[dep] = counts.get(dep, 0) + 1

    i = 0
    row("깊이표 (지그 윗면 기준)", i)
    i += 1
    row(f"안착 포켓   {seat['depth']:.2f}", i)
    i += 1
    for dep in depths:
        row(f"{tag_of[dep]}   {dep:.2f}   ({counts[dep]}곳)", i)
        i += 1

    sup = _of_kind(feats, "support_check")
    if sup and bands:
        p = sup[0].params
        row(
            f"받침띠   {len(bands)}곳   {p['band_area']:.0f}mm²   (받침 {p['ratio']:.0%})",
            i,
        )
        i += 1

    i += 1
    j = cfg["jig"]
    notes = [
        "1. 단위 mm, 척도 1:1",
        f"2. {dcfg['tolerance_note']}",
        f"3. 클리어런스: 외곽 {j['outline_clearance']}mm, 부품 {j['comp_clearance']}mm, "
        f"깊이 {j['depth_clearance']}mm",
        f"4. 위치결정 핀 {len(pin_feats)}개 — 핀 Ø = 툴링 홀 Ø − "
        f"{cfg['pins']['pin_clearance']}mm",
        f"5. 전체 높이 {-zmin:.2f}mm, 최소 바닥 {j['min_floor']}mm",
        "6. jigauto 자동 생성 도면 — 출도 전 검토 필수",
    ]
    if flags:
        notes.append(
            "7. BAND 레이어 해치 = 남은 받침띠, SUPPORT 레이어(파선) = 받침 경고 위치 — 부품 배치·포켓 병합 확인 필요"
        )
    for n in notes:
        row(n, i)
        i += 1

    # --- 표제란 -------------------------------------------------------------
    rows = [
        ("회사", dcfg["company"]),
        ("품명", dcfg["part_name"]),
        ("도번", dcfg["drawing_no"]),
        ("모델", dcfg["model"]),
        ("재질", dcfg["material"]),
        ("제작", dcfg["process"]),
        ("척도", "1:1"),
        ("단위", "mm"),
        ("작성일", f"{datetime.now(KST).strftime('%Y-%m-%d')} (KST)"),
        ("원본", Path(source).name if source else "미지정"),
    ]
    w1, w2, rh = 14 * h, 26 * h, 2 * h
    tbx1 = bx1
    tbx0 = tbx1 - (w1 + w2)
    tby1 = sec0 + zmin - 6 * h
    tby0 = tby1 - rh * len(rows)

    for k in range(len(rows) + 1):
        y = tby1 - k * rh
        msp.add_line((tbx0, y), (tbx1, y), dxfattribs={"layer": "TITLE"})
    for x in (tbx0, tbx0 + w1, tbx1):
        msp.add_line((x, tby0), (x, tby1), dxfattribs={"layer": "TITLE"})
    for k, (label, value) in enumerate(rows):
        y = tby1 - (k + 1) * rh + 0.5 * h
        text(label, tbx0 + 0.5 * h, y, layer="TITLE")
        text(str(value), tbx0 + w1 + 0.5 * h, y, layer="TITLE")

    path = Path(path)
    d.saveas(str(path))
    return str(path)
