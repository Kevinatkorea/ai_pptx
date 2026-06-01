# Proposal Factory — 제안서 템플릿 변환 지속학습 시스템

맥미니가 상시 '학습 공장'으로 가동되며, 고객 초안을 사내 표준 템플릿에 반영하고,
페이지 레이아웃 결함을 누적 학습한다. **AI를 쓸 때와 못 쓸 때(보안망) 모두** 동작하도록
"규칙을 만드는 공장(AI)"과 "규칙을 적용하는 엔진(결정론)"을 분리한 구조다.

## 아키텍처 요약
- **학습 공장(AI, 비보안망)**: 템플릿·샘플 분석 → 자산(레시피·디자인 가이드) 생성.
- **현장 엔진(결정론, 보안망)**: 자산만으로 분류→변환→검증→라우팅. 런타임 AI 호출 0.
- **4대 지속 자산**: Template Profile / Page-Type Library + Recipes / Design Guide / Defect Library.
- **3단 지능**: 결정론(무료) → 로컬 LLM Ollama(무료) → Claude API(월 $50 상한, 신규/모호/레시피 작성만).

## 폴더 구조
```
proposal_factory/
  config.json            엔진 설정(NAS·AI예산·렌더·린터 임계값·알림·보관)
  engine/
    geometry.py          슬라이드 XML → 도형 bbox·문단·표높이 추출
    linter.py            레이아웃 린터 v1 (잉크 박스 기반 5종 검사)
    classify.py          페이지 유형 분류(결정론 + LLM 폴백)
    transform.py         레시피 적용(text_inject·group_fill·table_rebuild·image_reuse·shape_rebuild + 다중 슬라이드)
    capture.py           검증 직전 캡처(PNG + 기하 스냅샷)
    learn.py             직원 수정 diff → 교정 기록(학습)
    pipeline.py          오케스트레이터(ingest→classify→transform→capture→lint→route)
    ai.py                3단 지능 게이트웨이 + 월 예산 상한
    notify.py            검수/승인 메일(직링크)
    pptx_io.py           unpack/pack/clean/render 래퍼
  schemas/               JSON Schema 6종(자산·잡·교정기록)
  assets/                자산 예시(page_types, recipe, design_guide=실제 결함 7종 시드)
  selfcheck/             자기검증 5종(린터·파이프라인+학습·transform·web UI·AI 게이트웨이)
  daemon/watch_inbox.py  /inbox 폴더 감시(launchd 상시 가동) + 수동 실행
  adapters/              비-PPTX 입력 어댑터 레이어(PDF/HWP/HWPX/텍스트 → source_slots; 외부 의존 lazy)
  docs/template-authoring.md  템플릿 작성 가이드(슬롯 명명·레시피·source_slots·트러블슈팅)
  web/                   FastAPI 검수·승인·학습 UI (외부 의존성 사용, engine 과 분리)
    app.py               FastAPI 라우트 9개(목록·상세·승인·수정본 업로드)
    store.py             파일 기반 job 저장소
    preview.py           soffice+pdftoppm 폴백 렌더(선택)
    templates/           Jinja2 HTML 템플릿
  requirements-web.txt   웹 UI 의존성(fastapi·uvicorn·jinja2·python-multipart·httpx)
```

## 템플릿 작성 가이드
새 페이지 유형 추가나 기존 템플릿 수정 절차는 [`docs/template-authoring.md`](docs/template-authoring.md) 참조.
요약: PowerPoint "선택 창"에서 도형 이름을 `slot:<key>` 로 부여 → `assets/page_types.json` 에 분류 시그니처 등록 → `assets/recipes/<type>.json` 작성 → `source_slots` 데이터와 함께 `pipeline.run_job()` 실행.

## 설치 (맥미니, Apple Silicon · 최신 macOS)
1. Python 3.11+, LibreOffice, poppler(pdftoppm) 설치.
2. **공체 등 템플릿 폰트** 설치(렌더 검증 정확도 핵심).
3. NAS(시놀로지 DS220+) SMB 마운트: `\\192.168.0.72\work` → `/Volumes/work`,
   `config.json`의 `nas.mount`을 `/Volumes/work/proposal_factory`로.
