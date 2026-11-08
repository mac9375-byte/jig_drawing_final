"""CLI: python -m jigauto build <input.step|input.dxf> --out <dir> [--config <spec.toml>]"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import asdict
from pathlib import Path

from .jig import build_jig, export_all, interference, seated_pcb
from .pcb import load_pcb
from .rules import apply_rules

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
INTERFERENCE_LIMIT = 1e-6


def load_config(path) -> dict:
    with open(path, "rb") as fp:
        return tomllib.load(fp)


def build(args) -> int:
    cfg = load_config(args.config)
    src = Path(args.input)

    if src.suffix.lower() == ".dxf":
        from .dxf_input import load_dxf

        pcb = load_dxf(src, cfg, view=args.view)
    else:
        pcb = load_pcb(
            src,
            face_down=cfg["pcb"]["face_down"],
            board_max_thickness=cfg["pcb"]["board_max_thickness"],
        )

    print(
        f"[OK] PCB 두께 {pcb.thickness:.2f}mm, 홀 {len(pcb.holes)}개, "
        f"기판 아래 돌출 {len(pcb.protrusions)}건"
    )

    feats, warnings = apply_rules(pcb, cfg)
    for w in warnings:
        print(f"[WARN] {w}")

    jig = build_jig(pcb, feats)
    placed = seated_pcb(pcb, feats)
    vol = interference(jig, placed)
    solids = len(jig.Solids())

    outputs = export_all(jig, pcb, feats, cfg, args.out, source=str(src))

    report = {
        "input": str(src),
        "face_down": cfg["pcb"]["face_down"],
        "counts": {
            "holes": len(pcb.holes),
            "protrusions": len(pcb.protrusions),
            "reliefs": len([f for f in feats if f.kind == "relief"]),
            "pins": len([f for f in feats if f.kind == "pin"]),
            "solids": solids,
        },
        "interference_mm3": vol,
        "jig_volume_mm3": jig.Volume(),
        "features": [asdict(f) for f in feats],
        "warnings": warnings,
        "outputs": outputs,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    outputs["report"] = str(report_path)

    if vol > INTERFERENCE_LIMIT or solids != 1:
        print(f"[ERROR] 간섭 {vol:.6f}mm³, 솔리드 {solids}개 — 지그 불합격")
        return 1

    print(
        f"[OK] 회피 포켓 {report['counts']['reliefs']}개, 핀 {report['counts']['pins']}개, "
        f"간섭 0 → {out}"
    )
    return 0


def main(argv=None) -> int:
    # 콘솔 코드페이지가 cp949 여도 경고 문구(—, Ø 등)가 깨지지 않게 한다
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(prog="jigauto")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="PCB 입력에서 안착 지그 생성")
    b.add_argument("input")
    b.add_argument("--out", default="out")
    b.add_argument("--config", default=str(DEFAULT_CONFIG))
    b.add_argument("--view", type=int, default=None, help="2D 도면 입력에서 쓸 기판 뷰 번호")
    b.set_defaults(func=build)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
