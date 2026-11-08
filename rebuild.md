0. 환경·구조
Python 3.12, 프로젝트 .venv. requirements.txt: cadquery>=2.8,<3, pytest>=9 (ezdxf 는 cadquery 의존성. 원본 환경: cadquery 2.8.0 / OCP 7.9.3.1 / ezdxf 1.4.5 / pytest 9.2.0)

단위 mm. 좌표: 지그 윗면 z=0, 아래가 -z. PCB 는 지그를 향하는 면(아랫면)이 z=0 이 되도록 정렬.

설정 config.toml (stdlib tomllib). 수치 하드코딩 금지, 근거 없는 값은 # [가정] 주석.

.gitignore: .venv/ __pycache__/ .pytest_cache/ *.bak* out/ .claude/settings.local.json samples/kicad/


jigauto/__init__.py  (빈 패키지, docstring 만)

jigauto/pcb.py       STEP → PcbModel

jigauto/dxf_input.py 고객 2D 도면 DXF → PcbModel

jigauto/rules.py     PcbModel + cfg → Feature 목록 + 경고

jigauto/jig.py       Feature → 지그 솔리드, 간섭 검증, 내보내기

jigauto/drawing.py   AutoCAD 용 DXF 도면

jigauto/__main__.py  CLI

samples/make_phone_pcb.py      (가상) 휴대폰 메인 PCB STEP

samples/make_ecad_like_pcb.py  (가상) ECAD 구조 흉내 STEP

samples/make_phone_dxf.py      (가상) 고객 2D 도면 DXF 4종

samples/kicad_to_step.py       KiCad 보드 → STEP 래퍼

samples/spec_phone_main.toml   (가상) 설계사양서 = 모델별 설정 파일

tests/conftest.py, tests/test_pipeline.py

1. 데이터 모델 (pcb.py)
클래스

필드

Hole

x, y, d

Protrusion

xmin, ymin, xmax, ymax, height — 기판 아랫면(z=0) 아래로 나온 솔리드의 XY bbox 와 돌출 높이

PcbModel

shape (정렬된 PCB 전체, 간섭 검증용), outline (기판 외곽 Wire, z=0), thickness, holes, protrusions, notes (입력 보정 내역 → 경고로 출력)

Feature (rules.py)

kind, rule_id, reason (사람이 읽는 근거 문장), params (dict)

모듈 상수 TOL = 1e-3.

2. STEP 입력 load_pcb(path, face_down, board_max_thickness) -> PcbModel
STEP 을 읽어 모든 형상을 Compound 하나로.

face_down == "top" 이면 원점 기준 X축 180° 회전. "bottom" 이 아니고 "top" 도 아니면 ValueError.

셸 → 솔리드 보정 _shells_to_solids: 솔리드에 속하지 않은 셸을 탐색(OCP TopExp_Explorer(shape, SHELL, avoid=SOLID)).
닫힌 셸(BRep_Tool.IsClosed) → Solid.makeSolid, 열린 셸 → 그 bbox 크기의 박스 솔리드. 하나라도 있으면
기존 솔리드 + 변환분으로 Compound 재구성, note: "솔리드가 아닌 셸 부품 {N}개 — 닫힌 셸 {a}개는 솔리드로 변환, 열린 셸 {b}개는 바운딩박스로 대체".
(셸은 부피가 없어 간섭 검증에서 안 보인다 — CLAUDE.md Constraint)

기판 = bbox 높이(zlen) ≤ board_max_thickness 인 솔리드 중 XY 면적(xlen·ylen) 최대. 없으면 ValueError "기판 미검출: 두께 …mm 이하 솔리드가 없음 (경로)".

기판 zmin 이 0 이 되도록 전체 이동. thickness = 기판 zlen.

아랫면 = 기판 면 중 평면 · 법선 z < -0.99 · 중심 |z| < TOL 인 것 중 면적 최대. 없으면 ValueError.

