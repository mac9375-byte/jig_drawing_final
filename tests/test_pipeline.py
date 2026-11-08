"""전체 파이프라인 검증 (합성 PCB · 가상 휴대폰 샘플 · 가상 ECAD 샘플 · 고객 2D 도면)."""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path

import cadquery as cq
import ezdxf
import pytest
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_State

from conftest import ROOT, make_pcb

sys.path.insert(0, str(ROOT / "samples"))

from jigauto.drawing import depth_regions, write_drawing  # noqa: E402
from jigauto.dxf_input import load_dxf  # noqa: E402
from jigauto.jig import build_jig, interference, seated_pcb  # noqa: E402
from jigauto.pcb import PcbModel, Protrusion, load_pcb  # noqa: E402
from jigauto.rules import Feature, apply_rules, merge_reliefs, support, wall  # noqa: E402

CAMERA_POCKET = (7.7, 37.7, 20.3, 48.3)  # 2D 입력에서만 생기는 개구부 포켓 (무해)


# --- 공용 도우미 ------------------------------------------------------------

def _load(step, cfg) -> PcbModel:
    return load_pcb(
        step,
        face_down=cfg["pcb"]["face_down"],
        board_max_thickness=cfg["pcb"]["board_max_thickness"],
    )


def _kind(feats, kind):
    return [f for f in feats if f.kind == kind]


def _relief_boxes(feats):
    return {
        tuple(round(f.params[k], 2) for k in ("xmin", "ymin", "xmax", "ymax"))
        for f in _kind(feats, "relief")
    }


def _pin_xy(feats):
    return sorted((round(f.params["x"], 2), round(f.params["y"], 2)) for f in _kind(feats, "pin"))


def _holes(pcb):
    return sorted((round(h.x, 2), round(h.y, 2), round(h.d, 2)) for h in pcb.holes)


def _inside(solid, point) -> bool:
    cl = BRepClass3d_SolidClassifier(solid.wrapped, gp_Pnt(*point), 1e-7)
    return cl.State() == TopAbs_State.TopAbs_IN


