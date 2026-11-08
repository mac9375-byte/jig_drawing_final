"""합성 PCB STEP 생성 + 공용 fixture."""

from __future__ import annotations

import tomllib
from pathlib import Path

import cadquery as cq
import pytest

ROOT = Path(__file__).resolve().parent.parent

BOARD_L, BOARD_W, BOARD_T = 60.0, 40.0, 1.0
HOLES = [(5.0, 5.0, 3.0), (55.0, 35.0, 3.0), (30.0, 5.0, 3.0), (30.0, 20.0, 0.5)]


def make_pcb(path, with_bottom_parts: bool = True) -> str:
    """합성 PCB: 60x40x1.0 기판 + 윗면 부품 1개 (+ 아랫면 부품 3개)."""
    board = (
        cq.Workplane("XY")
        .moveTo(BOARD_L / 2, BOARD_W / 2)
        .rect(BOARD_L, BOARD_W)
        .extrude(BOARD_T)
        .edges("|Z")
        .fillet(3.0)
    )
    solid = board.val()
    for x, y, d in HOLES:
        solid = solid.cut(
            cq.Solid.makeCylinder(d / 2, BOARD_T + 2, pnt=cq.Vector(x, y, -1.0))
        )

    parts = [
        cq.Solid.makeBox(10.0, 10.0, 2.0, pnt=cq.Vector(20.0, 15.0, BOARD_T)),  # 윗면
    ]
    if with_bottom_parts:
        parts += [
            cq.Solid.makeBox(8.0, 6.0, 1.5, pnt=cq.Vector(10.0, 20.0, -1.5)),   # A
            cq.Solid.makeBox(5.0, 5.0, 3.0, pnt=cq.Vector(40.0, 10.0, -3.0)),   # B
            cq.Solid.makeCylinder(0.4, 5.0, pnt=cq.Vector(25.0, 30.0, -2.0)),   # THT 리드
        ]

    cq.exporters.export(cq.Compound.makeCompound([solid] + parts), str(path))
    return str(path)


@pytest.fixture(scope="session")
def cfg() -> dict:
    with open(ROOT / "config.toml", "rb") as fp:
        return tomllib.load(fp)


@pytest.fixture(scope="session")
def spec_cfg() -> dict:
    with open(ROOT / "samples" / "spec_phone_main.toml", "rb") as fp:
        return tomllib.load(fp)


@pytest.fixture(scope="session")
def sample_step(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("step") / "synthetic_pcb.step"
    return make_pcb(path)