홀 = 아랫면 innerWires 중 모든 edge 가 CIRCLE 이고 반지름·중심이 TOL 안에서 같은 wire → Hole(중심, 2r). (사각 개구부는 홀 아님)

돌출 = 기판 외 솔리드 중 bbox.zmin < -TOL → Protrusion(bbox XY, height = -zmin).

outline = 아랫면 outerWire.

3. 규칙 (rules.py) — 함수 하나 = 규칙 하나, reason 필수
apply_rules(pcb, cfg) -> (features, warnings):

warnings 시작 = pcb.notes 복사. 돌출 0건이면 "기판 아래 돌출 부품 0건 — 회피 포켓 없음. 안착 방향(face_down) 확인 필요" 추가.

rel = merge_reliefs(reliefs(...), merge_gap) → pins → support → wall.

반환 순서: [base, seat_pocket, *rel, *pins, support_feat, wall_feat], warnings + pin경고 + R6경고 + R7경고.

규칙

kind

내용 / params

R1 seat_pocket

seat_pocket

PCB 외곽 + outline_clearance 간격, 깊이 pocket_depth. params {clearance, depth}

R2 reliefs

relief

돌출마다 bbox ± comp_clearance, depth = pocket_depth + height + depth_clearance. params {xmin,ymin,xmax,ymax,depth}

R5 merge_reliefs(rel, gap)

relief

(기준 상태 = bbox 병합) 아래 참조

R3 pins

pin

아래 참조. params {x, y, d, z_top}

R4 base

base

아래 참조. params {xmin,ymin,xmax,ymax,zmin}

R6 support

support_check

받침 비율 검사 (형상 아님)

R7 wall

wall_check

안착 벽 손실 검사 (형상 아님)

jig.py·drawing.py 는 kind 로 필터하므로 support_check/wall_check 는 형상·도면에 영향 없이 report 에만 들어간다.

R5 (기준 상태): 각 포켓을 그룹으로 시작. 두 그룹 bbox 의 분리거리 = max(sep_x, sep_y) (sep_x = max(xmin들) − min(xmax들), y 동일)
가 gap 미만이면 합친다. 합칠 때마다 처음부터 다시 검사(커진 그룹이 다른 포켓과 가까워짐, 연쇄). 그룹 크기 1 은 원래 Feature 그대로,
2 이상이면 R5 Feature: 그룹 bbox, depth = 최대값, params 에 "merged": 개수, reason "인접 회피 포켓 N개 병합 — 사이 벽 {gap}mm 미만은 제작 불가, 깊이는 최대값 …".
벽 = 정확히 gap 이면 병합 안 함.

② 에서 이 함수를 "얇은 벽 자리만 브리지 + 단차 깊이" 로 바꾼다 → HANDOFF.md 2절.

R3 pins: 후보 = hole_min_d ≤ d ≤ hole_max_d 이고 어떤 relief 사각형 안에도 있지 않은 홀. 후보 < 2 → 핀 없음 + 경고
"툴링 홀 후보 {n}개 (<2) — 위치결정 핀 생략, 외곽 포켓만으로 위치결정". 아니면 거리 최대인 두 홀. d = 홀 d − pin_clearance,
z_top = −pocket_depth + thickness + pin_protrusion.

R4 base: 외곽 bbox 와 모든 relief 사각형을 포함하는 범위 ± wall_margin. zmin = −(max(pocket_depth, relief 깊이들) + min_floor).

R6 support: board = 외곽 wire 로 만든 면. hollow = board ∩ relief 사각형들 면적 (기판 밖으로 나간 부분은 안 셈).
ratio = 1 − hollow/board 면적. params {board_area, unsupported_area, ratio, min_ratio}. reason "기판 면적 …mm² 중 회피 포켓 위 …mm² 는 받침 없음 → 받침 NN% (기준 NN% 이상)".
ratio < min_support_ratio 이면 경고 1개: "PCB 받침 {ratio:.0%} < 기준 {need:.0%} — 가장 큰 회피 포켓 {rule_id} {W:.1f}x{H:.1f}mm (x …, y …). SMT 인쇄·실장 압력에 PCB 처짐 위험 → 병합 포켓·부품 배치 확인 필요" (가장 큰 = 사각형 면적 최대).

