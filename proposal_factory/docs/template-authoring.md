# 템플릿 작성 가이드 (Template Authoring Guide)

이 문서는 **사내 디자이너·기획자**가 새 페이지 유형을 만들거나 기존 템플릿을 고칠 때 따르는 절차를 정리한다.
엔진은 결정론적으로 동작한다 — PowerPoint에서 도형 이름만 규약대로 부여하고, 레시피 1개를 작성하면 끝.

## 개념 한 장 요약

```
┌───────────────────┐    ┌──────────────────┐    ┌────────────────┐
│  Template PPTX    │    │  Recipe JSON     │    │ Source Slots   │
│  (빈 슬롯들)        │    │  (op 정의)       │    │ (실제 데이터)    │
│  slot:<key> 명명   │    │  slot↔from 매핑  │    │  recipe.from   │
└─────────┬─────────┘    └────────┬─────────┘    └────────┬───────┘
          │                       │                       │
          └───────────────┬───────┴───────────────────────┘
                          ▼
                ┌──────────────────┐
                │  engine.pipeline │  ── classify → transform → lint → route
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │  out.pptx        │  ── 검수 대기(ready_for_review) 또는 사람 승인
                └──────────────────┘
```

세 개 자산만 맞으면 엔진은 알아서 변환·검증·라우팅한다.

---

## 1. PowerPoint에서 슬롯 이름 부여

엔진은 도형의 **이름(name) 속성**으로 슬롯을 식별한다. 도형의 **텍스트**나 **alt text**가 아니다.

1. PowerPoint에서 슬라이드 열기
2. **홈 → 정렬 → 선택 창** (단축키 `Alt+F10` / macOS는 `⌘+F6`로 패널 토글)
3. 이름을 바꿀 도형을 선택
4. 패널에서 도형 이름을 더블클릭하여 `slot:<key>` 형식으로 변경

### 이름 규약

- 형식: `slot:<key>`
- `<key>`: 영문 소문자·숫자·언더스코어 권장 (예: `slot:breadcrumb`, `slot:company_overview`, `slot:history_timeline`)
- 한 슬라이드에 같은 키 중복 금지
- 슬롯 키는 레시피의 `slot` 필드와 **정확히** 일치해야 함

### 예시

| 슬롯 이름 | 도형 유형 | 용도 |
| --- | --- | --- |
| `slot:breadcrumb` | 텍스트 박스 (`<p:sp>`) | 섹션 경로 한 줄 |
| `slot:key_point` | 텍스트 박스 (`<p:sp>`) | 요약/KEY POINT |
| `slot:company_overview` | 표 (`<p:graphicFrame>`) | 회사 개요 표 (헤더 1행 + 본문 N행) |
| `slot:history_timeline` | 빈 직사각형 (`<p:sp>`) | 연혁 그리드 영역 (기준 박스) |
| `slot:certificate_iso` | 빈 도형 (어떤 타입이든) | 인증서 이미지 자리 |

---

## 2. 페이지 유형 분류 시그니처 정의

엔진은 고객 초안의 슬라이드 도형 구성을 보고 어느 페이지 유형인지 결정론적으로 판별한다.
판별 규칙은 `assets/page_types.json` (예시: `page_types.example.json`) 에 등록.

```json
{
  "type": "body_company_overview",
  "desc": "제안사 일반현황(회사개요 표 + 주요연혁 도형)",
  "match": {"n_table_min": 1, "n_year_box_min": 1},
  "recipe": "recipes/body_company_overview.json"
}
```

### `match` 객체의 키

| 키 | 의미 |
| --- | --- |
| `n_table_min` / `n_table_max` | 표(`<p:graphicFrame>` + `<a:tbl>`) 개수 |
| `n_image_min` / `n_image_max` | 이미지(`<p:pic>`) 개수 |
| `n_text_min` / `n_text_max` | 텍스트 박스(`<p:sp>` + texts) 개수 |
| `n_year_box_min` / `n_year_box_max` | 이름에 "연도" 또는 "year"가 포함된 도형 개수 |
| `has_title` | `True` 면 이름에 "title"/"제목" 포함 도형 필수 |

매칭은 **AND 조합**. 모든 조건을 만족해야 해당 유형으로 분류된다.