4. PPTX 도구 vendoring: 검증된 unpack/pack/clean 스크립트를 `scripts/`에 동봉하고
   환경변수 `PPTX_TOOLS` 지정.
5. 사내 LAN 접속(옵션) → DHCP 예약 후 `http://proposal.local:8000`.
6. 엔진 데몬: `daemon/watch_inbox.py`를 launchd 서비스로 등록(상시 가동).
7. **원격 접속(외부 직원·고정 IP 없음)**: `cloudflared`로 named tunnel 구성 → 자사 도메인(`https://proposal.회사도메인.com`) + Cloudflare Access 인증. 아래 '원격 접속' 절 참고.

## 원격 접속 (외부 직원 · 윈도우 · 고정 IP 없음)
직원은 고객사 등 외부에서 윈도우 PC로 접속하며, 대기업 보안정책상 VPN 클라이언트 설치가
막힐 수 있다. 따라서 **클라이언트 설치 0 · 표준 HTTPS만** 쓰는 Cloudflare Tunnel을 사용한다.
- 맥미니에서 `cloudflared`가 **아웃바운드** 터널을 열어 자사 도메인(named tunnel)으로 노출
  → 고정 공인 IP·포트포워딩 불필요, NAT/CGNAT 뒤에서도 동작.
- 직원은 브라우저로 `https://proposal.회사도메인.com` 접속(설치 0).
  **Cloudflare Access**로 이메일 OTP/SSO 인증 + 직원 이메일 허용목록.
- 데이터(고객 제안서)는 맥미니/NAS에 잔류, 터널은 암호화 세션만 중계. 특정 고객사가
  '데이터 제3자 엣지 경유 금지' 정책이면 그 건만 NAS 직접 전달 보조경로 사용.
- `cloudflared`도 launchd 서비스로 등록 → 사내 IP 변동 시 자동 재접속.
- 임시 `*.trycloudflare.com`은 차단·불안정 가능 → **반드시 named tunnel + 자사 도메인** 사용.

## AI 모드 (3단 지능 게이트웨이 — `engine/ai.py`)
- **결정론(무료)**: `engine/classify.py` 의 시그니처 매칭. 자산만으로 처리, 호출 0.
- **로컬 LLM(무료)**: Ollama HTTP API (`POST {base_url}/api/generate`). 기본 `qwen2.5`.
- **Claude API(유료, 상한 내)**: Anthropic Messages API (`POST /v1/messages`). 분류는 Haiku, 신규 페이지 유형의 레시피 작성은 Opus.

설정 (`config.json` 의 `ai` 섹션):
- `mode=tiered`: 결정론 → 로컬 → 클라우드. **평상시 기본**.
- `mode=local_only` (또는 `cloud.enabled=false`): 결정론 → 로컬. 클라우드 호출 0.
- `mode=secure_offline`: 결정론만. 로컬·클라우드 호출 0. **보안망 강제**.

예산 / 폴백:
- 응답의 `usage.input_tokens` / `output_tokens` 를 `cloud.pricing` (USD/1M 토큰) 으로 환산 → `logs/ai_spend.json` 에 월별 누적.
- `monthly_budget_usd` 초과 시 `can_use_cloud()` False → 자동 로컬/사람 폴백 (`fallback_when_over_budget`).
- API 키 (`api_key_env` 기본 `ANTHROPIC_API_KEY`) 미설정 시 클라우드 자동 비활성.

