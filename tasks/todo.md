# Ollama + Claude 연동 — 3단 지능 게이트웨이 구현 (설계)

## 배경
- `engine/ai.py` 는 현재 골격만 있고 `NotImplementedError` 만 던지는 스텁.
- `engine/classify.py` 는 이미 게이트웨이 인터페이스(`local_classify`/`cloud_classify`) 를 호출하도록 설계됨 → 게이트웨이만 채우면 결정론 폴백 흐름 완성.
- 신규 페이지 유형 발견 시 `author_recipe` 로 클라우드(Opus) 가 레시피 JSON 생성.

## 결정 사항 (사용자 확인 완료)
1. **의존성**: stdlib (`urllib.request`) 만 사용. anthropic SDK 미사용. engine 표준 라이브러리 원칙 유지.
2. **범위**: 분류 + 레시피 작성 모두 이번 라운드.
3. **Usage 로그 위치**: `proposal_factory/logs/ai_spend.json` (기존 ai.py 가 쓰던 경로 유지).

## 인터페이스

```python
class AIGateway:
    def __init__(self, cfg): ...
    # 정책
    def can_use_cloud(self) -> bool         # 보안망/예산 종합 판정
    def remaining_budget(self) -> float
    # 사용 지점
    def local_classify(self, sig: dict) -> Optional[str]
        """Ollama 호출. 페이지 유형 string 또는 None."""
    def cloud_classify(self, sig: dict) -> Optional[str]
        """Claude Haiku 호출. 보안망/예산 차단 시 None."""
    def author_recipe(self, sig: dict, sample_shapes: list, hint: str = "") -> Optional[dict]
        """Claude Opus 호출. recipe 사전 또는 None."""
```

- 모든 호출은 결과를 반환하거나 None (게이트웨이 단에서 예외 안 뱉음). 호출자(`classify.classify`) 는 이미 None 폴백 흐름 구현.
- 단, 네트워크/모델 에러는 게이트웨이 안에서 잡아 `usage` 로그에 실패 기록 → None 반환.

## 모드 (config.ai.mode)

- `tiered` (기본): 결정론 → 로컬 → 클라우드. 평상시.
- `local_only`: 결정론 → 로컬. 클라우드 호출 안 함(`can_use_cloud` 항상 False).
- `secure_offline`: 결정론만. 로컬/클라우드 둘 다 안 함.

기존 `cloud.enabled=false` 는 `mode=local_only` 의 별칭으로 처리.

## HTTP 호출 명세

### Ollama (`POST {ollama.base_url}/api/generate`)
- `base_url` 기본 `http://localhost:11434`
- 요청:
  ```json
  {"model": "qwen2.5", "prompt": "<prompt>", "stream": false,
   "format": "json", "options": {"temperature": 0}}
  ```
- 응답: `{"response": "<json string>", ...}` — JSON 파싱 → 결과.
- 타임아웃: 30초.
- 사용 시점: classify (분류 결과) 만. 레시피는 클라우드.

### Anthropic (`POST https://api.anthropic.com/v1/messages`)
- 헤더: `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json`
- 요청:
  ```json
  {"model": "<claude_model>", "max_tokens": <N>,
   "system": "<system_prompt>",
   "messages": [{"role": "user", "content": "<prompt>"}]}
  ```
- 응답: `{"content": [{"type": "text", "text": "<...>"}], "usage": {"input_tokens": X, "output_tokens": Y}, ...}`
- 가격: `cfg.ai.cloud.pricing` 에서 모델별 (USD/1M token) 읽음.
- 타임아웃: 60초.

### config.json 확장
```json
"ai": {
  "mode": "tiered",
  "local_llm": {"provider": "ollama", "base_url": "http://localhost:11434",
                "model": "qwen2.5", "timeout_s": 30, "enabled": true},
  "cloud": {"provider": "anthropic", "api_key_env": "ANTHROPIC_API_KEY",
            "endpoint": "https://api.anthropic.com/v1/messages",
            "anthropic_version": "2023-06-01",
            "classify_model": "claude-haiku-4-5",
            "recipe_model": "claude-opus-4-7",
            "classify_max_tokens": 200, "recipe_max_tokens": 2000,
            "timeout_s": 60, "enabled": true,
            "pricing": {
              "claude-haiku-4-5": {"in_per_mtok": 1.0, "out_per_mtok": 5.0},
              "claude-opus-4-7": {"in_per_mtok": 15.0, "out_per_mtok": 75.0}
            }},
  "monthly_budget_usd": 50,
  "fallback_when_over_budget": "local_then_human"
}
```

가격 값은 보수적 placeholder (실제 청구 시점 가격으로 사용자가 갱신 가능). 사용량은 응답 `usage` 에서 추출 → `_record(usd)`.

## Prompt 설계 (간결, 결정론 출력)

### local_classify prompt
- "주어진 도형 시그니처를 보고 페이지 유형을 다음 중 하나로 분류해라. 모르면 'unknown'."
- 시그니처 dict 와 알려진 페이지 유형 목록(page_types 의 type 들) 입력.
- 응답: `{"page_type": "<...>"}` JSON.

### cloud_classify prompt (Haiku)
- 같은 형식. system 에 정확한 라벨 강제.

### author_recipe prompt (Opus)
- "신규 페이지 유형 후보. 도형 시그니처와 샘플 도형들을 보고 4-op 레시피를 JSON 으로 작성."
- system 에 4-op 명세(text_inject/table_rebuild/image_reuse/shape_rebuild) 와 slot 이름 규약 명시.
- 응답: recipe JSON.

## 의존성 주입 (테스트 가능성)

```python
class AIGateway:
    def __init__(self, cfg, http=None):
        self._http = http or _stdlib_http
```

- `_stdlib_http(method, url, headers=None, json_body=None, timeout=30)` → `(status, body_bytes)` 또는 raise.
- mock 시 `http=MockHttp({...})` 로 외부 호출 없이 응답 시뮬레이션.

## classify.py 통합

기존 classify.classify 의 게이트웨이 폴백 흐름 그대로 유지. 게이트웨이가 NotImplementedError 대신 정상 응답(또는 None) 반환하면 자동으로 작동.

## 검증 (selfcheck/run_ai_demo.py)

1. **결정론 우선**: AIGateway 가 있어도 시그니처가 매칭되면 게이트웨이 호출 안 함.
2. **로컬 폴백**: 결정론 미매칭 → local_classify 호출 → 결과 반환.
3. **클라우드 폴백**: local 실패 → cloud_classify 호출 → 결과 반환.
4. **보안망 모드**: `mode=secure_offline` → local/cloud 둘 다 None (Mock http 호출 0회).
5. **local_only 모드**: local 만 호출, cloud 호출 0회.
6. **예산 추적**: cloud 호출 후 `_spend()` 가 USD 누적.
7. **예산 초과**: `monthly_budget_usd=0.01` 로 cloud_classify 1회 호출 → can_use_cloud False → 두번째 호출은 None.
8. **author_recipe**: mock cloud 응답 → 4-op 포함 dict 파싱.
9. **HTTP 실패**: mock 이 5xx 반환 → 게이트웨이 None.
10. **JSON 파싱 실패**: mock 이 비-JSON 반환 → None.
11. **실 호출 옵트인**: `PROPOSAL_REAL_LLM=1` 환경변수 + Ollama/Anthropic 키 둘 다 있을 때만 실제 ping (없으면 skip).

## 영향 범위 / 비목표
- 영향: `engine/ai.py` 재작성, `config.json` ai 섹션 확장, 신규 `selfcheck/run_ai_demo.py`.
- **건드리지 않음**: `classify.py`, `pipeline.py`, `transform.py`, `linter.py`, `web/`, 기존 4종 selfcheck.
- 비목표: 스트리밍 응답, 토큰 카운팅 클라이언트측 계산, 자동 모델 선택, Ollama 모델 자동 pull, 프롬프트 튜닝 라운드, Claude Sonnet 추가, OpenAI/Gemini 폴백.

## 가정 / 미해소
- A1. `config.ai.cloud.api_key_env` 이름의 환경변수에서 API 키 읽음 (기본 `ANTHROPIC_API_KEY`). 미설정 시 cloud 비활성과 동일.
- A2. 가격은 보수적 placeholder. 실제 가격 변동 시 사용자가 config 갱신.
- A3. Ollama 모델 `qwen2.5` 가 사전 설치 가정. 미설치 시 first request 가 실패 → None 반환 (정상 폴백).
- A4. Anthropic 모델 이름은 `claude-haiku-4-5`, `claude-opus-4-7` placeholder. 실제 사용 시 사용자가 정확한 ID로 갱신.