---

## 3. 레시피 작성

레시피는 슬라이드 한 장에 대해 어떤 슬롯을 어떤 데이터로 어떻게 채울지 결정한다.

### 기본 구조

```json
{
  "type": "body_company_overview",
  "template_slide": "ppt/slides/slide20.xml",
  "ops": [
    {"op": "text_inject", "slot": "breadcrumb", "from": "section_path"},
    {"op": "text_inject", "slot": "key_point", "from": "summary", "rule": "one_line"},
    {"op": "table_rebuild", "slot": "company_overview", "from": "company_fields",
     "style": {"label_col_fill": "F2F2F2", "highlight_fill": "DCEFFE"}},
    {"op": "image_reuse", "slot": "certificate_iso", "from": "images.certificate_iso"},
    {"op": "shape_rebuild", "slot": "history_timeline", "from": "year_bullets",
     "design": {"year_box_fill": "2B8ECB", "year_text_color": "FFFFFF",
                "pointer_fill": "1A3D46", "uniform_gap_emu": 46000, "columns": 2}}
  ]
}
```

### 필드

- **`type`**: 페이지 유형 식별자(page_types 의 type 과 동일)
- **`template_slide`**: PPTX 내부에서 편집할 슬라이드 XML 경로
  - PowerPoint UI상 "N번째 슬라이드"가 반드시 `slide<N>.xml`이 아님 — PPTX를 unzip해서 `ppt/slides/` 디렉터리를 직접 확인할 것
- **`ops`**: 순서대로 실행할 변환 연산 목록

### 4-op 명세

#### (1) `text_inject` — 텍스트 박스 채우기
```json
{"op": "text_inject", "slot": "key_point", "from": "summary", "rule": "one_line"}
```
- `from`: source_slots 내 키 (점 표기 가능: `images.certificate_iso`)
- `rule`:
  - `null`(미지정) — 입력값을 그대로 주입. `\n`은 새 문단(`<a:p>`)으로 분리.
  - `"one_line"` — 모든 공백·줄바꿈을 단일 공백으로 압축 (KEY POINT 등 한 줄 보장).
- 폰트/색/사이즈는 템플릿의 첫 `<a:rPr>` 속성을 그대로 보존.

#### (1b) `group_fill` — 그룹 내부 텍스트박스 채우기
```json
{"op": "group_fill", "slot": "asis_summary", "from": "asis_lines"}
```
- 슬롯이 그룹(`<p:grpSp>`)일 때 사용. `text_inject` 는 단일 텍스트박스(`<p:sp>`) 전용이라 그룹은 못 채운다.
- `from` 값이 배열이면 그룹 내부 텍스트박스를 **문서 순서대로** 채우고, 문자열이면 첫 박스만 채운다(둘 다 **문구 verbatim**).
- 그룹의 아이콘·도형·구조는 보존. 값 개수보다 많은 박스는 원본 유지.

#### (2) `table_rebuild` — 표 본문 재생성
```json
{"op": "table_rebuild", "slot": "company_overview", "from": "company_fields",
 "style": {"label_col_fill": "F2F2F2", "highlight_fill": "DCEFFE"}}
```
- 슬롯은 `<p:graphicFrame>` 안에 `<a:tbl>` 을 가진 도형이어야 함.
- 첫 `<a:tr>`(헤더)은 보존. 본문 행은 입력 배열 길이만큼 재생성.
- 각 행 = 2열(label / value).
- `style.label_col_fill`: 좌측(라벨) 컬럼 채움 #RRGGBB
- `style.highlight_fill`: `highlight: true` 인 행의 값 셀 채움
- 행 높이는 헤더 `h="..."` 값을 그대로 사용.

#### (3) `image_reuse` — 이미지 삽입
```json
{"op": "image_reuse", "slot": "certificate_iso", "from": "images.certificate_iso"}
```
- 슬롯은 어떤 도형이든 가능(`<p:sp>` 빈 박스, 기존 `<p:pic>` 모두 허용).
- 슬롯의 `<a:off>/<a:ext>`을 기본 좌표로 사용. source_slots 에서 x/y/cx/cy 가 있으면 override.
- 자동 작업:
  - 이미지 파일 → `ppt/media/` 복사 (이름 충돌 시 `_1`, `_2` 추가)
  - `ppt/slides/_rels/slideN.xml.rels` 에 image Relationship 추가
  - `[Content_Types].xml` 에 png/jpg/jpeg/gif 확장자 등록 (없을 때만)