신규 페이지 유형 자동 레시피 제안:
- 분류가 `unknown`(신규 유형)이고 클라우드 사용이 가능하면, pipeline 이 Opus 에게 레시피 초안을 자동으로 청해 manifest 의 `recipe_proposal` 에 첨부한다.
- **자동 적용하지 않는다** — 상태는 `new_type_queued`(검수 큐)로 유지되고 검수자가 `/review` 화면에서 초안을 검토·승격한다(human-in-the-loop). "⬆ 레시피 승격" 버튼이 레시피를 라이브러리 파일(`assets.recipes_dir`, 기본 `assets/recipes/`)로 기록하고 상태를 `recipe_promoted` 로 전이한다. 레시피 라이브러리 경로는 env `PROPOSAL_RECIPES_DIR` 로도 덮어쓸 수 있다.
- **승격 시 결정론 분류 규칙 자동 등록**: 신규 유형 작업의 구조 시그니처(manifest `signature`)에서 `match` 술어를 유도해 `page_types`(`assets.page_types`, 기본 `assets/page_types.example.json`)에 `{type, desc, match, recipe}` 엔트리를 추가한다. 이후 같은 구조의 작업은 **결정론(무료)** 으로 이 유형에 분류·변환된다. `assets.register_page_type_on_promote=false` 또는 시그니처/파일 부재 시 레시피만 기록하고 분류 등록은 건너뛴다(`page_type_register_reason`). 경로는 env `PROPOSAL_PAGE_TYPES`.
  - **shadow 자동 해소(match 정교화)**: 기존 규칙이 이미 새 시그니처를 잡는 경우, 새 규칙을 말미에 두면 도달 불가다. 이때 시그니처의 **정확 카운트**(`n_table`/`n_image`/`n_year_box`/`n_text`/`has_title`)로 규칙을 좁혀(가리는 규칙의 제약을 모두 포함 → 그 부분집합) **가리는 규칙 앞에 삽입**한다. 결과: 이 구조는 신규 유형으로 분기되고, 가리던 규칙이 잡던 *다른* 구조는 그대로 유지(라우팅 보존). 구분 술어를 더할 수 없으면 말미 추가 후 미해소로 기록. `manifest.recipe_proposal.page_type_resolution` ∈ `{appended, specialized_before:<type>, shadowed_unresolved:<type>}`.
- **승격 시 슬라이드 즉시 재생성**: 신규 유형 작업에 변환 입력(`transform_inputs` = `template_pptx` + `source_slots`)이 보존돼 있으면, 승격 직후 승격된 레시피로 `transform → lint → route` 를 수행해 `out.pptx` 를 생성하고 정상 검수 흐름(`ready_for_review`/`needs_human_approval`)으로 재진입한다(`pipeline.regenerate`). 입력이 없거나 토글(`assets.regenerate_on_promote`, 기본 true)이 꺼져 있으면 레시피·분류 등록만 반영하고 상태는 `recipe_promoted` 로 둔다. `transform_inputs` 는 `pipeline.run_job` 이 신규 유형 큐잉 시 자동 보존한다.
- `ai.author_recipe_on_new_type=false` 로 끄면 큐잉만 하고 레시피 작성은 건너뛴다. `secure_offline`/`local_only`/예산초과/키없음 상황에서도 자동으로 시도하지 않는다.