## 작업 순서 (승인 후)
1. `engine/ai.py` 재구현 (task #18) — AIGateway + _stdlib_http + 프롬프트 구성.
2. `selfcheck/run_ai_demo.py` (task #19) — 11 단계 mock 검증.
3. `config.json` 갱신 (task #20) — ai 섹션 확장.
4. `README.md` 갱신 — AI 모드 절 + 실 호출 활성화 가이드.
5. 5종 selfcheck 회귀.

## 사용자 확인 요청
- [ ] AIGateway 인터페이스: `local_classify(sig)` / `cloud_classify(sig)` / `author_recipe(sig, sample_shapes, hint)` 시그니처 OK?
- [ ] 모드 3가지(`tiered`/`local_only`/`secure_offline`)로 단순화 OK? (`cloud.enabled=false` 는 `local_only` 별칭)
- [ ] HTTP 호출 stdlib 으로 `urllib.request.urlopen` 사용 OK?
- [ ] 가격 placeholder (haiku 1.0/5.0, opus 15.0/75.0 USD/1M token) OK? 실제 가격은 사용자가 config 에서 갱신.
- [ ] 실 호출 옵트인은 `PROPOSAL_REAL_LLM=1` 환경변수 + 둘 다 살아있을 때만 OK?
- [ ] **건드리지 않을 것**: classify/pipeline/transform/linter 코드와 기존 4종 selfcheck — OK?

승인되면 task #18 부터 순차 진행.

## 리뷰

### 산출물
- `engine/ai.py` — `AIGateway` 재구현 (스텁 → 완전 동작). 표준 라이브러리만(urllib). 226 라인.
- `config.json` — `ai` 섹션 확장 (endpoint, anthropic_version, model IDs, pricing 표).
- `selfcheck/run_ai_demo.py` — MockHttp 로 13단계 25 sub-check.
- `README.md` — "AI 모드" 절 갱신, 실 호출 활성화 방법 추가, 자기검증 결과 5종으로 확장.

### 검증 결과 (5종 모두 PASS)
| selfcheck | 결과 |
| --- | --- |
| `run_selfcheck.py` (린터 v1) | PASS |
| `run_pipeline_demo.py` (오케스트레이션+학습+결선) | PASS |
| `run_transform_demo.py` (transform 4-op) | PASS |
| `run_ai_demo.py` (AI 게이트웨이 13단계) | PASS |
| `run_web_demo.py` (FastAPI UI 11라우트) | PASS |

### AI 게이트웨이 검증 세부
| 단계 | 검증 |
| --- | --- |
| 1 | 결정론 매칭 우선 — HTTP 호출 0회 |
| 2 | 결정론 미매칭 → local_classify → source=local_llm |
| 3 | local 실패(OSError) → cloud_classify → 예산 차감 0.000350 USD |
| 4 | secure_offline 모드 → HTTP 호출 0회, unknown 반환 |
| 5 | local_only 모드 → Anthropic 0회 |
| 6 | cloud.enabled=False 별칭 동작 |
| 7 | 예산 0 → can_use_cloud=False, API 호출 0회 |
| 8 | API 키 미설정 → can_use_cloud=False |
| 9 | author_recipe(Opus) — 4-op 검증, 비용 0.030 USD 정확 |
| 10 | Anthropic 5xx → None |
| 11 | 비-JSON → None / 코드펜스 JSON 파싱 성공 |
| 12 | 알 수 없는 라벨 → None (라벨 화이트리스트 검증) |
| 13 | 실 호출 옵트인 (PROPOSAL_REAL_LLM=1, 미설정 시 스킵) |

### 핵심 결정 사항
1. **stdlib 유지** — urllib.request 로 Ollama/Anthropic 둘 다 호출. engine 의 표준 라이브러리 원칙 보존.
2. **모드 3가지로 단순화** — `tiered` / `local_only` / `secure_offline`. `cloud.enabled=false` 는 `local_only` 별칭.
3. **HTTP 주입 가능** — `AIGateway(cfg, http=_callable_)` 로 mock 주입 → selfcheck 실 호출 없이 동작 검증.
4. **라벨 화이트리스트** — LLM 응답이 `known_page_types` 목록 밖이면 None. hallucination 방어.
5. **코드펜스 fallback 파싱** — ` ```json {...} ``` ` 형태도 인식 (LLM이 종종 마크다운 펜스로 래핑).
6. **비용 정확** — Anthropic 응답의 `usage.input_tokens/output_tokens` × `pricing` 표 → USD 변환 → `logs/ai_spend.json` 월별 누적.
7. **spend_path 주입** — selfcheck 가 임시 경로로 격리 가능. 운영은 기본 `logs/ai_spend.json`.

### 발견 사항
1. **모델 이름 placeholder**: `claude-haiku-4-5` / `claude-opus-4-7` 는 placeholder. 실제 Anthropic 공식 모델 ID로 사용자가 갱신해야 함 (config.json 에 명시).
2. **가격 placeholder**: pricing 표 (haiku 1.0/5.0, opus 15.0/75.0 USD/1M-token) 도 보수적 추정. 실제 청구 시점 가격으로 갱신 권장.
3. **engine/classify.py 호환 유지**: 기존 게이트웨이 인터페이스 (`local_classify(sig)`, `cloud_classify(sig)`) 그대로 → 코드 변경 없이 작동 확인.
4. **logs/ 디렉터리는 _record 시점에 생성**: 초기화에서 만들지 않음 (부작용 최소화). selfcheck 격리 용이.

### 비목표 (의도적 미수행)
- 스트리밍 응답 처리 (현재 `stream: false` 고정)
- 클라이언트측 토큰 카운팅 (응답의 usage 만 신뢰)
- 자동 모델 선택 / fall-back chain 내 다중 모델
- Ollama 모델 자동 pull
- 프롬프트 튜닝/평가 라운드
- 다중 LLM 벤더 (OpenAI, Gemini) 지원

### 다음 단계 후보
- PDF/HWP 입력 어댑터 (README 다음 단계 4번 마지막)
- ~~pipeline 의 자동 author_recipe 호출 (`new_type_queued` 상태에서 cloud 가능하면 자동 레시피 시도)~~ → **완료** (아래 라운드 참고)
- 다중 슬라이드 변환 (`recipe.template_slides[]` 배열 지원)
- launchd + cloudflared 통합 운영 가이드
- 자산 (page_types, recipes, design_guide) 의 NAS git 리포 동기화

---

# 라운드 — 신규 유형 자동 레시피 제안 (pipeline ↔ AI 게이트웨이 결선)

## 배경
직전 라운드에서 `AIGateway.author_recipe(sig, sample_shapes, hint)` 를 완성했으나,
`pipeline.run_job` 은 신규 유형(`unknown`) 발견 시 `new_type_queued` 로 큐잉만 하고
이 메서드를 호출하지 않아 연결이 끊긴 상태였음. 그 고리를 이음.

## 결정 사항
1. **자동 적용 금지**: AI가 작성한 레시피는 즉시 transform 에 쓰지 않고 manifest 의
   `recipe_proposal` 에 '제안'으로만 첨부. 상태는 `new_type_queued` 유지 → 검수자 승격.
   (human-in-the-loop. LLM hallucination 레시피로 잘못된 PPTX 자동 생성 방지.)
2. **토글**: `config.ai.author_recipe_on_new_type` (기본 true). false 면 큐잉만.
3. **게이트웨이 단 안전성 승계**: `can_use_cloud()` False(secure_offline/local_only/예산초과/키없음)
   면 시도 0. 게이트웨이 없으면 `recipe_proposal` 키 자체를 안 만듦.
4. **예외 흡수**: `author_recipe` 가 규약을 어기고 raise 해도 pipeline 은 죽지 않고
   `{"attempted":true,"ok":false,"reason":...}` 로 기록.

## 산출물
- `engine/pipeline.py` — `_try_author_recipe()` 헬퍼 + unknown 분기에서 호출. notify 문구 분기.
- `config.json` — `ai.author_recipe_on_new_type: true`.
- `schemas/job_manifest.schema.json` — `recipe_proposal` 속성 추가.
- `selfcheck/run_pipeline_demo.py` — `StubGateway` + 단계 [2]/[2b]/[2c] 추가.
- `README.md` — "AI 모드" 절에 자동 제안 설명, 자기검증/다음단계 갱신.

## 검증 (5종 모두 PASS)
| selfcheck | 결과 |
| --- | --- |
| run_selfcheck | PASS |
| run_pipeline_demo (신규 [2b]/[2c] 포함) | PASS |
| run_transform_demo | PASS |
| run_ai_demo | PASS |
| run_web_demo | PASS |

신규 단계 세부:
- [2] 게이트웨이 없음 → `recipe_proposal` 키 미생성, 큐잉.
- [2b] cloud 가능 → `author_recipe` 1회 호출, `recipe_proposal.ok=True`, **상태는 큐 유지**, 알림 문구 "AI 레시피".
- [2c] cloud 불가(`can_use_cloud=False`) → `author_recipe` 미호출, `attempted=False`.

## 비목표 (의도적 미수행)
- 검수 UI(`/review`)에서 `recipe_proposal` 표시·승격 버튼 (다음 라운드).
- 제안 레시피를 즉시 transform 에 적용해 본 슬라이드 생성 (안전상 보류).
- 다중 슬라이드 변환(`template_slides[]`).

---

# 라운드 — 검수 UI: AI 레시피 초안 표시 + 승격 버튼

## 배경
직전 라운드가 `recipe_proposal` 을 manifest 에 첨부했으나 검수 UI 에 노출되지 않아
검수자가 초안을 보거나 승격할 수단이 없었음. 그 마지막 한 칸을 채움.

## 결정 사항
1. **승격 = 레시피 라이브러리 등록**: AI 초안을 라이브러리 파일(`<recipes_dir>/<type>.json`)로
   기록 + job 디렉터리에 감사 사본(`promoted_recipe.json`). 즉시 transform 자동 적용은 안 함.
2. **새 상태 `recipe_promoted`**: 승격 시 전이. 검수 큐에서 빠지되 "슬라이드 생성 완료"와는 구분.
   `page_type` 이 unknown 이면 레시피 type 으로 갱신.
3. **레시피 경로 해석**: env `PROPOSAL_RECIPES_DIR` → `cfg.assets.recipes_dir` → `<root>/assets/recipes`.
   상대경로는 PROJECT_ROOT 기준.
4. **파일명 안전 슬러그**: LLM 이 만든 `type` 문자열을 그대로 파일명에 쓰지 않고 `_safe_slug` 로 정제.
5. **멱등 + 방어**: 이미 승격된 건 재작성 없이 redirect. `ok!=True`/recipe 없음 → 400.
6. **page_types 시그니처 자동 등록은 범위 밖**: 결정론 분류 매칭은 운영자가 별도 추가(UI 힌트 명시).

## 산출물
- `web/store.py` — `save_promoted_recipe(job_id, recipe)`.
- `web/app.py` — `_safe_slug`, `_resolve_recipes_dir`, `POST /jobs/{id}/promote-recipe`,
  `_STATUS_ORDER` 에 `recipe_promoted` 추가, `import json/re`.
- `web/templates/review.html` — `recipe_proposal` 카드(유형/op/JSON details/승격 폼/승격됨/실패 분기),
  감사 로그에 promote_recipe 표기.
- `web/templates/base.html` — `.status-recipe_promoted`, `pre.code-block`, `.ops-list`, `.badge-ai`.
- `config.json` — `assets.recipes_dir`.
- `schemas/job_manifest.schema.json` — status enum `recipe_promoted`, recipe_proposal 의
  `promoted`/`promoted_by`/`promoted_path` 속성.
- `selfcheck/run_web_demo.py` — 임시 라이브러리 격리 + 단계 [12]/[13]/[14], /jobs 카운트 1→3.

## 검증 (5종 모두 PASS)
| selfcheck | 결과 |
| --- | --- |
| run_selfcheck | PASS |
| run_pipeline_demo | PASS |
| run_transform_demo | PASS |
| run_ai_demo | PASS |
| run_web_demo (신규 [12]/[13]/[14]) | PASS |

신규 단계 세부:
- [12] `/review/{np}` → "AI 레시피 초안" 카드·유형·op·승격 버튼 폼 렌더.
- [13] `POST /promote-recipe` → 303, 상태 `recipe_promoted`, page_type 갱신, 라이브러리
  파일 작성·내용 일치, `recipe_proposal.promoted=True`, 감사 `promote_recipe` 기록.
- [14] 재승격 멱등(감사 1건 유지), 실패 초안은 사유 표시·승격 버튼 없음·승격 시도 400.

## 비목표 (의도적 미수행)
- 승격 레시피의 `page_types` 매칭 시그니처 자동 등록 (결정론 분류 튜닝 — 다음).
- 제안 레시피로 본 슬라이드 즉시 생성/재인제스트.
- 다중 슬라이드 변환(`template_slides[]`).

---

# 라운드 — 승격 시 page_types 매칭 시그니처 자동 등록

## 배경
승격은 레시피를 라이브러리에 기록했지만, 신규 유형이 **결정론(무료)** 으로 분류되려면
`page_types` 에 match 규칙이 있어야 함. 그게 없으면 같은 구조의 다음 작업도 매번 unknown→LLM.
이 마지막 고리(분류 자급)를 채워 비용/지연 없이 재사용되게 함.

## 결정 사항
1. **시그니처 출처**: pipeline 이 unknown 분기에서 `classify.signature` 를 manifest `signature` 에 보존.
   승격 시 이 값에서 match 술어를 유도(없으면 등록 스킵·사유 기록).
2. **match 유도 규칙**(`_match_from_signature`): 구조 키만 사용
   (n_table/n_image/n_year_box). 값>0→`_min:1`, ==0→`_max:0`. has_title 은 참일 때만 고정.
   n_text 는 과도하게 일반적이라 제외. 항상 비어있지 않음(classify._match 가 빈 match 거부).
3. **추가 위치 = 목록 말미**: 기존 엔트리가 먼저 평가되어 기존 유형 라우팅 보존.
   같은 type 이면 갱신(멱등). 더 앞선 기존 규칙이 같은 시그니처를 잡으면 shadow 로 감사 경고.
4. **best-effort**: 등록 실패(파일 부재/쓰기 실패/시그니처 없음/토글 off)는 승격 자체를 막지 않음
   (레시피 라이브러리 기록은 이미 끝남). `recipe_proposal.page_type_register_reason` 에 사유.
5. **경로/토글**: `assets.page_types`(env `PROPOSAL_PAGE_TYPES`),
   `assets.register_page_type_on_promote`(기본 true).
6. **page_types 자체 자동 생성은 안 함** — 운영 파일이 없으면 빈 목록에서 시작해 1건 추가.

## 산출물
- `engine/pipeline.py` — unknown 분기에서 `man["signature"]` 보존.
- `web/app.py` — `_resolve_page_types_path`, `_match_from_signature`, `_register_page_type`,
  promote-recipe 에 등록 결선, `import classify`.
- `web/templates/review.html` — 승격됨 카드에 결정론 등록/매칭 시그니처/미등록 사유 표시,
  사전 안내 문구 갱신.
- `config.json` — `assets.page_types`, `assets.register_page_type_on_promote`.
- `schemas/job_manifest.schema.json` — `signature` 속성, recipe_proposal 의
  `page_type_registered`/`page_type_match`/`page_type_register_reason`.
- `selfcheck/run_web_demo.py` — page_types 임시 격리(예시 복사) + np 시그니처(비충돌) +
  [13] 에 등록·결정론 라우팅·기존 라우팅 보존 검증.

## 검증 (5종 모두 PASS, 레포 page_types.example.json 무변경)
| selfcheck | 결과 |
| --- | --- |
| run_selfcheck | PASS |
| run_pipeline_demo | PASS |
| run_transform_demo | PASS |
| run_ai_demo | PASS |
| run_web_demo (등록·라우팅 검증 포함) | PASS |

신규 검증 세부([13] 확장):
- `recipe_proposal.page_type_registered=True`, page_types 파일에 `new_body` 엔트리(match 포함) 추가.
- 같은 구조의 새 작업 → `classify.classify` 가 `("new_body", 1.0, "deterministic")` 반환.
- 표+연도박스 작업 → 여전히 `body_company_overview` (기존 라우팅 보존).

## 비목표 (의도적 미수행)
- shadow 자동 해소(규칙 재정렬/세분화) — 사람이 page_types 편집으로 처리.
- match 술어에 카운트 정확값·신뢰도 가중 반영(현재 존재/부재 이분).
- 승격 레시피로 이 작업의 본 슬라이드 즉시 재생성.
- 다중 슬라이드 변환(`template_slides[]`).

---

# 라운드 — 승격 레시피로 본 슬라이드 즉시 재생성

## 배경
승격은 레시피·분류 규칙을 등록했지만 **이 작업 자체의 슬라이드는 만들지 않아서**,
검수자가 결과물을 보려면 재인제스트를 기다려야 했음. 승격 직후 그 레시피로
이 작업의 슬라이드를 즉시 생성해 검수 흐름으로 바로 재진입시키도록 결선.

## 결정 사항
1. **transform 입력 보존**: 슬라이드 재생성에는 `template_pptx` + `source_slots` 가 필요.
   `pipeline.run_job` 이 신규 유형 큐잉 시(`unknown`) job 에 둘이 있으면
   manifest `transform_inputs` 에 보존(없으면 미보존 → 재생성 스킵).
2. **transform→lint 결선 재사용**: run_job 의 인라인 transform 블록을 `_do_transform` 으로
   추출하고, 승격 경로용 `pipeline.regenerate(recipe, template_pptx, source_slots, cfg,
   workdir, out_pptx, job_id)` 추가. 둘이 같은 헬퍼를 공유(중복 제거).
3. **성공 시 정상 흐름 재진입**: 재생성 성공 → manifest 에 transform/lint 병합 + 상태를
   `route()` 결과(`ready_for_review`/`needs_human_approval`)로. recipe_proposal.promoted 는
   유지(레시피는 등록됨). 즉 "레시피 승격 + 슬라이드 생성 완료, 이제 슬라이드 검수".
4. **best-effort**: 입력 없음/토글 off/템플릿 파일 없음/변환 실패는 승격 자체를 막지 않음.
   상태는 `recipe_promoted` 유지(또는 변환 실패 시 `needs_human_approval`).
   `recipe_proposal.regenerated`(bool) + `regenerate_reason` 기록.
5. **토글**: `assets.regenerate_on_promote`(기본 true).
6. **출력 위치**: `<job_dir>/out.pptx` (store.out_pptx_path 가 바로 인식 → 미리보기·다운로드 활성).

## 산출물
- `engine/pipeline.py` — `_do_transform` 추출, `regenerate()` 추가, run_job 리팩터,
  unknown 분기에서 `transform_inputs` 보존.
- `web/app.py` — promote-recipe 에 재생성 결선(`pipeline.regenerate` 호출·manifest 병합·
  상태 재라우팅), `import pipeline`.
- `web/templates/review.html` — 재생성 결과(성공/사유) 표시, 안내 문구 갱신.
- `config.json` — `assets.regenerate_on_promote`.
- `schemas/job_manifest.schema.json` — `transform_inputs`, recipe_proposal 의
  `regenerated`/`regenerate_reason`.
- `selfcheck/run_web_demo.py` — 실제 합성 템플릿+source_slots 가진 job `demo-web-rg` +
  단계 [15], /jobs 카운트 3→4.

## 검증 (5종 모두 PASS, 레포 자산 무변경)
| selfcheck | 결과 |
| --- | --- |
| run_selfcheck | PASS |
| run_pipeline_demo (transform 리팩터) | PASS |
| run_transform_demo | PASS |
| run_ai_demo | PASS |
| run_web_demo (재생성 [15] 포함) | PASS |

신규 단계 세부([15]):
- `POST /promote-recipe` → 303, transform 성공(recipe=regen_demo, error 없음),
  린트 fails=0, 상태 `ready_for_review` 재진입, `recipe_proposal.regenerated=True`.
- `<job_dir>/out.pptx` 생성, `GET /jobs/{id}/out.pptx` → 200·PK, 리뷰 화면에 "슬라이드 재생성됨".

## 비목표 (의도적 미수행)
- 실제 ingest 경로(`daemon/watch_inbox.process`)가 `transform_inputs` 포함 manifest 를
  store 에 기록하는 결선 — 현재 daemon.process 는 여전히 TODO 스텁(다음).
- 다중 슬라이드 변환(`template_slides[]`).
- shadow 자동 해소·match 술어 정교화.

---

# 라운드 — 데몬 ingest 결선 (inbox → 검수 큐)

## 배경
지금까지 기능(분류·AI 제안·승격·등록·재생성)이 다 갖춰졌지만,
`daemon/watch_inbox.process` 가 TODO 스텁이라 **실제로 파일을 받아 흐름에 태우는 입구**가
비어 있었음. inbox PPTX → pipeline → 검수 store 로 잇는 입구를 채움.

## 결정 사항
1. **soffice 불필요**: 분류·린트에는 도형만 필요하므로 `zipfile` 로 첫 슬라이드 XML 만 읽어
   `geometry.extract_shapes` (PNG 렌더는 검수 UI 의 선택적 preview 가 담당).
2. **store 재사용**: 검수 저장소 레이아웃은 `web.store.JobStore` 를 그대로 씀.
   store.py 는 json/os 만 쓰는 의존성 없는 모듈 → 데몬이 FastAPI 없이 import 가능
   (중복 구현으로 인한 레이아웃 불일치 방지). 데몬은 stdlib + engine + web.store 만 의존.
3. **사이드카 잡 스펙**: 초안 `X.pptx` 옆 `X.job.json` 의
   template_pptx/source_slots/workdir/out_pptx/size 를 job 에 병합 → 결정론 변환 경로
   활성화. 신규 유형이면 변환 보류하되 transform_inputs 보존(승격 재생성 연결).
4. **초안 노출**: 변환 산출물이 없으면 초안 PPTX 를 검수용 out.pptx 로 복사 → 미리보기·다운로드.
5. **경로 해석**: store_dir=env PROPOSAL_WEB_STORE→cfg.web.store_dir→jobs,
   page_types=env PROPOSAL_PAGE_TYPES→cfg.assets.page_types→예시. 상대경로는 루트 기준.
6. **테스트 가능 구조**: `ingest(path, cfg, store, assets, gateway)` 를 순수 함수로 분리,
   `process`/`loop` 는 런타임 묶음을 1회 생성·재사용. loop 는 .pptx 만 처리(사이드카 .json 제외).

## 산출물
- `daemon/watch_inbox.py` — 전면 구현: `_resolve`/`load_assets`/`build_runtime`/`_job_id`/
  `_draft_shapes`/`ingest`/`process`/`loop`. (기존 TODO 스텁 대체)
- `selfcheck/run_pipeline_demo.py` — `MINI_TEXT_SLIDE`/`_pack_pptx` + 단계 [5a]/[5b],
  `daemon`·`web.store` import.
- `README.md` — "데몬 ingest" 절 신설, 자기검증/다음단계 갱신.

## 검증 (5종 모두 PASS + 데몬 stdlib import 확인 + process() 스모크)
| selfcheck | 결과 |
| --- | --- |
| run_selfcheck | PASS |
| run_pipeline_demo (데몬 [5a]/[5b] 포함) | PASS |
| run_transform_demo | PASS |
| run_ai_demo | PASS |
| run_web_demo | PASS |

신규 단계 세부:
- [5a] 표+헤더 초안(연도박스 0) ingest → `body_table` 결정론 분류, store 기록, 초안이 out.pptx 로 노출.
- [5b] 텍스트-only 초안 + 사이드카(template_pptx+source_slots) ingest → `new_type_queued`,
  저장 manifest 에 `transform_inputs` 보존(승격 시 재생성 입력).
- 추가: `/usr/bin/python3`(venv 밖)에서 `from daemon import watch_inbox` import 성공 →
  stdlib + engine + web.store 만 의존함을 확인. `process()` 기본 런타임(AIGateway 포함) 스모크 통과.

## 비목표 (의도적 미수행)
- `source_slots` 자동 추출(초안 → 구조화 콘텐츠) — 사이드카 없으면 변환 없이 분류·린트만.
- launchd plist 작성/운영 가이드.
- inbox 처리 완료 파일의 이동/보관(현재 seen 집합으로 재처리만 방지).
- 다중 슬라이드(첫 슬라이드만 분류·노출).

---

# 라운드 — 데몬 inbox 파일 수명주기 (재기동 재처리 방지)

## 배경
직전 라운드의 `loop()` 는 `seen`(메모리) 으로만 재처리를 막아 **재기동 시 inbox 의 모든
파일을 다시 처리(중복 job 생성)** 하고, 처리된 파일이 inbox 에 무한정 쌓였음.
처리한 파일을 inbox 밖 보관 폴더로 이동해 이 두 문제를 동시에 해소.

## 결정 사항
1. **이동으로 재처리 방지**: 처리 후 초안(+사이드카)을 `_processed`(성공)/`_failed`(실패)로
   `shutil.move`. inbox 에서 사라지므로 재기동해도 재처리 안 됨(seen 의 영속화 불필요).
2. **루프 전용**: 이동은 가동 루프(`loop`)에서만. CLI 단건(`watch_inbox.py <파일>`)은
   사용자가 가리킨 파일을 그대로 둔다(`process(archive_dirs=None)`).
3. **경로**: `daemon.processed_dir`/`daemon.failed_dir`(env `PROPOSAL_PROCESSED_DIR`/
   `PROPOSAL_FAILED_DIR`), 기본 inbox 하위 `_processed`/`_failed`. 빈 문자열이면 기본값.
4. **루프는 .pptx 만 처리**: `_processed`/`_failed` 는 디렉터리라 listing 에서 자동 제외,
   사이드카 .json 도 .pptx 필터로 제외.
5. **이동 실패 무해화**: `_archive` 의 OSError 는 로그만 남기고 흐름 유지.
6. **이름 충돌**: 보관 폴더에 동명 파일이 있으면 시각 접미사(`-HHMMSS`) 부여.

## 산출물
- `daemon/watch_inbox.py` — `_archive`, `_archive_dirs`, `process(archive_dirs=...)`,
  `loop` 가 이동 결선. `import shutil`.
- `config.json` — `daemon` 섹션(`processed_dir`/`failed_dir`).
- `selfcheck/run_pipeline_demo.py` — 단계 [5c].
- `README.md` — "파일 수명주기" 단락, 자기검증/다음단계 갱신.

## 검증 (5종 모두 PASS)
신규 [5c]: inbox 의 초안+사이드카를 `process(..., archive_dirs=...)` → `body_table` 분류,
manifest store 기록 유지, 초안·사이드카가 `_processed` 로 이동(inbox 비워짐) 확인.

## 비목표 (의도적 미수행)
- 보관 폴더의 보관기간 만료 삭제(`retention.source_delete_days`) 적용 — 별도 청소 잡.
- 실패 분류 정교화(현재 ingest 예외 시에만 _failed; 대부분 예외를 내부 흡수).
- `source_slots` 자동 추출, 다중 슬라이드, launchd plist.

---

# 라운드 — 자율 마무리(A 보관청소 / B launchd / C 다중슬라이드 / D 자동 source_slots)

사용자 "끝까지 알아서 진행" 지시로 남은 로드맵을 stdlib 원칙·테스트 가능성 유지하며 일괄 진행.

## A. 보관기간 만료 삭제 (retention)
- `watch_inbox.cleanup_old(dir, days, now=None)` + `_cleanup_all`. loop 가 기동 직후 + 하루 1회 호출.
- `retention.source_delete_days`(기본 30) 초과 파일 삭제. now 주입으로 테스트 결정론.
- 검증 [5d]: 40일 전 1건 삭제·1일 전 보존.

## B. launchd plist + 운영 가이드
- `daemon/com.proposalfactory.watch.plist` 템플릿(`__PYTHON__`/`__PROJECT_DIR__` 치환,
  RunAtLoad/KeepAlive/로그 경로/env 오버라이드 주석).
- `docs/operations.md`: 준비→sed 치환→launchctl load→웹 연동→env 표.
- 검증 [5e]: plistlib 파싱 + Label/가동 인자/RunAtLoad/KeepAlive 확인.

## C. 다중 슬라이드 변환 (template_slides[])
- `transform.apply`: `_apply_one(slide_path, ops, ...)` 추출 → recipe 가 `template_slides:[{template_slide,ops}]`
  이면 슬라이드별 독립 처리(각자 rels), 마지막 1회 pack. 단일 슬라이드 하위호환.
- `pipeline`: `_produce`(생성) / `_recipe_slide_paths` / `_lint_output`(슬라이드별 린트 합산)으로
  분리. run_job·regenerate 양쪽이 다중 슬라이드를 슬라이드별로 린트(거짓 겹침 방지).
- 검증 [transform 8]: 2장 템플릿에 slide1(text+image)/slide2(text) 각각 적용 확인.

## D. source_slots 자동 추출 (slot 명명 초안 → 텍스트)
- `geometry.source_slots_from_shapes(shapes)`: `slot:<key>` 텍스트 도형 → {key:text}. 텍스트 한정.
- 데몬: 사이드카가 `template_pptx` 만 주고 `source_slots` 없으면 초안에서 자동 추출해 채움
  (`daemon.auto_extract_slots` 기본 true). 표/이미지는 여전히 사이드카 필요.
- 검증 [5f]: 단위 추출 + 초안 slot 텍스트("고객이 쓴…")가 별개 표준 템플릿에 반영됨(e2e).

## 산출물
- `engine/transform.py`(_apply_one/template_slides), `engine/pipeline.py`(_produce/_recipe_slide_paths/
  _lint_output, run_job·regenerate 다중슬라이드), `engine/geometry.py`(source_slots_from_shapes),
  `daemon/watch_inbox.py`(cleanup_old/_cleanup_all/loop 정리·자동추출), `config.json`(daemon 확장),
  `daemon/com.proposalfactory.watch.plist`, `docs/operations.md`,
  `selfcheck/run_pipeline_demo.py`([5d][5e][5f]), `selfcheck/run_transform_demo.py`([8]+2장 픽스처).

## 검증 (5종 모두 PASS)
| selfcheck | 결과 |
| --- | --- |
| run_selfcheck | PASS |
| run_pipeline_demo ([5d]/[5e]/[5f] 포함) | PASS |
| run_transform_demo ([8] 다중 슬라이드 포함) | PASS |
| run_ai_demo | PASS |
| run_web_demo | PASS |
추가: `/usr/bin/python3`(venv 밖) 데몬 import + plist 파싱 OK(데몬 stdlib 의존 유지 확인).

## 남은 항목 (결정/외부의존 필요 — 자율 범위 밖)
- PDF·HWP 입력 어댑터: 외부 파서 의존 → engine 의 stdlib 전용 원칙과 충돌. 도입 여부는 사용자 결정.
- 표/이미지 슬롯의 LLM 기반 콘텐츠 추출(현재 자동추출은 텍스트 한정).
- match 술어 정교화(shadow 자동 해소·카운트 가중), 다종 표준 템플릿 라이브러리.

---

# 라운드 — PDF·HWP 입력 어댑터(별도 레이어)

사용자 지시: "PDF·HWP 입력 어댑터 별도 레이어로 진행". engine 의 stdlib 전용 원칙을
보존하기 위해 외부 파서 의존성을 `adapters/` 레이어에 격리하고 lazy import.

## 결정 사항
1. **별도 레이어 `adapters/`**: web 처럼 engine 과 분리. `base.py`(인터페이스·확장자
   레지스트리·디스패치·`AdapterError`/`AdapterUnavailable`·`text_to_result`) + 어댑터별 모듈.
2. **lazy 외부 의존**: pdf=`pypdf`, hwp=`olefile` 을 함수 내부에서 import → 미설치 시
   `AdapterUnavailable`(설치 힌트 포함). engine/데몬은 영향 없음(stdlib 유지).
3. **text 레퍼런스 어댑터**(.txt/.md, 무의존): 레이어 전체를 e2e 로 검증하는 기준.
4. **HWP5 핵심 로직은 순수 함수**: 섹션 inflate(raw-deflate) + 레코드 파싱(PARA_TEXT,
   컨트롤 문자 1/8-WCHAR 처리)을 `parse_records`/`_para_text`/`extract_sections` 로 분리 →
   합성 데이터로 검증. OLE 컨테이너 읽기만 olefile 에 위임(lazy).
5. **추출 = 텍스트 → source_slots(title/body/blocks/text)**. 문서엔 슬라이드 구조가
   없으므로 표/이미지 슬롯은 채우지 않음(사이드카 필요). 변환하려면 사이드카가
   `template_pptx` + `page_type` 지정.
6. **run_job 에 forced page_type**: `job["page_type"]` 가 있으면 분류 대신 그 유형 사용
   (`classify.source="forced"`). 분류 도형이 없는 문서 입력용.
7. **데몬 통합**: loop 가 .pptx + 어댑터 지원 확장자 처리(.job.json 제외). ingest 가
   확장자별 분기. 추출 실패(미설치/형식오류)는 `needs_human_approval`+`adapter_error` 로
   큐잉(데몬 안 죽음). 추출 메타는 manifest `extracted`{kind,title} 보존.

## 산출물
- `adapters/__init__.py`·`base.py`·`text_adapter.py`·`pdf_adapter.py`·`hwp_adapter.py`(신규 레이어).
- `engine/pipeline.py` — run_job forced page_type.
- `daemon/watch_inbox.py` — `_load_sidecar`/`_merge_sidecar`, ingest 확장자 분기, loop 필터 확장,
  `import adapters, notify`.
- `schemas/job_manifest.schema.json` — `extracted`/`source`/`adapter_error`.
- `selfcheck/run_adapters_demo.py`(신규, 6번째 셀프체크).
- `requirements-adapters.txt`(pypdf·olefile, 선택), `docs/operations.md`·`README.md` 갱신.

## 검증 (6종 모두 PASS)
| selfcheck | 결과 |
| --- | --- |
| run_selfcheck / run_pipeline_demo / run_transform_demo / run_ai_demo / run_web_demo | PASS |
| run_adapters_demo (신규 7단계) | PASS |
- [1]레지스트리 [2]text 추출 [3]pdf/hwp AdapterUnavailable [4]HWP5 레코드 파서(평/압축)
  [5]미지원 확장자 [6]데몬 .txt→변환(forced page_type, 추출 title 반영) [7].pdf→검수 큐.
- engine/adapters 모두 `/usr/bin/python3`(venv·파서 밖)에서 import 확인(원칙 유지).

## 비목표 (의도적 미수행)
- HWPX(.hwpx, zip 기반 XML) — 별도 어댑터로 추가 가능(다음).
- 표/이미지 등 비텍스트 슬롯 추출(LLM 영역).
- PDF 레이아웃/표 구조 복원(현재 텍스트만; pypdf extract_text 기준).
- 실제 pypdf/olefile 설치 후 실파일 회귀(미설치 환경이라 lazy 경로만 검증).

---

# 라운드 — 입력 어댑터 실파일 회귀(pypdf/olefile 설치 후)

사용자 지시: "pip install -r requirements-adapters.txt 후 실파일로 회귀 테스트 추가".
venv 에 pypdf 6.12.2 / olefile 0.47 설치 후 실제 추출 경로를 검증.

## 결정 사항
1. **환경 적응형 셀프체크**: `run_adapters_demo.py` 가 pypdf/olefile 설치 여부를 자동 감지
   (`HAS_PDF`/`HAS_OLE`). 설치 시 실파일 단계 실행, 미설치 시 스킵 — 둘 다 PASS 유지
   (run_ai_demo 의 실호출 옵트인과 동일 철학, 단 비용 없으니 env 옵트인 대신 자동 감지).
2. **실 PDF 생성은 stdlib**: `_make_pdf` 가 텍스트 1줄짜리 최소 유효 PDF 를 직접 조립
   (xref 오프셋 정확). pypdf 가 실제로 추출 → 외부 샘플 파일 의존 없음.
3. **HWP 실바이너리 한계 인정**: olefile 은 읽기 전용이라 .hwp(OLE 복합) 합성 불가.
   대신 실제 olefile 로 비-OLE 거부(AdapterError) 검증 + 본문 추출 로직은 합성 레코드로
   이미 검증([4]). 진짜 .hwp 회귀는 사용자 제공 샘플 필요(문서화).
4. **[3]/[7] 환경 양립**: [3]은 미설치→Unavailable / 설치→추출실패 둘 다 허용,
   [7]은 adapter_error 비어있지 않음으로 완화(설치 시 "pypdf" 힌트 문자열 없음).

## 산출물
- `selfcheck/run_adapters_demo.py` — `HAS_PDF`/`HAS_OLE` 감지, `_make_pdf`, 단계 [8](실 PDF
  추출→데몬 변환 왕복)/[9](실 olefile 비-OLE 거부), [3]/[7] 환경 양립.
- `README.md` 자기검증 절 갱신(실파일 단계 자동 실행/스킵 명시).
- venv 에 pypdf·olefile 설치(requirements-adapters.txt).

## 검증
- venv(파서 설치): 6종 모두 PASS. 어댑터 [8] 토큰 'RegressionPDFcontent2026' 가 생성 PDF →
  pypdf 추출 → 표준 템플릿 슬라이드에 반영 확인. [9] 비-OLE .hwp → olefile 로 형식 거부 확인.
- 시스템 python3(파서 없음): run_adapters_demo PASS([3] Unavailable, [8]/[9] 스킵) — 원칙 유지.

## 비목표 (의도적 미수행)
- 진짜 .hwp 바이너리 픽스처 생성(olefile read-only, OLE writer 의존성 없음) — 사용자 샘플 필요.
- 한글(비-latin1) 텍스트 PDF(폰트 임베딩 필요) — 회귀는 ASCII 토큰으로 충분.
- HWPX, 표/이미지 슬롯 추출.

---

# 라운드 — HWPX(.hwpx) 어댑터 (stdlib, 외부 의존 없음)

자율 진행. 합성 불가했던 .hwp(OLE)와 달리 HWPX 는 ZIP+XML(.pptx 계열)이라 표준
라이브러리만으로 실파일 생성·추출·e2e 검증이 가능 → 한글 문서 입력을 의존성 없이 완성.

## 결정 사항
1. **무의존**: `Contents/section*.xml` 의 `<hp:p>`(문단)/`<hp:t>`(런)을 zipfile+정규식으로
   추출. olefile/pypdf 불필요 → 항상 활성(.txt/.md 와 동급).
2. **순수 함수 분리**: `extract_text_from_sections(section_xmls)` 로 파싱 로직 분리(테스트 용이),
   `extract_hwpx` 는 ZIP 읽기만 담당. 인라인 태그 제거 + HTML 엔티티 언이스케이프.
3. **graceful**: ZIP 아님/섹션 없음/본문 없음 → AdapterError(데몬은 검수 큐로 폴백).

## 산출물
- `adapters/hwpx_adapter.py`(신규), `adapters/__init__.py` 등록.
- `selfcheck/run_adapters_demo.py` — `_make_hwpx`(실 .hwpx 생성) + 단계 [10](추출 + 데몬
  변환 왕복), [1] 레지스트리에 hwpx 추가.
- `README.md`/`docs/operations.md` 갱신.

## 검증
- venv·시스템 python3 양쪽에서 run_adapters_demo PASS. [10] 은 외부 의존이 없어 **항상 실행**:
  생성한 .hwpx → "HWPX 제안 개요" 추출 → 사이드카(template+page_type) → 표준 템플릿 슬라이드 반영.
- 6종 셀프체크 전부 PASS.

## 남은 항목은 사용자 입력/결정 필요
- 실 `.hwp` 바이너리 회귀: olefile read-only(OLE writer 의존성 없음) → **사용자 제공 .hwp 샘플 필요**.
- 표/이미지 등 비텍스트 슬롯 추출: LLM/휴리스틱 영역, 프롬프트·제품 결정 필요.
- 다종 표준 템플릿/레시피 라이브러리: 실제 템플릿 콘텐츠 작성(자산) 필요.

---

# 라운드 — match 술어 정교화(shadow 자동 해소 + 정확 카운트 가중)

사용자 지시. 기존엔 신규 규칙이 기존 규칙에 가려지면(shadow) 말미에 추가 + 경고만 해서
도달 불가였음. 안전하게 해소하도록 정교화.

## 결정 사항
1. **정확 카운트 가중으로 좁히기**: shadow 발생 시 `_specialized_against(가리는_규칙, sig)` 가
   가리는 규칙의 모든 술어를 포함하고(⊇ 제약) 시그니처의 정확 카운트
   (`n_table/n_image/n_year_box/n_text` + `has_title`)를 추가해 더 구체적인 규칙을 만든다.
2. **가리는 규칙 앞에 삽입**: 특수 규칙 ⊆ 가리는 규칙이므로, 그 앞에 두면 이 시그니처만
   신규 유형으로 분기되고 가리는 규칙이 잡던 다른 구조는 그대로 유지(라우팅 보존 보장).
   - 핵심 안전 근거: 더 앞선(가리는 규칙보다 먼저인) 엔트리는 sig 를 안 잡으므로
     삽입 위치(가리는 규칙 인덱스)는 그들 뒤 → 그들 라우팅 불변. 가리는 규칙은 특수 규칙의
     여집합만 잃음(= 의도한 분기).
3. **구분 불가 시 미해소 기록**: 가리는 규칙이 이미 모든 카운트를 정확 고정해 더 좁힐 게
   없으면 말미 추가 + `shadowed_unresolved:<type>`(운영자 수동 조정 안내).
4. **비-shadow 는 종전대로** 자연 시그니처(존재/부재) 규칙을 말미 추가(일반성 유지).
5. **투명성**: `recipe_proposal.page_type_resolution`(appended/specialized_before:T/
   shadowed_unresolved:T) + 감사 로그 + 검수 UI 표기.

## 산출물
- `web/app.py` — `_specialized_against`, `_register_page_type` 재작성(shadow 해소·삽입·
  갱신 분기), `_write_page_types` 추출, promote 라우트에 resolution 전달.
- `web/templates/review.html` — specialized/shadowed_unresolved 안내.
- `schemas/job_manifest.schema.json` — `page_type_resolution`.
- `selfcheck/run_web_demo.py` — 단계 [16](shadow 해소: 특수 규칙이 일반 규칙 앞 + 라우팅 보존).

## 검증 (6종 모두 PASS)
신규 [16]: `gen_table{n_table_min:1}` 가 있는 page_types 에 sig{n_table:1,n_text:2,...} 승격
→ `specialized_before:gen_table`, 특수 규칙(index 0)이 gen_table(1) 앞, 이 구조→special2
(결정론), 표+텍스트5 구조→gen_table(보존). 기존 [13](비-shadow append) 회귀 PASS.

## 비목표
- 다중 키 최소-구별집합 탐색(현재는 모든 미고정 카운트를 정확값으로 추가 — 보수적·안전 우선,
  다소 brittle 하나 운영자가 이후 page_types 에서 넓힐 수 있음).
- shadow 가 여러 겹일 때의 재귀 해소(첫 가리는 규칙 기준 1회).

---

# 라운드 — 다중 페이지 분류(A) + AI 콘텐츠 매핑(B), 1:1·verbatim

사용자 비전: 엉망 초안(다업체 혼합)을 표준 디자인에 일관 반영. 제약 확정 — **페이지 1:1**
(분리/병합 없음), **이미지 그대로**(디자이너 추후 변경), **문구 변경 없음**(초안 텍스트 verbatim).

## A. 다중 페이지 분류
- `pipeline.classify_deck(slides, assets, gateway)` — 슬라이드별 1:1 독립 분류 → pages[].
- daemon `_deck_slides` + ingest 분기: 다중 슬라이드 .pptx(사이드카 변환 없을 때) → deck manifest
  (kind/page_count/pages, 상태=미정의 있으면 new_type_queued).
- schema(kind/page_count/pages), review.html 덱 페이지 카드.
- 검증 [6]: 2슬라이드 덱 → p0=body_table, p1=unknown, deck manifest 기록.

## B1. AI 콘텐츠 매핑(게이트웨이)
- `ai.AIGateway.map_content(blocks, slots, hint)` — **문구 변경 불가 설계**: LLM 은 인덱스만
  반환({assign:{slot:block_idx}}), 텍스트 값은 호출자가 초안에서 verbatim 복사. 유효 키/인덱스만 통과.
- config ai.cloud.map_model(기본 haiku)/map_max_tokens. _MAP_SYSTEM 에 "텍스트 절대 변경 금지" 명시.
- 검증 [9b]: 유효 배정 보존·무효 인덱스/키 필터·응답에 인덱스(정수)만·haiku 1회.

## B2. 덱 파이프라인(분류→매핑→변환)
- `pipeline.build_page_source_slots(draft_shapes, recipe, gateway)` — text_inject 슬롯에 초안
  텍스트 verbatim 배정(AI map_content 또는 순서 폴백). 값은 항상 초안 그대로.
- `pipeline.run_deck(slides, assets, cfg, gateway, std_template_pptx, workdir)` — 1:1 페이지별
  분류→매핑→표준 템플릿 변환→린트, 페이지별 표준화 출력 + deck manifest(상태 집계).
- 검증 [7a] 오프라인 positional·문구 verbatim, [7b] mock AI 인덱스 배정(swap) 준수·문구 변경 0.

## 검증 (6종 모두 PASS)
run_selfcheck/run_pipeline_demo([6][7])/run_transform_demo/run_ai_demo([9b])/run_web_demo/run_adapters_demo.

## 의도적 미수행 (실파일 검증/패키징 후속)
- **단일 파일 덱 조립**(N 표준화 페이지 → 1 pptx) + **초안 이미지 carry-over(원위치 그대로)**:
  OOXML 슬라이드 복제·미디어 충돌·rels 재매핑이라 실 53p deck 으로 검증 필요. 현재 run_deck 은
  페이지별 표준화 출력을 생성(핵심 지능: verbatim 매핑·1:1 변환은 검증 완료).
- 표/구조(shape_rebuild) 슬롯의 매핑(현재 text_inject 슬롯 중심).
- 데몬 deck→run_deck 자동 결선(현재 deck 은 분류까지; run_deck 은 직접 호출/셀프체크로 검증).

---

# 라운드 — group_fill op + 운영자 페이지별 타입 지정 (디자인가이드2.0 실템플릿 대응)

실제 SKB 표준 템플릿(디자인가이드2.0.pptx) 분석 결과: 슬롯 102개(이미 명명됨) 중 grpSp 66·sp 29·
표 5·이미지 2. 두 이슈 → 사용자 결정: (1)그룹 채우기 op 개발 (2)운영자 페이지별 타입 지정.

## 결정·구현
1. **group_fill op**: 슬롯이 grpSp 면 내부 <p:sp> 텍스트박스들을 입력 배열로 순서대로 verbatim
   채움(`_fill_inner_texts`). 그룹 비텍스트 도형/구조 보존. _OPS/_VALID_OPS 등록.
2. **운영자 타입 지정**: `run_deck(forced_types=list|dict)` — 페이지별 타입 명시 시 분류 우회
   (source="operator"). 시그니처로 구분 안 되는 grouped 텍스트 타입 대응.

## 실파일 검증(디자인가이드2.0)
- asis_tobe(sp 슬롯): slide11 에 asis/tobe 문구 verbatim 주입 성공.
- group_fill: slide12 asis_summary(내부 2박스)에 2값 순서대로 verbatim, 구조(txBody 2) 보존.

## 셀프체크(6종 PASS)
- run_transform_demo [9]: group_fill(그룹 2박스 verbatim·원본 치환·구조 보존).
- run_pipeline_demo [7c]: forced_types 로 page_types 비어도 지정 타입 변환(source=operator).

## 분석으로 드러난 사실(향후)
- 분류 충돌: 시그니처 5개로 grouped 텍스트 타입 구분 불가 → 당분간 운영자 지정. 내용기반 LLM 분류는 보류.
- 타입 카탈로그(2.0 슬롯셋): 표지/목차/파트간지/asis_tobe/본문(body_head+body)/좌우리스트/액션4분할/장비표 등 ~10종.
- 그룹 자동 매핑(초안→group_fill 배열)은 미구현 — 현재 group_fill 값은 명시 source_slots/사이드카.

---

# 라운드 — SKB 표준 템플릿 타입 라이브러리 22종 작성·검증

디자인가이드2.0(슬롯 102개/38슬라이드)에서 구분되는 슬롯셋 22종 식별 → 타입별 recipe 자동 생성
(슬롯 태그 기반 op: sp→text_inject, grpSp→group_fill, 표→table_rebuild, 이미지→image_reuse) →
**실제 템플릿 슬라이드 변환으로 22/22 전수 검증** → 자산 저장.

## 산출물
- `assets/recipes/skb/*.json` — 22 recipe(cover/toc/part_divider/section_title/asis_tobe/
  asis_tobe_summary/body_block/body_with_head_text|block/list_two_col/list_full_two_col/
  table_pair_body/device_table_image|dual/device_title_table/action_grid_4/two_items/
  lead_body|title/eval_keypoint/closing_messages2|3).
- `assets/page_types.skb.json` — 22종 등록(match 비움 = 운영자 페이지별 지정).
- `docs/template-authoring.md` 11장: 라이브러리 사용법.

## 검증
- recipe 생성 시 각 타입을 실제 디자인가이드2.0 슬라이드로 transform → 22/22 오류 없음.
- run_deck 실파일(운영자 지정 forced_types): p0 asis_tobe→out+lint PASS, p1 section_title→out+lint
  FAIL→needs_human_approval(설계대로 사람 검수 라우팅). verbatim·1:1·operator source 확인.
- 6종 셀프체크 회귀 PASS(자산 추가는 셀프체크 무영향).

## 남은 것
- 표준 템플릿 pptx(디자인가이드2.0, 8.9MB)는 미커밋(사용자 자산) — recipe 는 그 내부 슬라이드 경로 참조,
  런타임에 std_template_pptx 로 제공.
- group_fill 슬롯의 초안→배열 자동 매핑, 단일 파일 덱 조립 + 이미지 carry-over(후속).

---

# 라운드 — 단일 파일 덱 조립 + 초안 이미지 carry-over (engine/deck.py)

제약: 페이지 1:1, 이미지 그대로, 문구 verbatim. 실 초안 파일이 없어 디자인가이드2.0 실슬라이드를
기질로 검증(사용자 실초안은 그대로 투입 가능).

## 산출물
- `engine/deck.py` — `pics_from`(초안 <p:pic>+미디어 추출), `_carry_pics`(위치 그대로 주입·미디어
  namespaced·rels·CT), `assemble`(표준 템플릿 복제 기반 1:1 다중 슬라이드 조립·presentation
  sldIdLst 재지정·CT override). 표준 라이브러리만.
- `engine/pipeline.run_deck(out_deck=, draft_pptx=)` — 조립 경로 결선(+ 조립 슬라이드 린트·라우팅).
- `engine/transform.py` — image_reuse/table_rebuild **source 없으면 skip**(템플릿 자리 보존;
  이미지는 carry-over, 표는 템플릿 유지). 조립 견고성 확보.
- `selfcheck/run_pipeline_demo.py [8]` — 합성 템플릿+이미지 초안으로 조립·carry·구조 검증.

## 검증
- 합성 [8]: 단일덱 sldIdLst=2·XML 유효·verbatim·carry 이미지.
- 실파일(디자인가이드2.0): run_deck(out_deck, draft) → out 생성, sldIdLst 2, carry 미디어 5,
  p0 PASS·p1 FAIL→needs_human_approval(린터 라우팅 설계대로). 6종 회귀 PASS.

## 남은 것
- PowerPoint 실제 열람 확인(헤드리스 구조검증까지만 수행) — 사용자 확인 권장.
- 조립 시 원본 템플릿 슬라이드 파트가 미표시로 잔존(파일 크기) — 추후 정리.
- group_fill 슬롯의 초안→배열 자동 매핑, table_rebuild 자동 입력.

---

# 라운드 — 덱 조립 후처리: 파일 크기 정리 + carry 이미지 z-order

사용자 피드백(샘플 확인): 정상 열림. (1)파일 크기 큼 (2)상단 회색 진함 (3)타이틀 텍스트 안 보임.

## 수정
1. **파일 크기**: `deck._prune` — 출력 sldIdLst 에 없는 원본 템플릿 슬라이드 + 어떤 .rels 도
   참조 않는 고아 미디어 삭제(마스터/레이아웃/테마 보존). 실측 9.4MB → **1.68MB**.
2. **타이틀 가림**: carry 이미지를 spTree 끝(z-top)에서 **그룹 속성 직후(z-bottom)** 로 이동
   → 템플릿 텍스트/타이틀이 위에 렌더(디자이너가 추후 앞으로). 타이틀 텍스트는 원래 XML 에
   정상 존재했음(가림 문제였음).
3. **상단 회색**: 템플릿 마스터/레이아웃 배경 디자인 — 엔진이 바꾸지 않음(템플릿 측 값).
   draft==template 샘플의 이미지 중복으로 진해 보이던 부분은 z-bottom 으로 완화.

## 검증
- 6종 셀프체크 PASS. 샘플 재생성 1.68MB, 구조 유효(전체 XML 파싱). carry pic 이 spTree 앞으로 이동 확인.

---

# 라운드 — 내용 기반 자동 타입 분류 (운영자 지정 부담 제거)

문제: 시그니처 5개로는 grouped 텍스트 타입 구분 불가 → 지금까지 운영자가 페이지별 타입 지정 필요.
해결: 슬라이드 내용+레이아웃을 LLM 에 주어 타입 카탈로그에서 자동 선택.

## 산출물
- `classify.content_profile(shapes)` — 시그니처 + 텍스트 스니펫 + 위치/크기 + 도형 구성(토큰 제한).
- `ai.AIGateway.classify_page(profile, catalog, hint)` — type+desc 카탈로그 기반 LLM 분류,
  라벨 화이트리스트 검증. config page_classify_model(기본 classify_model=haiku).
- `pipeline._classify_page` — 결정론 → 내용 LLM → unknown. run_deck/classify_deck 에 결선.
  게이트웨이 없거나 보안망이면 결정론만(안전).

## 검증 (6종 PASS)
- run_ai_demo [9c]: 카탈로그 라벨 반환·밖 라벨 None(화이트리스트)·빈 카탈로그 None.
- run_pipeline_demo [7d]: forced_types 없이 mock gateway.classify_page → source=content_llm·자동 변환.

## 효과 / 남은 것
- 운영: inbox 초안 → 페이지 타입 자동 분류 → 변환. 운영자 지정은 폴백/우선(forced_types) 유지.
- 한계: geometry 가 최상위 도형만 추출 → 그룹 내부 텍스트는 프로필에서 일부 누락(분류 정확도 영향).
  → 다음: geometry 그룹 재귀 추출로 분류·매핑 정확도 향상.

---

# 라운드 — geometry 그룹 내부 재귀 추출 (분류·매핑 정확도 기반 개선)

문제: 디자인가이드2.0 텍스트의 64%가 그룹(grpSp) 안 → 최상위만 보는 extract_shapes 로는
분류/매핑이 그룹 텍스트를 놓침(일부 슬라이드는 top-level 텍스트 0개).

## 산출물
- `geometry._parse_shape_block` — 도형 블록 파싱 공통 헬퍼(중복 제거).
- `geometry.extract_shapes_deep` — 그룹 재귀, 자식→절대좌표 변환(_group_xfrm/_group_fn 합성),
  리프 도형만 반환. **extract_shapes(린터/시그니처)는 동작 불변**(분리).
- `daemon._deck_slides` — 초안 분류·매핑은 deep 사용. 출력 린트는 기존 extract_shapes 유지.

## 검증
- 실측(디자인가이드2.0): top→deep 텍스트 slide6 5→25, slide12 4→21, slide13 14→34,
  slide7 0→10, slide9 0→11. 시그니처도 풍부(이미지/제목 감지).
- run_transform_demo [10]: 그룹 2박스 deep 추출 + 절대좌표 변환(200,200) 확인.
- 6종 회귀 PASS(extract_shapes 불변 → 기존 셀프체크 무영향; 합성 픽스처는 flat 이라 deep==top).

## 효과
- content_profile 가 그룹 텍스트까지 → classify_page 분류 정확도↑.
- _text_blocks/build_page_source_slots 도 그룹 텍스트 surfacing(매핑 후보↑).

---

# 라운드 — group_fill 슬롯 초안 자동 매핑 (verbatim 배열)

deep 추출로 그룹 텍스트가 보이게 됐으므로, 초안 콘텐츠를 표준 템플릿 그룹 슬롯에 자동 배정.

## 산출물
- `ai.map_content` — group_fill 슬롯은 **인덱스 배열**, text_inject 는 단일 인덱스 반환(문구 verbatim,
  모델은 인덱스만). int/list 검증·필터.
- `pipeline.build_page_source_slots` — text_inject/group_fill 모두 처리: 단일→문자열, 그룹→문자열 배열
  (모두 초안에서 verbatim). 폴백(순서 기반)도 그룹은 리스트로.

## 검증 (6종 PASS)
- run_ai_demo [9d]: group_fill 배열 보존(순서)·text 단일·무효 인덱스 배열 제외.
- run_pipeline_demo [7e]: AI 배정 → summary=['그룹라인1','그룹라인2'](verbatim 배열), title 단일; 폴백도 동작.

## 남은 것
- 표 슬롯 자동 입력(table_rebuild — label/value 구조 추출), 실 .hwp 회귀, 비렌더 폰트 리맵.

---

# 라운드 — 표 자동입력 / 폰트 리맵 / HWPX·.hwp / 실열람 검증 (4종)

## A. 표 슬롯 자동 입력
- geometry._parse_shape_block: graphicFrame 표 셀텍스트(rows) 추출 → shp["table"].
- pipeline._draft_tables + build_page_source_slots: 초안 2열 표 → [{label,value}] verbatim 자동 입력
  (헤더 제외). 복합/병합/단일열 표는 건너뜀(table_rebuild source 없으면 템플릿 표 보존).
- 검증 [7f]: 2열 표 자동 입력, 3열 복합표 보존.

## B. 폰트 리맵
- config.fonts.remap(공체/뫼비우스/가는각진제목체 → KoPub). transform._remap_fonts 가 _apply_one
  에서 typeface 전수 치환(템플릿 자체 폰트 포함). 실파일 slide3 공체 6→0 확인.
- 검증 [transform 11]: 공체 → KoPub 리맵.

## C. HWPX e2e / .hwp
- HWPX 데몬 e2e 는 run_adapters_demo [10](사이드카+변환)에서 이미 커버. olefile 설치로 .hwp 경로 활성.
- 바이너리 .hwp: olefile read-only → 합성 불가. 레코드 파서[4]·olefile 경계[9] 검증됨.
  **실 round-trip 은 사용자 .hwp 샘플 필요**(문서화).

## D. 실열람 정밀 확인
- deck.validate: content-type/rels dangling/미정의 r:id 검사. 노트(notesSlides) 제거(원본 슬라이드
  참조 dangling 방지 — 실 샘플에서 발견·수정).
- 검증 [8b]: 합성 조립 출력 문제 0건. 실 Desktop 샘플 0건 + **soffice→PDF 렌더 성공(실열람 확인)**.
- 6종 셀프체크 PASS.

폰트 zip 38종 설치 완료. 남은 미설치: 굴림/맑은고딕(Windows 기본), 산돌고딕B, 뫼비우스/공체/
가는각진제목체(→리맵으로 대체), 나눔스퀘어.