def _relief(xmin, ymin, xmax, ymax, depth, rule_id="R2"):
    return Feature(
        kind="relief",
        rule_id=rule_id,
        reason="테스트용",
        params={"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax, "depth": depth},
    )


def _board_model(cfg) -> PcbModel:
    """합성 기판(60x40, 모서리 R3) 외곽만 쓰는 PcbModel."""
    solid = (
        cq.Workplane("XY")
        .moveTo(30, 20)
        .rect(60, 40)
        .extrude(1.0)
        .edges("|Z")
        .fillet(3.0)
        .val()
    )
    face = max(
        (
            f
            for f in solid.Faces()
            if f.geomType() == "PLANE" and f.normalAt().z < -0.9 and abs(f.Center().z) < 1e-6
        ),
        key=lambda f: f.Area(),
    )
    return PcbModel(
        shape=solid,
        outline=face.outerWire(),
        thickness=1.0,
        holes=[],
        protrusions=[],
        notes=[],
    )


FILLET_CORNER = 9.0 - 9.0 * math.pi / 4  # R3 모서리 하나가 깎아내는 면적


# --- 세션 fixture ----------------------------------------------------------

@pytest.fixture(scope="session")
def phone_step(tmp_path_factory):
    from make_phone_pcb import make_phone_pcb

    return make_phone_pcb(tmp_path_factory.mktemp("phone") / "phone_main_pcb.step")


@pytest.fixture(scope="session")
def ecad_step(tmp_path_factory):
    from make_ecad_like_pcb import make_ecad_like_pcb

    return make_ecad_like_pcb(tmp_path_factory.mktemp("ecad") / "ecad_like_pcb.step")


@pytest.fixture(scope="session")
def dxf_files(tmp_path_factory):
    from make_phone_dxf import make_phone_dxf

    out = tmp_path_factory.mktemp("dxf")
    paths = {}
    for kind in ("clean", "messy", "layer0", "unlabeled"):
        paths[kind] = make_phone_dxf(out / f"phone_main_{kind}.dxf", kind=kind)
    paths["broken"] = make_phone_dxf(out / "broken.dxf", kind="clean", broken=True)
    paths["round_top"] = make_phone_dxf(out / "round_top.dxf", kind="messy", round_part=True)
    paths["round_notop"] = make_phone_dxf(out / "round_notop.dxf", kind="clean", round_part=True)
    return paths


@pytest.fixture(scope="session")
def phone_result(phone_step, spec_cfg):
    pcb = _load(phone_step, spec_cfg)
    feats, warns = apply_rules(pcb, spec_cfg)
    return pcb, feats, warns


# --- 1. STEP 파싱 -----------------------------------------------------------

def test_parse(sample_step, cfg):
    pcb = _load(sample_step, cfg)
    assert pcb.thickness == pytest.approx(1.0)
    assert sorted(round(h.d, 2) for h in pcb.holes) == [0.5, 3.0, 3.0, 3.0]
    assert sorted(round(p.height, 2) for p in pcb.protrusions) == [1.5, 2.0, 3.0]


# --- 2. 규칙 ---------------------------------------------------------------

def test_rules(sample_step, cfg):
    pcb = _load(sample_step, cfg)
    feats, warns = apply_rules(pcb, cfg)

    j = cfg["jig"]
    want = sorted(j["pocket_depth"] + h + j["depth_clearance"] for h in (1.5, 2.0, 3.0))
    assert sorted(round(f.params["depth"], 3) for f in _kind(feats, "relief")) == pytest.approx(want)
    assert _pin_xy(feats) == [(5.0, 5.0), (55.0, 35.0)]
    assert all(f.reason and f.rule_id for f in feats)
    assert warns == []


# --- 3. R5 병합 -------------------------------------------------------------

def test_merge_reliefs():
    rel = [
        _relief(0.0, 0.0, 1.0, 1.0, 1.0),
        _relief(1.5, 0.0, 2.5, 1.0, 2.0),
        _relief(3.0, 0.0, 4.0, 1.0, 1.5),
        _relief(5.5, 0.0, 6.0, 1.0, 0.8),
    ]
    got = merge_reliefs(rel, 1.0)

    # 원래 포켓은 그대로 (깊이 유지), 얇은 벽 자리에만 브리지가 더해진다
    kept = [f for f in got if f.rule_id == "R2"]
    assert [_relief_boxes([f]).pop() for f in kept] == [
        (0.0, 0.0, 1.0, 1.0),
        (1.5, 0.0, 2.5, 1.0),
        (3.0, 0.0, 4.0, 1.0),
        (5.5, 0.0, 6.0, 1.0),
    ]
    assert [f.params["depth"] for f in kept] == [1.0, 2.0, 1.5, 0.8]
    assert not any("merged" in f.params for f in got)

    bridges = [f for f in got if f.rule_id == "R5"]
    assert _relief_boxes(bridges) == {(1.0, 0.0, 1.5, 1.0), (2.5, 0.0, 3.0, 1.0)}
    # 브리지 깊이 = 두 포켓 중 얕은 쪽 → 단차가 남는다
    by_box = {_relief_boxes([f]).pop(): f.params["depth"] for f in bridges}
    assert by_box[(1.0, 0.0, 1.5, 1.0)] == pytest.approx(1.0)
    assert by_box[(2.5, 0.0, 3.0, 1.0)] == pytest.approx(1.5)

    # 벽이 정확히 merge_gap 이면 브리지를 만들지 않는다
    exact = merge_reliefs([_relief(0, 0, 1, 1, 1.0), _relief(2.0, 0, 3.0, 1, 1.0)], 1.0)
    assert len(exact) == 2

    # 대각선으로 인접하면 모서리 기둥 자리만 브리지
    diag = merge_reliefs([_relief(0, 0, 1, 1, 2.0), _relief(1.5, 1.5, 2.5, 2.5, 1.0)], 1.0)
    corner = [f for f in diag if f.rule_id == "R5"]
    assert len(corner) == 1
    assert _relief_boxes(corner) == {(1.0, 1.0, 1.5, 1.5)}
    assert corner[0].params["depth"] == pytest.approx(1.0)


# --- 4. R6 받침 비율 --------------------------------------------------------

def test_support_check(cfg):
    pcb = _board_model(cfg)
    board_area = 2400 - 4 * FILLET_CORNER

    feat, warns = support(pcb, [], cfg)
    assert feat.params["ratio"] == pytest.approx(1.0)
    assert feat.params["board_area"] == pytest.approx(board_area, abs=1e-3)
    assert feat.params["regions"] == 0
    assert warns == []

    # 기판 밖으로 나간 포켓은 기판 안 부분만 센다 (StickHub 형태).
    # 경고는 포켓이 아니라 받침 없는 **연결 영역**(기판 안 60x30)을 가리켜야 한다
    feat, warns = support(pcb, [_relief(-5, 5, 65, 35, 2.0)], cfg)
    assert feat.params["unsupported_area"] == pytest.approx(1800.0, abs=1e-3)
    assert feat.params["regions"] == 1
    assert feat.params["max_region_area"] == pytest.approx(1800.0, abs=1e-3)
    assert feat.params["max_span_ratio"] == pytest.approx(1.0)
    assert len(warns) == 2
    assert "PCB 받침 25%" in warns[0]
    assert "60.0x30.0" in warns[0]
    assert "가로지름" in warns[1]

    # 꼭짓점만 스치는 포켓 2개 = 이어진 것으로 보지 않는다 (영역 2곳, 횡단 50% → 구간 경고 없음)
    feat, warns = support(
        pcb, [_relief(0, 0, 30, 20, 2.0), _relief(30, 20, 60, 40, 2.0)], cfg
    )
    assert feat.params["unsupported_area"] == pytest.approx(1200 - 2 * FILLET_CORNER, abs=1e-3)
    assert feat.params["regions"] == 2
    assert feat.params["max_span_ratio"] == pytest.approx(0.5)
    assert len(warns) == 1
    assert "연결 영역 2곳" in warns[0]

    # 비율은 기준을 넘지만 받침 없는 구간이 기판을 가로지르는 경우 → 구간 경고만
    feat, warns = support(pcb, [_relief(0, 18, 60, 22, 2.0)], cfg)
    assert feat.params["ratio"] > cfg["jig"]["min_support_ratio"]
    assert feat.params["max_span_ratio"] == pytest.approx(1.0)
    assert len(warns) == 1
    assert "가로지름" in warns[0]
    # 기판을 완전히 가로지르면 받침띠가 둘로 나뉜다 (목이 아니라 분리 → 좁은 목 경고는 없다)
    assert feat.params["bands"] == 2
    assert feat.params["thin_bands"] == 0


def test_support_band_pinch(cfg):
    """받침띠에 min_support_band 보다 좁은 목이 있으면 WARN (띠 폭은 침식으로 잰다)."""
    pcb = _board_model(cfg)
    gap = cfg["jig"]["min_support_band"]

    # 양옆에 0.7mm 만 남기고 가로지르는 포켓 → 띠는 이어져 있지만 목이 0.7mm
    feat, warns = support(pcb, [_relief(0.7, 10, 59.3, 30, 2.0)], cfg)
    assert feat.params["bands"] == 1
    assert feat.params["thin_bands"] == 1
    assert feat.params["min_band"] == pytest.approx(gap)
    thin = feat.params["thin_band_regions"][0]
    assert thin["pieces"] == 2  # 목 때문에 침식하면 두 조각
    assert any("좁은 목" in w and "2조각" in w for w in warns)

    # 같은 포켓을 양옆 2mm 남기면 경고 없음 (목이 기준보다 넓다)
    feat, warns = support(pcb, [_relief(2.0, 10, 58.0, 30, 2.0)], cfg)
    assert feat.params["bands"] == 1
    assert feat.params["thin_bands"] == 0
    assert not [w for w in warns if "받침띠" in w]


def test_support_rib_width(cfg):
    """받침 리브 폭은 min_support_band 로 따로 본다 (merge_gap 과 분리된 설정).

    침식 검사는 띠가 끊기는 목만 잡으므로, 돌아가는 길이 있는 얇은 리브는 거리로 재야 한다.
    재는 대상은 포켓 사각형이 아니라 **받침 없는 연결 영역** — ② 의 브리지가 이미 없앤
    사각형 사이 틈을 받침으로 세면 안 된다.
    """
    pcb = _board_model(cfg)
    # 사이 1.2mm 를 남긴 포켓 두 개 (merge_gap 1.0 이상이라 R5 가 잇지 않는다)
    rel = merge_reliefs(
        [_relief(10, 10, 30, 20, 2.0), _relief(10, 21.2, 30, 30, 2.0)],
        cfg["jig"]["merge_gap"],
    )
    assert [f.rule_id for f in rel] == ["R2", "R2"]  # 브리지 없음 = 리브가 실제로 남는다

    loose = copy.deepcopy(cfg)
    loose["jig"]["min_support_band"] = 1.0
    feat, warns = support(pcb, rel, loose)
    assert feat.params["thin_ribs"] == 0
    assert not [w for w in warns if "리브" in w]

    strict = copy.deepcopy(cfg)
    strict["jig"]["min_support_band"] = 2.0
    feat, warns = support(pcb, rel, strict)
    assert feat.params["thin_ribs"] == 1
    rib = feat.params["thin_rib_regions"][0]
    assert rib["width"] == pytest.approx(1.2, abs=1e-6)
    assert any("받침 리브" in w and "1.20mm" in w for w in warns)

    # 사각형 사이 틈이 merge_gap 미만이면 브리지가 생기므로 리브로 세지 않는다
    bridged = merge_reliefs(
        [_relief(10, 10, 30, 20, 2.0), _relief(10, 20.5, 30, 30, 2.0)],
        cfg["jig"]["merge_gap"],
    )
    assert any(f.rule_id == "R5" for f in bridged)
    feat, warns = support(pcb, bridged, strict)
    assert feat.params["thin_ribs"] == 0


# --- 5. R7 안착 벽 손실 -----------------------------------------------------

def test_wall_check(cfg):
    pcb = _board_model(cfg)

    feat, warns = wall(pcb, [_relief(2, 10, 10, 20, 2.0), _relief(-0.2, 10, 10, 20, 2.0)], cfg)
    assert feat.params["lost"] == pytest.approx(0.0, abs=1e-6)
    assert warns == []

    feat, warns = wall(pcb, [_relief(-0.3, 10, 10, 20, 2.0)], cfg)
    assert feat.params["lost_by_clearance"] == pytest.approx(10.0, abs=1e-2)
    assert len(warns) == 1
    assert "여유 0.5mm 가 벽을 넘음" in warns[0]

    feat, warns = wall(pcb, [_relief(-5, 10, 10, 20, 2.0)], cfg)
    assert feat.params["lost_by_part"] == pytest.approx(10.0, abs=1e-2)
    assert len(warns) == 1
    assert "불가피" in warns[0]


# --- 6. 지그 형상·간섭·안착 -------------------------------------------------

def test_jig_no_interference_and_seated(sample_step, cfg):
    pcb = _load(sample_step, cfg)
    feats, _ = apply_rules(pcb, cfg)
    jig = build_jig(pcb, feats)

    solids = jig.Solids()
    assert len(solids) == 1
    assert interference(jig, seated_pcb(pcb, feats)) < 1e-6

    d = cfg["jig"]["pocket_depth"]
    assert _inside(solids[0], (50, 30, -d - 0.01)), "포켓 바닥 아래는 재료여야 한다"
    assert not _inside(solids[0], (50, 30, -d / 2)), "안착 포켓 안은 빈 공간이어야 한다"

    want = -(1.0 + 3.0 + 0.5 + 2.0)
    assert jig.BoundingBox().zmin == pytest.approx(want, abs=1e-6)


# --- 7. face_down = top -----------------------------------------------------

def test_face_down_top(sample_step, cfg):
    pcb = load_pcb(sample_step, face_down="top", board_max_thickness=cfg["pcb"]["board_max_thickness"])
    assert sorted(round(p.height, 2) for p in pcb.protrusions) == [2.0, 2.0]

    feats, _ = apply_rules(pcb, cfg)
    jig = build_jig(pcb, feats)
    assert interference(jig, seated_pcb(pcb, feats)) < 1e-6


# --- 8. 돌출 0건 경고 -------------------------------------------------------

def test_no_protrusion_warns(tmp_path, cfg):
    step = make_pcb(tmp_path / "flat.step", with_bottom_parts=False)
    pcb = _load(step, cfg)
    _feats, warns = apply_rules(pcb, cfg)
    assert any("돌출 부품 0건" in w for w in warns)


# --- 9. CLI + 도면 ---------------------------------------------------------

def _check_drawing(path, base, seat_depth, depths, pin_xy):
    doc = ezdxf.readfile(path)
    auditor = doc.audit()
    assert not auditor.errors, auditor.errors

    msp = doc.modelspace()
    dims = msp.query("DIMENSION")

    measured = []
    for dim in dims:
        block = doc.blocks.get(dim.dxf.geometry)
        shown = [e.text for e in block if e.dxftype() == "MTEXT"]
        assert len(shown) == 1, f"치수 문자 {shown}"
        m = dim.get_measurement()
        # dimlfac=100 이면 표시 문자가 100배로 찍힌다 (실제 사고) → 표시 문자로 검사
        assert float(shown[0]) == pytest.approx(round(m, 2), abs=5e-3)
        measured.append(round(m, 2))

    n = len(pin_xy)
    assert len(dims) == 2 * n + 2 + (1 if n == 2 else 0) + 2

    width = round(base["xmax"] - base["xmin"], 2)
    height = round(-base["zmin"], 2)
    assert width in measured
    assert height in measured
    if n == 2:
        (x0, y0), (x1, y1) = pin_xy
        dist = round(math.dist((x0, y0), (x1, y1)), 2)
        assert any(abs(m - dist) < 0.01 for m in measured)

    texts = [
        e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
        for e in msp
        if e.dxftype() in ("TEXT", "MTEXT")
    ]
    for dep in depths:
        assert any(f"{dep:.2f}" in t for t in texts), f"깊이 {dep} 문자 없음"
    assert any(f"{seat_depth:.2f}" in t for t in texts)
    assert any(t.rstrip().endswith("(KST)") for t in texts)

    assert "SECTION" in doc.layers
    assert any(e.dxf.layer == "SECTION" for e in msp)


def test_cli(sample_step, tmp_path, cfg):
    out = tmp_path / "cli"
    res = subprocess.run(
        [sys.executable, "-m", "jigauto", "build", str(sample_step), "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert res.returncode == 0, res.stdout + res.stderr

    for name in ("jig.step", "jig.stl", "jig.dxf", "report.json"):
        assert (out / name).stat().st_size > 0

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["counts"] == {
        "holes": 4,
        "protrusions": 3,
        "reliefs": 3,
        "pins": 2,
        "solids": 1,
    }
    assert report["interference_mm3"] < 1e-6

    again = cq.importers.importStep(str(out / "jig.step"))
    assert len(again.val().Solids()) == 1

    feats = report["features"]
    base = [f for f in feats if f["kind"] == "base"][0]["params"]
    seat = [f for f in feats if f["kind"] == "seat_pocket"][0]["params"]
    depths = sorted({round(f["params"]["depth"], 6) for f in feats if f["kind"] == "relief"})
    pin_xy = sorted(
        (f["params"]["x"], f["params"]["y"]) for f in feats if f["kind"] == "pin"
    )
    _check_drawing(str(out / "jig.dxf"), base, seat["depth"], depths, pin_xy)


# --- 19. R5 브리지 단차 (깊이 다른 두 포켓) --------------------------------

def test_bridge_depth_step(cfg):
    """깊이 다른 두 포켓을 브리지로 이으면 얕은 쪽 깊이로 단차가 남는다.

    바운딩박스 병합이면 얕은 쪽까지 깊이가 최대값으로 파여 받침이 사라졌다.
    """
    pcb = _board_model(cfg)
    parts = [(10.0, 10.0, 20.0, 20.0, 0.5), (21.2, 10.0, 30.0, 20.0, 3.0)]
    pcb.protrusions = [Protrusion(*p) for p in parts]
    pcb.shape = cq.Compound.makeCompound(
        [pcb.shape]
        + [
            cq.Solid.makeBox(x1 - x0, y1 - y0, h, pnt=cq.Vector(x0, y0, -h))
            for x0, y0, x1, y1, h in parts
        ]
    )

    feats, warns = apply_rules(pcb, cfg)
    rel = _kind(feats, "relief")
    bridges = [f for f in rel if f.rule_id == "R5"]

    j = cfg["jig"]
    shallow = j["pocket_depth"] + 0.5 + j["depth_clearance"]
    deep = j["pocket_depth"] + 3.0 + j["depth_clearance"]
    assert len(bridges) == 1
    assert bridges[0].params["depth"] == pytest.approx(shallow)
    assert sorted(round(f.params["depth"], 3) for f in rel if f.rule_id == "R2") == [
        pytest.approx(shallow),
        pytest.approx(deep),
    ]

    jig = build_jig(pcb, feats)
    solids = jig.Solids()
    assert len(solids) == 1
    assert interference(jig, seated_pcb(pcb, feats)) < 1e-6

    # 브리지 자리는 얕은 깊이까지만 파인다 (bbox 병합이면 여기가 빈 공간이었다)
    bridge_x = (bridges[0].params["xmin"] + bridges[0].params["xmax"]) / 2
    assert _inside(solids[0], (bridge_x, 15.0, -(shallow + 0.5)))
    assert not _inside(solids[0], (bridge_x, 15.0, -(shallow - 0.1)))


# --- 21. 도면 가독성: 같은 깊이는 영역 하나로 ------------------------------

def test_drawing_merges_same_depth_regions(phone_result, spec_cfg, tmp_path):
    """포켓마다 사각형·태그를 찍으면 도면을 읽을 수 없다 (② 이후 80개).

    같은 깊이로 이어진 포켓·브리지는 영역 하나의 외곽선 + 태그 하나로 그린다.
    """
    pcb, feats, _ = phone_result
    jig = build_jig(pcb, feats)
    path = tmp_path / "jig.dxf"
    write_drawing(jig, pcb, feats, spec_cfg, path, source="test")

    rel = _kind(feats, "relief")
    regions = depth_regions(rel)
    assert len(rel) > 4 * len(regions), "영역으로 묶여야 한다"

    doc = ezdxf.readfile(str(path))
    assert not doc.audit().errors
    msp = doc.modelspace()
    on_relief = [e for e in msp if e.dxf.layer == "RELIEF"]

    outlines = [e for e in on_relief if e.dxftype() == "LWPOLYLINE"]
    tags = [e for e in on_relief if e.dxftype() == "TEXT"]
    assert len(tags) == len(regions)
    assert len(outlines) >= len(regions)  # 영역 안에 깊은 포켓이 있으면 내부 루프가 더해진다

    h = spec_cfg["drawing"]["text_height"]
    assert min(e.dxf.height for e in tags) >= 0.3 * h, "태그가 읽을 수 없게 작아졌다"

    # 깊이표에 영역 수가 들어가고, 그려진 태그 종류가 깊이 수와 맞는지
    depths = sorted({round(f.params["depth"], 6) for f in rel})
    assert {e.dxf.text for e in tags} == {f"D{i + 1}" for i in range(len(depths))}
    table = [e.dxf.text for e in msp if e.dxftype() == "TEXT"]
    for i, dep in enumerate(depths):
        n = sum(1 for d, _f, _g in regions if d == dep)
        assert any(f"D{i + 1}   {dep:.2f}   ({n}곳)" == t for t in table)

    # 받침이 기준을 넘는 지그에는 SUPPORT 표시도, 주기 7 도 없다
    assert not [e for e in msp if e.dxf.layer == "SUPPORT"]
    assert not [t for t in table if t.startswith("7.")]

    # 남은 받침띠는 BAND 레이어 해치로 그린다 — 경계 = 띠 외곽 1 + 받침 없는 영역 섬들
    hatches = msp.query("HATCH")
    sup = _kind(feats, "support_check")[0].params
    assert len(hatches) == sup["bands"] == 1
    assert hatches[0].dxf.layer == "BAND"
    assert hatches[0].dxf.pattern_name == "ANSI31"
    assert len(hatches[0].paths) == 1 + sup["regions"]
    assert any(f"받침띠   {sup['bands']}곳" in t for t in table)


# --- 20. (가상) 밀집 혼합높이 샘플 — 깊은 구간이 국소화되는지 --------------

def test_dense_sample_depth_localized(tmp_path, spec_cfg):
    """높은 부품 2개 + 낮은 부품 28개가 한 덩어리로 묶여도 깊게 파이는 자리는 그곳뿐.

    바운딩박스 병합이던 때는 묶인 구간 전체(333.7mm²)가 최대 깊이로 파였다.
    """
    from make_dense_pcb import TALL, make_dense_pcb

    pcb = _load(make_dense_pcb(tmp_path / "dense_pcb.step"), spec_cfg)
    feats, warns = apply_rules(pcb, spec_cfg)

    j = spec_cfg["jig"]
    deepest = j["pocket_depth"] + 3.0 + j["depth_clearance"]
    rel = _kind(feats, "relief")
    assert max(f.params["depth"] for f in rel) == pytest.approx(deepest)

    c = j["comp_clearance"]
    one = (2.0 + 2 * c) ** 2
    deep_area = sum(
        (f.params["xmax"] - f.params["xmin"]) * (f.params["ymax"] - f.params["ymin"])
        for f in rel
        if abs(f.params["depth"] - deepest) < 1e-9
    )
    assert deep_area == pytest.approx(len(TALL) * one, rel=1e-6)

    # 받침 비율은 면적만 보므로 브리지로는 개선되지 않는다 (경고는 그대로 떠야 한다).
    # 경고는 받침 없는 연결 영역 1곳(부품 묶음 전체)과 기판 횡단을 지목해야 한다
    sup = _kind(feats, "support_check")[0].params
    assert sup["regions"] == 1
    assert sup["max_region_area"] == pytest.approx(sup["unsupported_area"], rel=1e-6)
    assert sup["max_span_ratio"] > sup["max_span_limit"]
    assert any("PCB 받침" in w and "연결 영역 1곳" in w for w in warns)
    assert any("가로지름" in w for w in warns)

    jig = build_jig(pcb, feats)
    assert len(jig.Solids()) == 1
    assert interference(jig, seated_pcb(pcb, feats)) < 1e-6

    # 경고 난 받침 없는 영역은 도면 SUPPORT 레이어에 파선 사각형 + 라벨로 표시된다
    path = tmp_path / "jig.dxf"
    write_drawing(jig, pcb, feats, spec_cfg, path, source="dense")
    doc = ezdxf.readfile(str(path))
    assert not doc.audit().errors
    assert doc.layers.get("SUPPORT").dxf.linetype == "DASHED"

    msp = doc.modelspace()
    on_support = [e for e in msp if e.dxf.layer == "SUPPORT"]
    boxes = [e for e in on_support if e.dxftype() == "LWPOLYLINE"]
    labels = [e.dxf.text for e in on_support if e.dxftype() == "TEXT"]
    assert len(boxes) == 1
    region = sup["max_span_region"]
    xs = [p[0] for p in boxes[0].get_points()]
    ys = [p[1] for p in boxes[0].get_points()]
    assert min(xs) == pytest.approx(region["xmin"], abs=1e-3)
    assert max(xs) == pytest.approx(region["xmax"], abs=1e-3)
    assert min(ys) == pytest.approx(region["ymin"], abs=1e-3)
    assert max(ys) == pytest.approx(region["ymax"], abs=1e-3)
    assert any("받침없음" in t and "받침 48%" in t for t in labels)
    assert any("횡단" in t for t in labels)
    assert any("좁은 목" in t for t in labels)  # 받침띠는 라벨만 (bbox 가 기판 전체라 무의미)
    notes = [e.dxf.text for e in msp if e.dxftype() == "TEXT"]
    assert any(t.startswith("7.") and "SUPPORT" in t and "BAND" in t for t in notes)


# --- 10. (가상) 휴대폰 샘플 -------------------------------------------------

def test_phone_sample(phone_result, spec_cfg):
    pcb, feats, warns = phone_result

    assert pcb.thickness == pytest.approx(0.8)
    assert len(pcb.holes) == 5
    assert len(pcb.protrusions) == 37

    rel = _kind(feats, "relief")
    # 돌출 37건은 각자 포켓(R2) 그대로, 얇은 벽 자리에만 브리지(R5)
    assert len([f for f in rel if f.rule_id == "R2"]) == 37
    assert len([f for f in rel if f.rule_id == "R5"]) == 43
    assert not any("merged" in f.params for f in rel)
    assert _pin_xy(feats) == [(3.0, 49.0), (67.0, 3.0)]

    sup = _kind(feats, "support_check")[0].params
    assert sup["unsupported_area"] == pytest.approx(514.8, abs=0.5)
    assert sup["ratio"] == pytest.approx(0.84, abs=0.01)
    assert _kind(feats, "wall_check")[0].params["lost"] == pytest.approx(0.0, abs=1e-6)
    assert warns == []

    jig = build_jig(pcb, feats)
    assert len(jig.Solids()) == 1
    assert interference(jig, seated_pcb(pcb, feats)) < 1e-6


# --- 11. (가상) ECAD 구조 샘플 ---------------------------------------------

def test_ecad_like_structure(ecad_step, spec_cfg):
    pcb = _load(ecad_step, spec_cfg)
    feats, warns = apply_rules(pcb, spec_cfg)

    assert pcb.thickness == pytest.approx(0.8)
    assert len(pcb.holes) == 214
    assert any("셸 부품 3개" in w for w in warns)

    rel = _kind(feats, "relief")
    assert len([f for f in rel if f.rule_id == "R2"]) == 123
    assert len([f for f in rel if f.rule_id == "R5"]) == 146

    by_corner = {(round(f.params["xmin"], 2), round(f.params["ymin"], 2)): f for f in rel}
    assert by_corner[(57.7, 14.7)].params["depth"] == pytest.approx(2.3)
    assert by_corner[(57.7, 3.7)].params["depth"] == pytest.approx(2.4)
    assert by_corner[(29.7, 7.7)].params["depth"] == pytest.approx(1.85)
    assert by_corner[(9.45, 27.95)].params["xmax"] == pytest.approx(12.55)
    assert by_corner[(33.45, 46.45)].params["depth"] == pytest.approx(2.4)

    assert _pin_xy(feats) == [(3.0, 49.0), (67.0, 3.0)]

    jig = build_jig(pcb, feats)
    assert len(jig.Solids()) == 1
    assert interference(jig, seated_pcb(pcb, feats)) < 1e-6


# --- 12~14. 고객 2D 도면 = STEP 경로와 같은 결과 ---------------------------

@pytest.mark.parametrize("kind", ["clean", "messy", "layer0"])
def test_dxf_input_matches_step(kind, dxf_files, phone_result, spec_cfg):
    step_pcb, step_feats, _ = phone_result

    pcb = load_dxf(dxf_files[kind], spec_cfg)
    feats, _warns = apply_rules(pcb, spec_cfg)

    assert _holes(pcb) == _holes(step_pcb)
    assert _relief_boxes(feats) == _relief_boxes(step_feats) | {CAMERA_POCKET}
    assert _pin_xy(feats) == _pin_xy(step_feats)

    jig = build_jig(pcb, feats)
    assert len(jig.Solids()) == 1
    assert interference(jig, seated_pcb(pcb, feats)) < 1e-6


# --- 15. 뷰 표시 없음 → ERROR ----------------------------------------------

def test_dxf_unlabeled_view(dxf_files, spec_cfg, tmp_path):
    with pytest.raises(ValueError) as err:
        load_dxf(dxf_files["unlabeled"], spec_cfg)
    assert "--view" in str(err.value)

    pcb = load_dxf(dxf_files["unlabeled"], spec_cfg, view=2)
    assert len(pcb.holes) == 5

    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "jigauto",
            "build",
            str(dxf_files["unlabeled"]),
            "--out",
            str(tmp_path / "unlabeled"),
            "--config",
            str(ROOT / "samples" / "spec_phone_main.toml"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert res.returncode == 1
    assert "[ERROR]" in res.stdout


# --- 16. 반전 설정이 도면과 반대 → ERROR ----------------------------------

def test_dxf_mirror_opposite(dxf_files, spec_cfg):
    bad = copy.deepcopy(spec_cfg)
    bad["drawing2d"]["bottom_view_mirrored"] = not spec_cfg["drawing2d"]["bottom_view_mirrored"]

    with pytest.raises(ValueError) as err:
        load_dxf(dxf_files["messy"], bad)
    assert "반대" in str(err.value)


# --- 17. 부품 외곽 한 변 빠짐 → WARN --------------------------------------

def test_dxf_broken_part_warns(dxf_files, spec_cfg):
    pcb = load_dxf(dxf_files["broken"], spec_cfg)
    assert len(pcb.protrusions) == 37
    assert any("닫히지 않은 선 1개" in n for n in pcb.notes)


# --- 18. 원형 부품 ↔ 홀 구분 (Top 뷰 대조) --------------------------------

def test_dxf_round_part_vs_hole(dxf_files, spec_cfg):
    pcb = load_dxf(dxf_files["round_top"], spec_cfg)
    assert len(pcb.holes) == 5
    assert len(pcb.protrusions) == 39

    feats, _ = apply_rules(pcb, spec_cfg)
    assert (38.2, 33.2, 41.8, 36.8) in _relief_boxes(feats)

    # Top 뷰가 없으면 원형 부품을 홀로 읽고 경고한다 (포켓 누락 위험)
    pcb = load_dxf(dxf_files["round_notop"], spec_cfg)
    assert len(pcb.holes) == 6
    assert len(pcb.protrusions) == 38
    assert any("홀로 처리" in n for n in pcb.notes)