다중 페이지 덱 + 콘텐츠 매핑(1:1):
- **다중 페이지 분류**(`pipeline.classify_deck`): 덱의 각 슬라이드를 1:1로 독립 분류 → `manifest.pages[]`(페이지별 유형·신뢰도·레시피 유무). 데몬이 다중 슬라이드 .pptx 를 자동으로 이 경로로 처리.
- **AI 콘텐츠 매핑**(`ai.map_content` + `pipeline.run_deck`): 초안 페이지의 텍스트를 표준 템플릿 슬롯에 배정해 변환. **문구 변경 없음** — LLM 은 "어느 초안 블록 → 어느 슬롯"의 **인덱스만** 결정하고(text_inject=단일 인덱스, **group_fill=인덱스 배열**), 실제 텍스트 값은 초안에서 **그대로(verbatim)** 복사한다. 그룹 슬롯도 초안 블록을 자동 배정(deep 추출로 그룹 텍스트가 보임). 게이트웨이가 없거나 보안망이면 **순서 기반(positional) 폴백**으로 오프라인 동작. 페이지는 1:1(분리/병합 없음).
  - **내용 기반 자동 분류**(`ai.classify_page` + `pipeline._classify_page`): 결정론(시그니처)으로 구분 안 되는 grouped 텍스트 타입은, 슬라이드 **내용 프로필**(텍스트 스니펫+레이아웃, `classify.content_profile`)과 **타입 카탈로그**(type+desc)를 LLM 에 주어 자동 분류한다(`source="content_llm"`). 라벨은 카탈로그 화이트리스트로 검증. 게이트웨이 없거나 보안망이면 결정론 결과만(안전).
  - **그룹 내부 추출**(`geometry.extract_shapes_deep`): 도형이 그룹(`grpSp`)에 중첩돼 있으면 최상위 추출(`extract_shapes`, 린터용)은 그룹 안 텍스트를 놓친다. deep 추출은 그룹을 재귀해 리프 텍스트를 절대좌표로 surfacing → 분류·매핑 정확도↑(실측: 일부 슬라이드는 top-level 텍스트 0개 → deep 10개+). 데몬이 초안 분류·매핑에 deep 을 쓰고, 출력 린트는 기존 `extract_shapes` 유지.
  - **운영자 페이지별 타입 지정**(폴백/우선): `run_deck(forced_types=...)` 로 페이지마다 타입을 명시 지정(분류 우회, `source="operator"`). 자동 분류가 애매한 페이지에 사용.
  - **`group_fill` op**: 슬롯이 그룹(`grpSp`)이면 내부 텍스트박스들을 입력 배열로 **순서대로 verbatim** 채운다(text_inject 는 단일 텍스트박스 전용). 그룹의 비텍스트 도형/구조는 보존.
  - **단일 파일 덱 조립 + 초안 이미지 carry-over**(`engine/deck.py`): `run_deck(out_deck=..., draft_pptx=...)` 로 표준 템플릿 기반 1:1 단일 PPTX 를 조립한다. 각 페이지는 그 유형의 표준 슬라이드를 복제·변환한 새 슬라이드가 되고(presentation sldIdLst 재지정), 초안 슬라이드의 이미지(`<p:pic>`)는 **위치 그대로** 옮겨진다(미디어 namespaced·rels 재매핑). 실제 SKB 템플릿(디자인가이드2.0)으로 구조 검증 완료.
  - 콘텐츠가 없는 op 는 건너뛴다(템플릿 자리 보존): `image_reuse`/`table_rebuild` 는 source 없으면 no-op → 이미지는 carry-over, 표는 템플릿 유지.
  - **표 슬롯 자동 입력**: 초안의 **2열(label/value) 표**를 `geometry` 가 셀텍스트로 추출 → `table_rebuild` 슬롯에 `[{label,value}]` **verbatim** 자동 입력. 복합/병합 표는 건너뜀(템플릿 표 보존).
  - **폰트 리맵**(`config.fonts.remap`): 미설치/비렌더 폰트(공체·뫼비우스·가는각진제목체 등)를 설치 폰트(KoPub 등)로 transform 단계에서 일괄 치환(템플릿 자체 폰트 포함). 어느 PC에서 열든 대체 깨짐 방지.
  - **조립 출력 검증**(`deck.validate`): content-type 누락·rels dangling 대상·미정의 r:id 0 보장. 노트(notesSlides)는 표준 출력에서 제거(원본 슬라이드 참조 dangling 방지). soffice 설치 시 PDF 렌더로 실열람 확인 가능.

HTTP 는 표준 라이브러리(urllib) 만 사용. anthropic SDK 등 외부 의존성 없음.

### 실 호출 활성화
```bash
# Ollama 설치 (별도 — 본 가이드 범위 밖)
brew install ollama && brew services start ollama
ollama pull qwen2.5

# Anthropic 키
export ANTHROPIC_API_KEY="sk-ant-..."

# AI selfcheck 의 실 호출 단계 켜기
PROPOSAL_REAL_LLM=1 python3 selfcheck/run_ai_demo.py
```

## 실행

### 엔진 자기검증 (표준 라이브러리만)
```
python3 selfcheck/run_selfcheck.py        # 린터 v1 자기검증
python3 selfcheck/run_pipeline_demo.py    # 파이프라인(분류·결선된 transform) + 학습 루프 자기검증
python3 selfcheck/run_transform_demo.py   # transform 4-op 단독 자기검증
python3 selfcheck/run_ai_demo.py          # AI 게이트웨이(분류·레시피·예산·모드) mock 검증
python3 selfcheck/run_adapters_demo.py    # 입력 어댑터(PDF/HWP/텍스트) + 데몬 통합 검증
python3 daemon/watch_inbox.py             # /inbox 상시 감시
python3 daemon/watch_inbox.py <파일>      # 수동 1건 처리(.pptx → 분류·린트·검수 큐)
```