R7 wall: 벽선 seat = 외곽.offset2D(outline_clearance, “arc”). t = 1e-3, c = comp_clearance. relief 마다:
cut = seat ∩ (사각형을 t 만큼 줄인 면) 의 edge 길이 합 (벽선에 딱 맞닿은 포켓은 0). cut > 0 이면 원인 판정:
사각형을 c+t 만큼 줄인 면(= 부품 자체)이 벽선과 교차하면 “part”(불가피) 아니면 “clearance”.
경고 "안착 포켓 벽 {cut:.2f}mm 없음 — {rule_id} 회피 포켓 (x a..b, y c..d): " + part 면 "부품이 기판 외곽 밖으로 나와 불가피. 그 구간 위치결정 확인 필요",
clearance 면 "부품은 기판 안쪽이나 여유 {c}mm 가 벽을 넘음(둥근 모서리 등)". params {perimeter, lost, lost_by_part, lost_by_clearance}.

4. 지그 형상·간섭 (jig.py)
build_jig(pcb, feats): 몸체 = base 범위 박스 (z: zmin..0). 빼기: 안착 포켓(외곽.offset2D(clearance,”arc”) 면을 −depth 로 압출) +
relief 마다 박스(z: −depth..0). 핀: 원기둥, 시작 z0 = −seat_depth − 1.0 (바닥 속 1mm 부터 → 확실히 한 덩어리), 끝 z_top. fuse 후 clean.

placed_pcb = pcb.shape 를 −pocket_depth 이동. interference = jig ∩ placed_pcb 부피.

export_all: out 폴더에 jig.step, jig.stl(tolerance = export.stl_tolerance), jig.dxf(drawing). 경로 dict 반환.

5. CLI (main.py)
python -m jigauto build <input> [--out out] [--config config.toml] [--view N] (기본 config = 패키지 상위의 config.toml)

입력 확장자 .dxf → load_dxf(path, cfg, view), 그 외 → load_pcb.

출력: [OK] PCB 두께 …, 홀 N개, 기판 아래 돌출 N건 → 경고마다 [WARN] … → 지그 생성·간섭·내보내기.

report.json (UTF-8, ensure_ascii=False, indent 2): input, face_down, counts{holes, protrusions, reliefs, pins, solids},
interference_mm3, jig_volume_mm3, features(asdict), warnings, outputs.

간섭 > 1e-6 mm³ 또는 솔리드 ≠ 1 → [ERROR] … 반환 1. 성공 [OK] 회피 포켓 N개, 핀 N개, 간섭 0 → out 반환 0.

ValueError 는 [ERROR] {메시지} 출력 후 반환 1.

6. 도면 (drawing.py) write_drawing(jig, pcb, feats, cfg, path, source)
cadquery DxfDocument(setup=True). 레이어/색: OUTLINE 7, SEAT 3, RELIEF 1, PIN 5, SECTION 7, CENTER 2, DIM 4, TEXT 7, TITLE 7.
문자 스타일 “KOR” = malgun.ttf. 치수 스타일 “EZDXF”: dimtxt = h, dimasz = 0.8h, dimdec 2, dimlfac = 1.0 (기본 100 → 필수 수정, CLAUDE.md). h = drawing.text_height.

평면도: base 사각형(OUTLINE), 안착 포켓 선(SEAT), relief 사각형(RELIEF) + 중앙에 깊이 태그 D1..Dn (깊이 오름차순 번호, 글자 크기 min(h, 0.6·짧은 변)),
핀 원 + "Ø{d:.2f} PIN" 문자.