#### (4) `shape_rebuild` — 그리드 도형 재구성
```json
{"op": "shape_rebuild", "slot": "history_timeline", "from": "year_bullets",
 "design": {"year_box_fill": "2B8ECB", "year_text_color": "FFFFFF",
            "uniform_gap_emu": 46000, "columns": 2}}
```
- 슬롯은 빈 직사각형(기준 박스 역할). `<a:off>/<a:ext>` 위치/크기를 그리드 배치 영역으로 사용.
- 입력 항목 N개를 `columns`(1 또는 2)로 분배.
- 각 항목당 도형 2개 생성:
  - `year_box_<i>` — 연도 배지 (사각형 + 흰글자)
  - `bullet_<i>` — 설명 텍스트 박스
- 세로 간격 `uniform_gap_emu` 균일.
- 기존 슬롯 도형은 제거됨.

---

## 4. source_slots 데이터 준비

레시피의 `from` 키들에 대응하는 입력 데이터. 스키마: `schemas/source_slots.schema.json`.

```json
{
  "section_path": "1. 제안사 소개 > 1.1 회사 개요",
  "summary": "국내 최대 통신사 제안",
  "company_fields": [
    {"label": "회사명", "value": "주식회사 가나다"},
    {"label": "대표자", "value": "홍길동", "highlight": true},
    {"label": "설립일", "value": "2001-03-15"}
  ],
  "year_bullets": [
    {"year": "2024", "text": "국가고객만족도 14년 연속 1위"},
    {"year": "2023", "text": "ISMS-P 인증 획득"}
  ],
  "images": {
    "certificate_iso": {
      "path": "/abs/path/to/cert.png",
      "x": 6500000, "y": 2600000, "cx": 2400000, "cy": 1800000
    }
  }
}
```

### 키별 규칙

- **`section_path`** (string): `text_inject from="section_path"` 가 받음. 줄바꿈은 그대로 새 문단으로.
- **`summary`** (string): one_line 룰과 함께 쓰는 경우가 일반적.
- **`company_fields`** (array): 표 본문 행. `label` + `value` 필수, `highlight: true` 옵션.
- **`year_bullets`** (array): 그리드 항목. `year` + `text` 필수.
- **`images`** (object): 키 = 슬롯 키. 값 = `{path, x?, y?, cx?, cy?}`. 좌표 미지정 시 슬롯 박스 좌표 사용.

---

## 5. 파이프라인 실행

### Python 코드에서 직접

```python
from engine import pipeline
from engine.config import load_config

cfg = load_config()
assets = {
    "page_types": page_types_list,    # assets/page_types.json
    "base_dir": "assets",             # 레시피 경로 해석 기준
}
job = {
    "id": "proposal-2026-Q2-001",
    "shapes": draft_shapes,           # 고객 초안에서 추출한 도형 리스트(분류용)
    "size": (cx, cy),                 # EMU
    "template_pptx": "/abs/path/template.pptx",
    "source_slots": source_slots,
    "workdir": "/abs/path/workdir",   # 선택. 없으면 임시 디렉터리.
}
man = pipeline.run_job(job, assets, cfg)

# 결과
print(man["page_type"])                       # 분류 결과
print(man["transform"]["out_pptx"])           # 출력 PPTX 경로
print(man["lint"]["verdict"])                 # PASS / FAIL
print(man["status"])                          # ready_for_review / needs_human_approval / ...
```

### 데몬으로

`daemon/watch_inbox.py` 가 NAS의 `/inbox` 를 감시하고 자동 처리. (현재 구현은 골격 — 실제 어댑터 연결은 후속.)

### 출력 검수

`man["status"] == "ready_for_review"` 면 직원이 `man["transform"]["out_pptx"]` 를 검수, 필요시 수정.
수정본은 `learn.diff_shapes()` 로 후보와 비교 → 교정 기록 누적 → 반복 패턴은 디자인 가이드/레시피로 자동 승격.

---