### 데몬 ingest (inbox → 검수 큐)
`watch_inbox` 가 `nas.mount/inbox` 의 새 `.pptx` 를 감지해 처리한다(soffice 불필요, 표준 라이브러리 + engine 만 사용):
1. 초안 첫 슬라이드의 도형/크기 추출 → `pipeline.run_job` (분류 → 린트 → 라우팅, 신규 유형이면 AI 레시피 초안 제안까지).
2. 결과 manifest 를 검수 store(`web.store_dir`, env `PROPOSAL_WEB_STORE`)에 기록 → `/review` 에 즉시 노출.
3. 변환 산출물이 없으면 초안 PPTX 를 검수용 `out.pptx` 로 노출(미리보기·다운로드).

**사이드카 잡 스펙**: 초안 `X.pptx` 옆에 `X.job.json` 을 두면 그 안의 `template_pptx`/`source_slots`/`workdir`/`out_pptx`/`size` 가 job 에 병합되어 결정론 변환 경로가 활성화된다. 신규 유형(`unknown`)이면 변환은 보류하되 `template_pptx`+`source_slots` 를 `transform_inputs` 로 보존해, 검수자가 레시피를 승격할 때 슬라이드가 즉시 재생성된다(위 "AI 모드" 절). `page_types` 경로는 `assets.page_types`(env `PROPOSAL_PAGE_TYPES`).

**source_slots 자동 추출**: 사이드카가 `template_pptx` 만 주고 `source_slots` 가 없으면, 초안의 `slot:<key>` 로 명명된 텍스트 도형에서 내용을 결정론적으로 추출(`geometry.source_slots_from_shapes`)해 표준 템플릿에 반영한다 → 손으로 `source_slots` JSON 을 작성하지 않아도 된다(텍스트 한정; 표·이미지는 사이드카 필요). `daemon.auto_extract_slots`(기본 true)로 끈다.

**파일 수명주기**: 가동 루프는 처리한 초안(+사이드카)을 `daemon.processed_dir`(기본 `inbox/_processed`, env `PROPOSAL_PROCESSED_DIR`)로, 처리 실패 건은 `daemon.failed_dir`(기본 `inbox/_failed`, env `PROPOSAL_FAILED_DIR`)로 이동한다 → inbox 를 비워 **재기동 시 재처리(중복 job)를 방지**한다. 보관 폴더의 `retention.source_delete_days`(기본 30) 초과 파일은 기동 직후 + 하루 1회 자동 삭제한다. 수동 1건 처리(`watch_inbox.py <파일>`)는 지정 파일을 이동하지 않는다.

상시 가동(launchd)·검수 웹 연동·환경변수 오버라이드 전체 절차는 [`docs/operations.md`](docs/operations.md) 참고.

### 입력 어댑터 (PDF·HWP·텍스트 — `adapters/`)
PPTX 가 아닌 입력은 별도 어댑터 레이어가 텍스트를 추출해 `source_slots`(title/body/blocks/text)로 만든다. **engine 의 표준 라이브러리 전용 원칙을 깨지 않도록**, 외부 파서 의존성은 이 레이어 안에서만 **lazy import** 한다.
- `.txt`/`.md`: 의존성 없음(레퍼런스 어댑터).
- `.pdf`: `pypdf`(`pip install pypdf`). 미설치 시 `AdapterUnavailable` 로 우아하게 실패 → 데몬은 해당 건을 `needs_human_approval` 로 큐잉(크래시 없음).
- `.hwp`: HWP 5.0(OLE). 레코드 파싱은 순수 함수로 구현(검증됨), OLE 컨테이너 읽기만 `olefile`(`pip install olefile`) lazy.
- `.hwpx`: HWPX(OWPML, ZIP+XML). `Contents/section*.xml` 의 `<hp:p>`/`<hp:t>` 추출 — **표준 라이브러리만**(외부 의존 없음, 항상 활성).

문서엔 슬라이드 구조가 없으므로, 변환하려면 사이드카가 `template_pptx`(표준 템플릿)와 `page_type`(유형)을 지정해야 한다. 데몬이 추출 텍스트를 `source_slots` 로, 사이드카의 `page_type` 를 강제 분류(`classify.source="forced"`)로 사용해 변환한다. 외부 파서는 `pip install -r requirements-adapters.txt`(pypdf·olefile) 후 활성화된다. `run_adapters_demo.py` 는 설치 여부를 자동 감지해 실파일 단계를 켜거나 스킵하므로, engine 자기검증은 파서 없이도 PASS 한다.