치수: 핀마다 X(외곽 좌상 기준, base ymax 위로 단계 배치)·Y(오른쪽, 90°) 각 1, 전체 폭·전체 깊이, 핀이 2개면 핀 간 aligned 치수,
단면도에 전체 높이·가장 깊은 포켓 깊이. 치수 개수 = 2·핀 + 2 + (핀==2) + 2.

단면 A-A: 가장 깊은 relief 중심 y 의 XZ 단면(jig ∩ 평면) → X축 −90° 회전해 평면도 아래(sec0 = base ymin − 8h)에 배치(SECTION).
평면도에 CENTER 선(linetype CENTER) + 양끝 “A”, "단면 A-A" 문자.

깊이표+주기(평면도 오른쪽): "깊이표 (지그 윗면 기준)", 안착 포켓 깊이, Dn  깊이(소수 2자리)  (개수), 주기 1~6(단위·척도, 공차 주기,
클리어런스 3종, 핀 개수·핀 직경 규칙, 전체 높이·최소 바닥, “jigauto 자동 생성 도면 — 출도 전 검토 필수”).

표제란(단면도 아래 오른쪽): 회사·품명·도번·모델·재질·제작·척도 1:1·단위 mm·작성일 YYYY-MM-DD (KST)·원본 파일명. 2열 표.

7. 2D 도면 입력 (dxf_input.py) load_dxf(path, cfg, view=None) -> PcbModel
원칙: 레이어 이름을 보지 않는다 (형상으로 분류). 애매하면 포켓이 생기는 쪽, 뷰가 애매하면 ERROR (폴백 금지).
face_down 이 “bottom” 이 아니면 ValueError.

읽기: modelspace 를 INSERT 재귀 분해(virtual_entities + attribs). TEXT/ATTRIB/MTEXT 는 (문자, 위치) 로 수집.
무시: TEXT, MTEXT, DIMENSION, LEADER, MULTILEADER, HATCH, ATTRIB, ATTDEF, POINT, SOLID, WIPEOUT, IMAGE, VIEWPORT, MLINE, XLINE, RAY, TOLERANCE.
닫힌 LW/POLYLINE·CIRCLE → 닫힌 루프. LINE·ARC·열린 폴리라인 → 낱개 edge. 그 외(ELLIPSE, SPLINE…) → 종류별 개수 → note "읽지 않은 개체 {…} — 이 형상은 분류에서 빠짐".
edge 변환은 OCS→WCS (돌출 방향 −Z 호 대응): ARC 는 시작·중간·끝 3점 호, CIRCLE 은 원, 길이 0 LINE 은 버림.
루프 = wire 로 면을 만들 수 있는 것(실패하면 열린 선). 루프 객체: wire, face, area, bbox, circle(단일 원이면 (cx, cy, d)).
holds(o) = bbox 포함(tol) + 면적이 작고 교집합 면적 ≥ o 면적·(1−1e-6). 비교는 객체 동일성(dataclass eq=False).

낱개 선 잇기 _chains: 끝점을 join_tol 격자로 묶어 노드화(인접 3x3 셀 검색), 같은 선 중복 제거(양끝 노드+중점 키),
union-find 로 연결 덩어리. 모든 노드 차수가 2 인 덩어리만 닫힌 루프, 나머지는 열린 선. (전부 한꺼번에 이으면 표제란·테두리가 엉뚱한 고리 — CLAUDE.md)

테두리 제거: 큰 루프부터, 직사각형(면적 = bbox 면적)이고 나머지 모든 루프·열린 선을 감싸면 테두리로 제거, 반복(이중 테두리).

부모: 각 루프를 감싸는 가장 작은 루프.

기판 후보: 부모 없음 + 자식 있음 + 짧은 변 ≥ min_board_size + 자식 면적 합 < tile_ratio·면적(표제란 제외). x, y 순 정렬, 1번부터 번호.

뷰 이름: 문자 대문자화, bottom_labels / top_labels 부분 일치 → 가장 가까운 후보(bbox 거리)에 태그.

