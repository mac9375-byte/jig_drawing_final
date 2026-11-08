# HANDOFF — PCB 안착 지그 설계 자동화

작성: 2026-11-08 · 데스크톱 → **노트북**에서 clone 후 추가 개발·검증

먼저 읽을 것: `claude.md`(규칙·Constraints) → `plan.md`(단계별 계획·실행·측정 기록) → `prd.md`(요구사항)
이 문서는 **저장소에 담기지 않는 것**만 적는다 — 환경 구축, 재생성 명령, 미검증 항목, 사용자 대기 항목.

---

## 1. 한 줄 상태 (2026-11-08)

형상 엔진 MVP(CLI) 완료. STEP / 고객 2D DXF 입력 → 규칙 R1~R7 → STEP·STL·DXF 출력.
**테스트 23개 전부 통과** (`pytest -q`, 약 30초). 간섭 0 / 단일 솔리드 — 샘플 7종 전부.

저장소: https://github.com/mac9375-byte/jig_drawing_final (main, 첫 커밋 = 위 상태)

## 2. 노트북 환경 구축

```
git clone https://github.com/mac9375-byte/jig_drawing_final.git
cd jig_drawing_final
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
```

- 검증된 조합: **Python 3.12.10 / cadquery 2.8.0 / OCP 7.9 / ezdxf 1.4.5 / pytest 9**
- `ezdxf` 는 cadquery 의존성으로 같이 깔린다 (`requirements.txt` 에 따로 없음)
- 테스트는 cadquery 불리언 연산이라 수십 초 걸린다. 첫 실행이 느린 것은 정상

## 3. 저장소에 없는 것 (= .gitignore 대상, 빌드로 다시 생김)

| 경로 | 무엇 | 되살리는 법 |
|---|---|---|
| `out/` | 빌드 산출물(STEP·STL·DXF·report.json) 8세트 | 아래 재생성 명령 |
| `.venv/` | 가상환경 | 2절 |
| `samples/kicad/` | KiCad 데모(StickHub) STEP 사본 | KiCad 10 설치 후 `samples/kicad_to_step.py` |
| `_bak/`, `*.bak*` | 수정 전 백업 사본 | 복원 불필요 (git 이력이 대체) |

### 재생성 명령 (전부 `.venv\Scripts\python` 으로)

```
python samples\make_phone_pcb.py
python samples\make_ecad_like_pcb.py
python samples\make_dense_pcb.py
python samples\make_phone_dxf.py
python -m jigauto build samples\phone_main_pcb.step   --out out\phone_main --config samples\spec_phone_main.toml
python -m jigauto build samples\ecad_like_pcb.step    --out out\ecad       --config samples\spec_phone_main.toml
python -m jigauto build samples\dense_pcb.step        --out out\dense_cli  --config samples\spec_phone_main.toml
python -m jigauto build samples\phone_main_clean.dxf  --out out\dxf_clean  --config samples\spec_phone_main.toml
python -m jigauto build samples\phone_main_messy.dxf  --out out\dxf_messy  --config samples\spec_phone_main.toml
python -m jigauto build samples\phone_main_layer0.dxf --out out\dxf_layer0 --config samples\spec_phone_main.toml
python -m jigauto build samples\phone_main_unlabeled.dxf --out out\dxf_view2 --view 2 --config samples\spec_phone_main.toml
```

기대값(포켓 수·경고 문구·간섭 0)은 `plan.md` 각 절의 결과표에 있다. 밀집 샘플의 WARN 3건
(받침 48%·구간 92%·받침띠 목)은 **검사기가 제대로 잡은 것**이며 결함이 아니다.

## 4. 미검증 — 노트북에서 확인할 후보

1. **AutoCAD 에서 `out/phone_main/jig.dxf` 열기** — 사용자 대기 항목 1순위. 치수 스타일(dimlfac=1),
   레이어(RELIEF·SUPPORT·BAND·DIM), 표제란이 사내 관행에 맞는지. ezdxf 로만 검증했다
2. **실제 STEP·실제 고객 2D 도면** — 지금은 전부 가상 샘플이다. 실물 1건이 들어오면 pcb.py 의
   기판 검출(`board_max_thickness`)과 dxf_input.py 의 형상 분류가 처음으로 실전 검증된다
3. **KiCad 실측(StickHub)** — 노트북에 KiCad 10 을 깔면 가능. `claude.md` 의 주의(kicad-cli 직접 호출 금지) 필수
4. **포켓 99~269개 도면의 AutoCAD 열기 성능** — ECAD 샘플이 가장 무겁다

## 5. 남은 작업 (우선순위 순, 미착수)

1. **받침 면적 문제** — R5 개선(②)은 깊이만 고쳤다. 받침 비율은 48.4% → 48.4% 로 **효과 0**(실측).
   부품 배치 차원의 문제여서 별 과제로 다뤄야 한다. 검사·경고는 이미 다 들어가 있다
2. **`[가정]` 수치 교체** — `config.toml` 과 `samples/spec_phone_main.toml` 에 `# [가정]` 주석이 붙은 20여 개.
   특히 `min_support_band`(현 1.0): 1.5 로 올리면 0201 두 줄 사이 1.10mm 리브가 바로 걸린다
   (캘리브레이션 표는 `plan.md` 2026-11-07 마지막 절)
3. **최소 벽 두께 검사** — 브리지 이후 남는 얇은 조각을 보는 검사가 아직 없다
4. **단면 A-A** — 가장 깊은 포켓 한 곳만 자른다. 단차가 여러 단인 지그는 단면 1개로 다 안 보인다
5. **R7 중복 계산** — 사각형별로 벽 손실을 더하므로 사각형이 겹치면 중복 가능 (현 샘플에선 0)
6. 범위 밖 후보: 웹 UI, 토폴로지 최적화, Gerber/BOM 입력 (`plan.md` "이후 단계")

## 6. 사용자 맥락

- 한국성전(휴대폰용 PCB 공급) **현업 실사용** 목적. 2차면 SMT 시 1차면 부품 회피용 안착 지그. CAD 는 AutoCAD
- 실제 데이터가 없어 전부 가상 샘플로 검증 — 샘플·사양서의 `(가상)`/`SAMPLE` 표기를 유지한다
- 사용자 대기: AutoCAD 열기 확인 / `[가정]` 값의 사내 기준 / 사내 표제란 양식
- 응답은 한국어. 수정 전 `.bak` 백업, 같은 오류 2회면 중단 — 전역 CLAUDE.md 규칙

## 7. git

- 이 저장소는 2026-11-08 데스크톱에서 `git init` 후 첫 커밋 = 위 완료 상태. 그 이전 이력은 없다
  (데스크톱의 11/06 로컬 커밋 `613d5eb` 는 사내망 푸시가 403 으로 막혀 올리지 못한 별개 저장소다)
- 커밋 작성자는 Public 저장소라 noreply 주소로 **저장소 로컬**에 설정했다 — 노트북에서 clone 하면
  전역 설정이 쓰이니 `git config user.email` 확인
- 노트북에서 push 하려면 GitHub 인증이 필요하다 (Git Credential Manager 또는 `gh auth login` → `gh auth setup-git`)