### 웹 UI (FastAPI, venv 의존)
```
# 최초 1회
python3 -m venv ../.venv                  # /Users/shin/AI_pptx/.venv 생성
../.venv/bin/pip install -r requirements-web.txt

# 자기검증
../.venv/bin/python selfcheck/run_web_demo.py  # 라우트 11종 in-process 검증(포트 안 띄움)

# 개발 서버 실행
../.venv/bin/uvicorn web.app:app --reload --host 127.0.0.1 --port 8000
# 또는 source ../.venv/bin/activate 후 uvicorn 그대로 사용

# 브라우저: http://127.0.0.1:8000/
```
`config.json` 의 `web.store_dir` 가 job 디렉터리 위치(기본 `./jobs`). `PROPOSAL_WEB_STORE` 환경변수가 있으면 우선.
미리보기는 `soffice`+`pdftoppm` 설치 시만 작동. `PROPOSAL_PREVIEW_DISABLE=1` 로 강제 비활성화.

## 직원 수정 학습 루프
초안 → 엔진 후보(`/review`) → 직원이 PPTX 수정 → `learn.diff_shapes()`가
후보와 최종본을 비교(이동·크기·폰트·텍스트) → 교정 기록 누적 →
반복 교정은 사람 1회 승인 후 design_guide/recipe로 승격.

## 검증/거버넌스
- 린터 차단(fail): overlap · text_overflow · off_slide / 경고(warn): tight_gap · uneven_gap · nonrender_font.
- 경미(폰트·간격) 자동수정, 구조 변경은 사람 승인(메일 알림 + 검수 직링크).
- 자산·코드 git 리포(NAS), 골든셋 회귀, 스냅샷 90일·원본 30일 보관.

## 자기검증 결과(현재)
- 린터 v1: 정상 더미 PASS / 결함 더미 3종 검출 / 실제 생성 슬라이드 PASS.
- 파이프라인: 결정론 분류 → 검수 대기, 신규 유형 → 큐잉(게이트웨이 없을 때 제안 없음 / cloud 가능 시 AI 레시피 초안 자동 제안·상태는 큐 유지 / cloud 불가 시 작성 건너뜀), 직원수정 diff 검출, PPTX→transform→lint 결선, 데몬 ingest(inbox→분류→store 기록·초안 노출 / 사이드카→신규 유형 큐잉+transform_inputs 보존 / 처리 후 _processed 이동 / 보관기간 만료 삭제 / launchd plist 검증 / source_slots 자동 추출) 모두 PASS.
- transform 4-op: 합성 PPTX·이미지 픽스처로 text_inject/table_rebuild/image_reuse/shape_rebuild + 다중 슬라이드(template_slides[]) 모두 PASS.
- 웹 UI: TestClient 로 라우트 (목록·상세·다운로드·승인·수정본 업로드+diff·AI 레시피 초안 표시·승격(멱등·실패 차단)·승격 시 page_types 자동 등록→결정론 라우팅·기존 라우팅 보존·shadow 자동 해소(특수 규칙을 일반 규칙 앞에 삽입)·승격 시 슬라이드 즉시 재생성→검수 재진입·404 등) 16단계 모두 PASS.
- 다중 페이지/덱: 페이지별 1:1 분류(`classify_deck`)·1:1 덱 변환(`run_deck`)에서 초안 문구가 표준 템플릿에 **verbatim** 반영(오프라인 positional + mock AI 인덱스 배정) PASS.
- 입력 어댑터: 레지스트리 등록·text 추출·pdf/hwp 우아한 실패·HWP5 레코드 파서(합성, 평/압축)·데몬 통합(.txt 변환 / .pdf 검수 큐) 모두 PASS. 파서 설치 환경에서는 실파일 단계도 자동 실행([8] 생성 PDF→pypdf 추출→데몬 변환 왕복, [9] 비-OLE .hwp→olefile 형식 거부); 미설치 환경에서는 자동 스킵하고 PASS 유지. [10] HWPX 는 stdlib(zip/xml)만 쓰므로 항상 실행(생성 .hwpx→추출→데몬 변환 왕복).
- AI 게이트웨이: MockHttp 로 13단계 25 sub-check 모두 PASS (결정론 우선, local 폴백, cloud 폴백, secure_offline/local_only 모드, 예산 추적·초과 차단, 레시피 4-op 검증, 5xx·비-JSON·코드펜스 파싱).
- 자기검증 중 발견·보완: 텍스트 박스 거짓 겹침 → 잉크 박스 도입, `wrap=none` 라벨 폭 보정, 표 높이는 행합 사용, `body_company_overview` 레시피의 `image_reuse` 누락 보강.