선택 _pick: 후보 0 → ValueError "기판 미검출: …". view 지정 → 그 번호(범위 밖 ValueError). bottom 태그 1개 → 그것.
bottom 태그 없음 + 후보 1개 + top 태그 아님 → 그것 + note. 그 외 → ValueError "Bottom 뷰를 정할 수 없음 (…). --view 번호로 지정: 1번 x …; 2번 …".
Top 뷰 = top 태그 후보가 정확히 1개일 때.

좌표 변환 _to_jig: mirrored 면 YZ 평면 거울 후 (bb.xmax, −bb.ymin) 이동, 아니면 (−bb.xmin, −bb.ymin). 결과 = 위에서 본 배치, 기판 좌하단 원점.

반전 대조 _check_mirror: Top 뷰 없음 → note("Bottom 뷰 좌우 반전 설정(bottom_view_mirrored=…)을 대조할 Top 뷰 없음 — …[가정]", 문구에 "반전" 포함), 대조 실패 표시.
있으면 기판면(외곽−지름 ≤ max_hole_d 원) 을 설정대로/반대로 변환해 Top 뷰 면과 대칭차 면적/Top 면적 비교:
설정대로 ≤ mirror_tol < 반대 → 성공. 반대만 맞으면 ValueError "Bottom 뷰 좌우 반전 설정이 도면과 반대: …". 둘 다 애매 → note.

원 분류 _split_circles: 기판 자식 원 중 홀 선택. Top 대조 성공 시: Top 뷰 같은 지그 위치·같은 지름(join_tol) 원이 있으면 홀, 없으면 원형 부품.
Top 없으면 지름 ≤ max_hole_d 를 홀 + note "기판 안 원 N개를 지름 …mm 이하라 홀로 처리 — Top 뷰 대조 불가로 원형 부품과 구분 못 함. …".
사각형 개구부는 이 대조를 하지 않고 부품으로 둔다 (양면 실드캔 오인 방지).

부품 = 기판 자식 중 홀 아닌 루프(원형 부품 포함; 손자 루프 = 실드캔 벽선·1번 핀 표시는 무시) + 기판 외곽에 걸친 루프
(기판이 아니고, 부모가 기판·부품이 아니고, 기판을 감싸지 않고, 기판과 겹침 > 0) → note "기판 외곽에 걸친 부품 외곽 N개 — …".
각 부품 → 지그 좌표 bbox, height = default_height 의 Protrusion + 박스 솔리드(z −h..0).

기판 솔리드 = (외곽 − 홀) 면을 board_thickness 만큼 +z 압출. shape = 기판 + 부품 박스 Compound.

기판 bbox 안에 완전히 들어간 열린 선 → note "기판 영역에 닫히지 않은 선 N개 (예: …) — 끊긴 부품 외곽이면 회피 포켓 누락. …".

항상 note "2D 도면 입력: 기판 두께 {t}mm, 부품 높이 {h}mm 일괄 적용 (도면에 없음) [가정]".

8. 설정 파일
config.toml (전부 [가정]):

[pcb] face_down “bottom”, board_max_thickness 3.2

[jig] wall_margin 10, pocket_depth 1.0, outline_clearance 0.2, comp_clearance 0.5, depth_clearance 0.5, min_floor 2.0, merge_gap 1.0, min_support_ratio 0.7

[pins] hole_min_d 2.0, hole_max_d 4.0, pin_clearance 0.1, pin_protrusion 1.0

[drawing2d] board_thickness 1.0, default_height 1.5, bottom_view_mirrored true, join_tol 0.01, max_hole_d 6.0, min_board_size 5.0, tile_ratio 0.9, mirror_tol 0.01,
bottom_labels [“BOTTOM”,”BOT”,”1차",”B면",”SIDE B”,”하면"], top_labels [“TOP”,”2차",”A면",”SIDE A”,”상면"]

[export] stl_tolerance 0.01

