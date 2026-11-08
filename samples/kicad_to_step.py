"""KiCad 보드 → STEP 래퍼.

kicad-cli 를 직접 부르면 3D 모델 경로(${KISYS3DMOD} 등)가 안 풀려 부품 없는 기판만 나온다
(-D·환경변수로도 안 됨, 2026-11-06 3회 반복). 보드 사본에서 경로를 절대경로로 치환하고
.wrl 을 .step 으로 바꾼 뒤 kicad-cli 를 부른다.

    python samples/kicad_to_step.py <board.kicad_pcb> <out.step>
    python samples/kicad_to_step.py --demo stickhub/StickHub.kicad_pcb samples/kicad/stickhub.step
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_KICAD = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "KiCad" / "10.0"


def kicad_home() -> Path:
    home = Path(os.environ.get("KICAD_HOME") or DEFAULT_KICAD)
    if not home.exists():
        raise SystemExit(f"[ERROR] KiCad 를 찾을 수 없음: {home} (KICAD_HOME 으로 지정)")
    return home


def _models_dir(home: Path) -> Path:
    for rel in ("share/kicad/3dmodels", "share/3dmodels"):
        p = home / rel
        if p.exists():
            return p
    raise SystemExit(f"[ERROR] 3D 모델 폴더를 찾을 수 없음: {home}")


def _cli(home: Path) -> Path:
    for rel in ("bin/kicad-cli.exe", "bin/kicad-cli"):
        p = home / rel
        if p.exists():
            return p
    raise SystemExit(f"[ERROR] kicad-cli 를 찾을 수 없음: {home}")


def export_step(board: Path, out: Path) -> Path:
    home = kicad_home()
    models = _models_dir(home).as_posix()

    text = board.read_text(encoding="utf-8", errors="replace")
    text = text.replace("${KISYS3DMOD}", models)
    text = re.sub(r"\$\{KICAD\d*_3DMODEL_DIR\}", models, text)
    text = re.sub(r"\.wrl(?=[\"\s)])", ".step", text)

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / board.name
        copy.write_text(text, encoding="utf-8")
        for extra in board.parent.glob("*.kicad_*"):
            if extra.name != board.name:
                shutil.copy2(extra, Path(tmp) / extra.name)
        cmd = [
            str(_cli(home)),
            "pcb",
            "export",
            "step",
            "--force",
            "-o",
            str(out),
            str(copy),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit(f"[ERROR] kicad-cli 실패:\n{res.stdout}\n{res.stderr}")
        missing = [l for l in res.stdout.splitlines() if "not found" in l.lower()]
        if missing:
            print(f"[WARN] 3D 모델 누락 {len(missing)}건")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kicad_to_step")
    ap.add_argument("board", help="보드 파일 (또는 --demo 와 함께 demos 폴더 상대경로)")
    ap.add_argument("out")
    ap.add_argument("--demo", action="store_true", help="KiCad 예제 보드 폴더 기준 경로")
    args = ap.parse_args(argv)

    board = Path(args.board)
    if args.demo:
        board = kicad_home() / "share" / "kicad" / "demos" / args.board
    if not board.exists():
        raise SystemExit(f"[ERROR] 보드 파일 없음: {board}")

    out = export_step(board, Path(args.out))
    print(f"[OK] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
