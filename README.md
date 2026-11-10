# PCB 안착 지그 설계 자동화

2차면 SMT 공정에서 1차면 부품과의 간섭을 피할 수 있도록 PCB 안착 지그를 생성하는 CLI입니다. PCB STEP 또는 2D DXF에서 기판과 부품 형상을 읽고 안착 포켓, 부품 회피 포켓, 위치결정 핀을 설계합니다.

## 빠른 시작

Python 3.12가 설치된 Windows에서 PowerShell을 열고 저장소 루트에서 실행합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 가상 PCB의 3D STEP 입력
.\.venv\Scripts\python.exe -m jigauto build samples\phone_main_pcb.step --out out\step --config samples\spec_phone_main.toml

# 같은 가상 PCB의 2D DXF 입력
.\.venv\Scripts\python.exe -m jigauto build samples\phone_main_messy.dxf --out out\dxf --config samples\spec_phone_main.toml
```

각 출력 폴더에 `jig.step`, `jig.stl`, `jig.dxf`, `report.json`이 생성됩니다. DXF에는 평면도, 치수, 단면 A-A, 포켓 깊이표가 포함됩니다. `report.json`에서 적용 규칙, 간섭 부피, 받침 부족 등의 경고를 확인할 수 있습니다.

`--config`를 생략하면 [기본 설정](config.toml)이 적용됩니다. 2D DXF의 기판 뷰를 자동으로 고를 수 없으면 오류에 표시된 후보 중 올바른 번호를 `--view`로 지정합니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

가상 샘플로 간섭, 안착, 단일 솔리드와 도면 출력을 검증했습니다. 실제 고객 STEP·DXF를 사용한 검증과 AutoCAD에서 도면을 직접 열어 보는 확인은 남아 있습니다. [검증 결과와 계획](plan.md) · [인수인계 및 미검증 항목](handoff.md)