[drawing] company "한국성전", part_name “PCB 안착 지그”, drawing_no/model/material/process "미지정", tolerance_note "지시 없는 치수 공차: 미지정", text_height 2.5

samples/spec_phone_main.toml (가상 설계사양서 = 같은 키 구조 + [spec] 설명 섹션):

[spec] customer “S사 휴대폰 사업부 (가상)”, product "휴대폰 메인 PCB (가상 SAMPLE)", purpose, pcb_size, pcb_thickness, tooling_hole (설명용)

[pcb] bottom, 1.2 · [jig] wall_margin 15, pocket_depth 0.6, outline_clearance 0.10, comp_clearance 0.30, depth_clearance 0.30, min_floor 3.0, merge_gap 1.0, min_support_ratio 0.7

[pins] 1.9 / 2.1 / 0.05 / 0.5 (툴링 Ø2.0 만, 스크류 Ø1.6 제외)

[drawing2d] config 와 같되 board_thickness 0.8, default_height 1.5

[drawing] company "한국성전", part_name “PCB 안착 지그 (2차면 SMT)”, drawing_no “KSJ-SAMPLE-0001”, model “PHONE-MAIN-SAMPLE (가상)”, material “ESD-PETG”, process “3D 프린팅 (FDM)”, tolerance_note "지시 없는 치수 공차 ±0.1", text_height 2.5

9. 가상 샘플 (테스트 기대값이 이 좌표에 의존 — 정확히 재현)
9.1 휴대폰 메인 PCB make_phone_pcb.py
두께 T = 0.8. 외곽 폴리라인 (0,0)(70,0)(70,30)(55,30)(55,52)(0,52), 세로 모서리 fillet R1.0.

홀: 툴링 Ø2.0 at (3,3)(67,3)(3,49), 스크류 Ø1.6 at (52,27)(30,49). 카메라 개구부 12x10 사각, 중심 (14,43) 관통.

부품 (x0,y0,x1,y1,높이,번호). 윗면은 z=T 위로, 아랫면은 z=−h..0 박스.

TOP: (25,8,47,30,1.1,SH2) (5,30,20,37,1.0,SH3) (30,40,38,43,0.7,J1) (58,10,66,13,0.9,J2)

BOTTOM: (30,8,48,22,1.0,SH1) (8,30,14,32.5,0.6,J3) (58,15,68,27,1.4,J5) (58,4,60,6,1.5,CL1) (45,45,47,47,1.5,CL2)

이어서 i=0..7, x=5+2i: 0402 = (x, y, x+1.0, y+0.5, 0.35, “R{2i+k+1}”) for k,y in (8,10); 0201 = (x, y, x+0.6, y+0.3, 0.30, “C{2i+k+1}”) for k,y in (13,15)
(부품 순서: 5개 다음 i 마다 R 2개, C 2개 — 총 37)

모듈이 T, TOOLING, SCREW, TOP_PARTS, BOTTOM_PARTS, make_board(), make_phone_pcb(path) 를 공개 (DXF 생성기가 재사용).

9.2 ECAD 구조 흉내 make_ecad_like_pcb.py
같은 외곽·툴링·스크류 + THT 홀 Ø0.9 at (34,47)(36,47) + 비아 Ø0.3: (50+i, 32+j) i<5, j<19 와 (22+i, 35+j) i<7, j<16 (총 207) + 카메라 개구부.
부품 로컬 좌표(실장면 z=0, XY 중심 원점), 아랫면은 X축 180° 뒤집어 배치(ECAD 방식), cadquery Assembly “PCB_ASSY”(PCB + “COMPONENTS” 하위, 부품마다 이름·위치):

chip(l,w,h) = 본체 + 양끝 단자(길이 0.2l) 3솔리드 · sot23 = 본체 2.9x1.3 (z 0.1..1.0) + 리드 3 · b2b(l,w,h,n) = 본체(z 0.05..h) + 핀 2n