## 6. 슬롯 타입별 PPTX 도형 가이드

새 템플릿 작성 시 각 op이 기대하는 OOXML 구조 요약.

| op | 슬롯 도형 | 필수 내부 구조 |
| --- | --- | --- |
| text_inject | `<p:sp>` 텍스트 박스 | `<p:txBody>` 안 `<a:p>` 최소 1개 |
| table_rebuild | `<p:graphicFrame>` | `<a:graphic>/<a:graphicData>/<a:tbl>` 안에 `<a:tr>` 헤더 1행 + 2열 |
| image_reuse | 어떤 도형이든 | `<a:xfrm><a:off/><a:ext/></a:xfrm>` (위치/크기) — txBody 유무 무관 |
| shape_rebuild | 빈 `<p:sp>` 직사각형 | `<a:xfrm>` (영역 정의) — 내부 도형은 모두 제거되고 재구성됨 |

---

## 7. 신규 페이지 유형 추가 워크플로우

> **슬롯 명명 누락 탐지**: `python3 tools/detect_slots.py <template.pptx>` — `slot:<key>` 로
> 명명되지 않은 **플레이스홀더 텍스트**("…입력하세요"/"제목"/"내용" 등)를 찾아 `slot:title`/`body`/`text*`
> 추천과 함께 보여준다(그룹 내부까지, 고정 디자인·반복 텍스트는 제외). 출력에 플레이스홀더가
> 남지 않으려면 여기 나온 도형을 모두 명명해야 한다. 일반 콘텐츠까지 보려면 `--all`.

> **작성 도우미**: `python3 tools/inspect_template.py <template.pptx> --type <유형>` 를 먼저 돌리면
> 슬라이드별 도형·시그니처를 분석해 `template_slide` 경로, `page_types` match, recipe 골격,
> source_slots 키를 **붙여넣기 가능한 형태로 제안**한다(2·3·4단계 자동화). 빈 박스 슬롯의
> image_reuse↔shape_rebuild 선택과 `from` 키 매핑만 사람이 마무리하면 된다.

1. **디자인**: PowerPoint 에서 새 페이지 디자인 → 모든 변동 요소에 `slot:<key>` 이름 부여
2. **슬라이드 번호 확인**: PPTX 저장 후 unzip → `ppt/slides/` 안에서 슬라이드 XML 번호 확인
   (도우미가 자동 출력. 수동: `unzip -l template.pptx | grep slides/slide`)
3. **유형 등록**: `assets/page_types.json` 에 분류 시그니처 + recipe 경로 추가(도우미 제안 활용)
4. **레시피 작성**: `assets/recipes/<유형>.json` 에 4-op 조합(도우미 골격에서 op·from 마무리)
5. **실 데이터 1건 검증**: 고객 초안 1건을 source_slots 로 변환 → 파이프라인 실행 → 출력 검수
6. **학습 누적**: 직원이 출력본을 수정하면 `learn.py` 가 diff 를 기록 → 반복 패턴은 design_guide/recipe 로 자동 승격

---

## 8. 흔한 함정

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| `MissingSlot: slot:xxx not found` | PPTX 의 도형 이름이 `slot:xxx` 가 아님 | "선택 창"에서 이름 확인 (alt text 가 아님) |
| 슬롯이 그룹 안에 있어도 찾아짐 | 가장 안쪽 enclosing 도형 매칭 | shape_rebuild 의 출력은 그룹 외부에 배치됨 — 권장: 슬롯은 그룹 밖에 |
| 출력에 이미지 없음 | recipe 에 `image_reuse` op 누락 | recipe.ops 에 추가 |
| 표 행이 안 채워짐 | source_slots 의 `from` 키가 array 가 아님 | `company_fields` 가 `[{label, value}, ...]` 형태인지 확인 |
| 표 헤더 잘림 | 헤더 셀이 2개 컬럼이 아님 | template 슬라이드의 표를 정확히 2열로 |
| 텍스트가 빈 줄로 출력 | source_slots 의 키 누락 또는 오타 | `_get_field` 가 None → 빈 문자열 주입 |
| `lint fail overlap` | shape_rebuild 결과 도형들이 다른 도형과 겹침 | `design.uniform_gap_emu` 증가 또는 슬롯 박스 위치/크기 조정 |
| `lint warn nonrender_font` | 공체 등 미렌더 폰트 사용 | 환경 경고일 뿐. 실제 PowerPoint 에서는 정상. design_guide DG007. |
| 한국어 폰트 깨짐 | 슬라이드 마스터/레이아웃 폰트 누락 | 템플릿의 슬라이드 마스터에서 한국어 폰트(`+mj-cs`, `+mn-cs`) 명시 |