## 다음 단계
1. ~~`transform.py`에 4대 변환 로직~~ → **완료(결선된 pipeline 포함)**. 작성 가이드: `docs/template-authoring.md`.
2. ~~웹 UI(FastAPI)~~ → **완료(검수·승인·학습 diff 화면)**. 실행 위 "실행" 절 참고.
3. ~~로컬 LLM(Ollama) 분류 + Claude 레시피 작성 연동~~ → **완료**. 위 "AI 모드" 절 참고.
4. ~~신규 유형 발견 시 pipeline 의 자동 레시피 초안 제안(`recipe_proposal`)~~ → **완료**. 위 "AI 모드" 절 참고.
5. ~~AI 레시피 초안의 검수 UI 노출(`/review` 표시·승격 버튼)~~ → **완료**. `recipe_promoted` 상태 + 레시피 라이브러리 기록.
6. ~~승격된 레시피의 `page_types` 매칭 시그니처 자동 등록~~ → **완료**. 승격 시 시그니처→match 유도, 결정론 라우팅.
7. ~~승격 레시피로 본 슬라이드 즉시 재생성~~ → **완료**. `pipeline.regenerate` + `transform_inputs` 보존.
8. ~~실제 ingest 경로 결선(`daemon/watch_inbox` 가 inbox→run_job→store 기록, 사이드카로 transform_inputs 보존)~~ → **완료**. 위 "데몬 ingest" 절.
9. ~~데몬 inbox 파일 수명주기(처리 후 보관 이동 → 재기동 재처리 방지)~~ → **완료**. 위 "파일 수명주기".
10. ~~보관기간 만료 삭제(retention)~~, ~~launchd plist 운영 가이드~~ → **완료**. `docs/operations.md`.
11. ~~다중 슬라이드 변환(`recipe.template_slides[]`)~~ → **완료**. transform/pipeline 슬라이드별 린트 합산.
12. ~~`source_slots` 자동 추출(slot 명명 초안 → 텍스트)~~ → **완료**. 표/이미지는 여전히 사이드카.
13. ~~PDF·HWP 입력 어댑터(별도 레이어, 외부 파서 lazy)~~ → **완료**. 위 "입력 어댑터". `pip install pypdf olefile` 로 활성화.
14. ~~HWPX(.hwpx, zip 기반) 어댑터~~ → **완료**. stdlib 만(외부 의존 없음), 실파일 e2e 검증.
15. ~~match 술어 정교화(shadow 자동 해소·정확 카운트 가중)~~ → **완료**. 위 "shadow 자동 해소".
16. ~~다중 페이지 분류 + AI 콘텐츠 매핑(1:1·verbatim)~~ → **완료**. 위 "다중 페이지 덱 + 콘텐츠 매핑".
17. ~~단일 파일 덱 조립 + 초안 이미지 carry-over(원위치)~~ → **완료**(`engine/deck.py`). 실 SKB 템플릿 구조 검증.
18. ~~SKB 표준 템플릿 타입 라이브러리~~ → **완료**(`assets/recipes/skb/` 22종, 실파일 전수 검증).
19. ~~내용 기반 자동 타입 분류~~ → **완료**(`ai.classify_page`). 운영자 지정 없이 페이지 타입 자동 선택(지정은 폴백).
20. ~~group_fill 슬롯의 초안→배열 자동 매핑~~ → **완료**(map_content 배열 배정·verbatim).
21. **남은 항목**: 표 슬롯 자동 입력(table_rebuild), 실 `.hwp` 샘플 회귀, 비렌더 폰트 리맵, PowerPoint 실열람 정밀 확인.