shield(l,w,h) = 바닥 열린 캔(벽 0.1, 천장 0.1) · closed_shell = 박스 6면 셸 · open_shell = 아랫면(법선 z<−0.5) 뺀 셸

tht_conn = 본체 4x2x1.0 + 핀 Ø0.5 두 개 (x ±1, z −2.3..1.0)

배치: 윗면 U1 shield(22,22,1.1)@(36,19), U2 shield(15,7,1.0)@(12.5,33.5), J1 b2b(8,3,0.7,10)@(34,41.5), J2 b2b(8,3,0.9,6)@(62,11.5) 90°, J3 tht_conn@(35,47);
아랫면 SH1 shield(18,14,1.0)@(39,15) z=0.05(파묻힘), J4 b2b(6,2.5,0.6,8)@(11,31.25) 90°, J5 closed_shell(10,12,1.4)@(63,21), CL1 open_shell(2,2,1.5)@(59,5),
CL2 open_shell(2,2,1.5)@(46,46), Q1 sot23@(25,20), i=0..7 x=5+2i: chip(1.0,0.5,0.35)@(x+0.5, y+0.25) y∈(8,10), chip(0.6,0.3,0.30)@(x+0.3, y+0.15) y∈(13,15)

9.3 고객 2D 도면 make_phone_dxf(path, kind="clean", broken=False, round_part=False)
ezdxf.new(setup=True). 테두리 0..420x297 + 10..410x10..287, 표제란 290..410 x 10..50 + 칸 40x20 6개(회사/품명/도번/모델/척도/단위 문자, 값은 “(가상)”/”SAMPLE” 포함), 주기 문자 “(가상 SAMPLE) 실제 고객 도면 아님”.

뷰 위치: TOP_AT (40,170), BOT_AT (180,170), SIDE_AT (40,120). 기판 폭 W = 70.

Bottom 뷰 = make_board() 아랫면(외곽+홀+개구부)을 YZ 거울 후 (BOT_AT.x + 70, BOT_AT.y) 이동. 부품 x 는 bx(x) = 180 + 70 − x 로 반전.
BOTTOM_PARTS 사각형, 부품번호 문자(높이 0.3), SH1 안쪽 벽선(0.3 안쪽 사각형), SIM 1번 핀 원 (bx(67), by(26)) r 0.25, 기판 폭 선형 치수.
round_part 면 원 Ø3.0 중심 (40,35) 를 Bottom 뷰에만.

kind별 레이어: clean = OUTLINE/HOLE/COMP_BOT/COMP_DETAIL/TEXT/FRAME/DIM; messy = 외형선, 0, [Bot부품, COMP-B, bottom_comp, 0, 외형선] 순환, 문자, TITLE, 치수 + 수동소자(R/C)는 블록 INSERT;
layer0·unlabeled = 전부 “0” + 모든 사각형·외곽을 낱개 LINE/ARC 로, Bottom 외곽의 첫 LINE 끝을 0.005 짧게(미세 틈).

뷰 이름: clean [“BOTTOM VIEW”], messy [“TOP VIEW”, “BOTTOM VIEW (1차면)”], layer0 [“TOP VIEW”, “BOTTOM VIEW”], unlabeled [“VIEW A”, “VIEW B”]. 문자는 뷰 원점 y−12.

clean 외: Top 뷰(반전 없음, TOP_PARTS 사각형·번호) + 측면 뷰(기판 70xT 사각형, 윗면 부품 높이, 아랫면 처음 5개 부품) + “SIDE VIEW”.

broken=True 면 J5(SIM) 사각형 한 변을 빼고 3변만.

main: samples/phone_main_{clean,messy,layer0,unlabeled}.dxf 생성.