---

## 9. End-to-end 따라하기

가장 간단한 살아있는 예제는 자기검증 스크립트.

- **`selfcheck/run_transform_demo.py`** — 합성 미니 템플릿(5슬롯) + 합성 PNG → 4-op 모두 실행 → 출력 검증.
- **`selfcheck/run_pipeline_demo.py`** `[4]` — pipeline.run_job 으로 결선된 전체 흐름.

코드를 그대로 복제하여 실제 템플릿 경로·source_slots 만 바꾸면 시작점이 된다.

---

## 10. 빠른 점검 체크리스트

새 페이지 유형 추가 또는 기존 템플릿 수정 시:

- [ ] PPTX 슬라이드의 모든 변동 도형에 `slot:<key>` 이름 부여 (선택 창에서 확인)
- [ ] `unzip -l template.pptx | grep slide` 로 `template_slide` 경로 확인
- [ ] `page_types.json` 에 `match` 시그니처와 `recipe` 경로 등록
- [ ] `recipes/<type>.json` 작성 — 모든 슬롯이 op 으로 커버되는지
- [ ] `source_slots` 의 키가 recipe `from` 들과 1:1 대응
- [ ] 합성 데이터로 `pipeline.run_job` 한 번 돌려서 `status == ready_for_review` 확인
- [ ] 출력 PPTX 를 PowerPoint 에서 직접 열어 시각 검수
- [ ] 자기검증(`selfcheck/run_pipeline_demo.py`) 회귀 통과 확인

---

## 11. SKB 표준 템플릿 타입 라이브러리

`assets/page_types.skb.json` + `assets/recipes/skb/*.json` — SKB 디자인가이드2.0(53슬라이드,
슬롯 102개)에서 식별한 **22종 타입**의 레시피. 각 레시피는 대표 슬라이드(`template_slide`)와
슬롯별 op(sp→`text_inject`, grpSp→`group_fill`, 표→`table_rebuild`, 이미지→`image_reuse`)로
구성되며, 실제 템플릿 변환으로 전수 검증됐다. 운영 시:
```python
from engine import pipeline
recipes = {r["type"]: json.load(open(f"assets/recipes/skb/{r['type']}.json"))
           for r in json.load(open("assets/page_types.skb.json"))}
assets = {"page_types": json.load(open("assets/page_types.skb.json")),
          "recipes": recipes, "base_dir": "assets"}
res = pipeline.run_deck(draft_slides, assets, cfg, gateway,
                        std_template_pptx="<디자인가이드2.0.pptx>",
                        forced_types={0: "asis_tobe", 1: "section_title", ...})  # 운영자 페이지별 지정
```
주요 타입: cover·toc·part_divider·section_title·asis_tobe·asis_tobe_summary·body_block·
body_with_head_(text|block)·list_two_col·list_full_two_col·table_pair_body·device_table_(image|dual)·
device_title_table·action_grid_4·two_items·lead_(body|title)·eval_keypoint·closing_messages(2|3).

> 미해결: `match` 는 비어 있음(운영자 페이지별 지정 사용 — 시그니처로는 grouped 텍스트 타입 구분 불가).
> `group_fill` 슬롯의 초안→배열 자동 매핑은 미구현(현재 text_inject 슬롯만 verbatim 자동, 그룹은 사이드카/명시).

## 12. 참고

- 코드: `engine/transform.py`, `engine/pipeline.py`
- 스키마: `schemas/source_slots.schema.json`, `schemas/page_recipe.schema.json`, `schemas/job_manifest.schema.json`
- 예시 자산: `assets/page_types.example.json`, `assets/recipes/body_company_overview.json`, `assets/design_guide.example.json`
- 자기검증: `selfcheck/run_transform_demo.py`, `selfcheck/run_pipeline_demo.py`