9.4 합성 PCB (tests/conftest.py)
기판 60x40x1.0 세로 모서리 R3, Ø3 홀 (5,5)(55,35)(30,5) + Ø0.5 비아 (30,20). 윗면 부품 박스 (20,15)-(30,25) z 1..3.
아랫면(옵션): A (10,20)-(18,26) z −1.5..0, B (40,10)-(45,15) z −3..0, THT 리드 원기둥 r0.4 길이 5 중심 (25,30) z −2..3.
fixture: cfg = config.toml, sample_step = tmp 에 생성. ROOT = 프로젝트 루트, make_pcb(path, with_bottom_parts=True).

9.5 KiCad (선택)
samples/kicad_to_step.py <board> <out> / --demo stickhub/StickHub.kicad_pcb samples/kicad/stickhub.step. KiCad 10 위치 %LOCALAPPDATA%\Programs\KiCad\10.0 (환경변수 KICAD_HOME 우선).
보드 사본에서 ${KISYS3DMOD}·${KICAD\d*_3DMODEL_DIR} 를 3D 모델 절대경로로, .wrl 을 .step 으로 치환 후 kicad-cli pcb export step --force -o out copy.
kicad-cli 직접 호출 금지(3D 모델 경로 미해결 — CLAUDE.md). 테스트는 이 파일에 의존하지 않음.

10. 테스트 18개 (합격 기준)
테스트

기대값

test_parse

두께 1.0, 홀 d [0.5,3,3,3], 돌출 높이 [1.5,2.0,3.0]

test_rules

relief 깊이 = 1.0+h+0.5 (h=1.5,2,3), 핀 (5,5)(55,35), 모든 feature 에 reason·rule_id, 경고 0

test_merge_reliefs

포켓 x(0..1 d1.0)(1.5..2.5 d2.0)(3..4 d1.5)(5.5..6 d0.8), y 0..1, gap 1 → 2개: R5 (0..4, depth 2.0, merged 3) + R2 xmin 5.5. 벽 = 정확히 1 이면 병합 안 함

test_support_check

포켓 없음 ratio 1. 사각형 (−5,5)-(65,35) → board_area = 2400 − 4(9 − 9π/4), unsupported 1800, 경고에 “PCB 받침 25%”·”70.0x30.0". (0,0)-(30,20)+(30,20)-(60,40) → unsupported 1200 − 2(9 − 9π/4), 경고 1

test_wall_check

y 10..20 사각형: x 2..10 + −0.2..10 → lost 0, 경고 0. −0.3..10 → lost_by_clearance≈10, "여유 0.5mm 가 벽을 넘음". −5..10 → lost_by_part≈10, "불가피"

test_jig_no_interference_and_seated

솔리드 1, 간섭 < 1e-6, (50,30,−d−0.01) 재료, (50,30,−d/2) 빈 공간, zmin = −(1.0+3.0+0.5+2.0)

test_face_down_top

돌출 높이 [2.0, 2.0], 간섭 0

test_no_protrusion_warns

"돌출 부품 0건" 경고

test_cli

종료 0, 4개 파일 크기 > 0, counts {holes 4, protrusions 3, reliefs 3, pins 2, solids 1}, STEP 재로드 솔리드 1, 도면 검사

(도면 검사 _check_drawing)

audit 오류 0. 치수 블록에 찍힌 MTEXT 문자 = round(측정값,2). 치수 개수 공식, 전체 폭·핀 간 거리·전체 높이 치수 존재, 모든 깊이 문자, “(KST)” 로 끝나는 문자, SECTION 레이어 존재

test_ecad_like_structure

spec 설정. 두께 0.8, 홀 214, 경고에 "셸 부품 3개", relief 10개, (57.7,14.7) depth 2.3, (57.7,3.7) 2.4, (29.7,7.7) 1.85, (9.45,27.95) xmax 12.55, (33.45,46.45) 2.4, 핀 (3,49)(67,3), 솔리드 1, 간섭 0

test_dxf_input_matches_step[clean/messy/layer0]

홀 = STEP 경로와 동일. relief 박스 = STEP 결과 ∪ {(7.7,37.7,20.3,48.3)} (카메라 개구부 → 무해한 추가 포켓). 핀 (3,49)