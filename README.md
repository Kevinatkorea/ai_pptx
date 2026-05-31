# Proposal Factory — 제안서 템플릿 변환 지속학습 시스템

![License](https://img.shields.io/badge/license-Proprietary-red.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Engine](https://img.shields.io/badge/engine-stdlib--only-success.svg)
![Self-checks](https://img.shields.io/badge/self--checks-6%20suites-brightgreen.svg)
![Status](https://img.shields.io/badge/status-active-blue.svg)

맥미니가 상시 '학습 공장'으로 가동되며, 고객 초안을 사내 표준 템플릿에 반영하고
페이지 레이아웃 결함을 누적 학습한다. **AI를 쓸 때와 못 쓸 때(보안망) 모두** 동작하도록
"규칙을 만드는 공장(AI)"과 "규칙을 적용하는 엔진(결정론)"을 분리한 구조다.

> 저장소: <https://github.com/Kevinatkorea/ai_pptx> ·
> 상세 문서: [`proposal_factory/README.md`](proposal_factory/README.md) ·
> 운영(launchd) 가이드: [`proposal_factory/docs/operations.md`](proposal_factory/docs/operations.md) ·
> 작업 이력: [`tasks/todo.md`](tasks/todo.md)

## 아키텍처

- **학습 공장(AI, 비보안망)**: 템플릿·샘플 분석 → 자산(레시피·디자인 가이드) 생성.
- **현장 엔진(결정론, 보안망)**: 자산만으로 분류 → 변환 → 검증 → 라우팅. 런타임 AI 호출 0.
- **3단 지능**: 결정론(무료) → 로컬 LLM(Ollama, 무료) → Claude API(월 예산 상한; 신규/모호/레시피 작성만).
- **레이어 분리**: `engine/` 은 **표준 라이브러리 전용**. 외부 의존성(FastAPI, pypdf, olefile)은
  `web/`·`adapters/` 레이어에 격리하고 lazy import — 보안망에서도 엔진이 그대로 돈다.

## 파이프라인 한눈에

```
inbox 입력            분류                 변환                   검증·검수
(.pptx/.pdf/.hwp/      결정론 시그니처       4-op + 다중 슬라이드    린터(겹침·넘침·이탈)
 .hwpx/.txt) ──┐       → 로컬 LLM           (text/table/image/      → 자동수정/사람승인
adapters 추출  ├─→ classify ─→ (recipe) ─→ shape) ─→ transform ─→ → 검수 웹 UI(/review)
사이드카/자동  ┘       → 클라우드            page_types 매칭         → 승인 → 출력
 source_slots         (신규 → AI 레시피      자동 등록·shadow 해소
                       초안 제안 → 승격)     → 슬라이드 재생성
```

직원 수정본 업로드 → 후보와 diff(이동·크기·폰트·텍스트) 누적 → 반복 교정은 사람 승인 후 자산으로 승격(지속학습).

## 저장소 구조

```
proposal_factory/
  engine/        결정론 엔진(geometry·linter·classify·transform·pipeline·ai·learn·notify·pptx_io) — stdlib 전용
  web/           FastAPI 검수·승인 UI + AI 레시피 초안 승격
  daemon/        inbox 감시 ingest(파일 수명주기·보관기간) + launchd plist
  adapters/      PDF/HWP/HWPX/텍스트 입력 어댑터(외부 의존 lazy)
  schemas/       JSON Schema(자산·잡·교정기록)
  assets/        page_types·recipe·design_guide 예시
  selfcheck/     6종 자기검증
  docs/          작성·운영 가이드
tasks/           todo.md(설계·라운드 이력) · Lessons.md
```

## 빠른 시작

```bash
# 클론
git clone https://github.com/Kevinatkorea/ai_pptx.git
cd ai_pptx

# 엔진 자기검증 (표준 라이브러리만 — 외부 설치 불필요)
python3 proposal_factory/selfcheck/run_selfcheck.py        # 린터
python3 proposal_factory/selfcheck/run_pipeline_demo.py    # 오케스트레이션 + 데몬 ingest
python3 proposal_factory/selfcheck/run_transform_demo.py   # transform 4-op + 다중 슬라이드
python3 proposal_factory/selfcheck/run_ai_demo.py          # AI 게이트웨이(mock)
python3 proposal_factory/selfcheck/run_adapters_demo.py    # 입력 어댑터

# 웹 검수 UI (venv 필요)
python3 -m venv .venv
.venv/bin/pip install -r proposal_factory/requirements-web.txt
.venv/bin/uvicorn web.app:app --host 127.0.0.1 --port 8000   # (cwd: proposal_factory/)

# 입력 어댑터(PDF/HWP) 선택 의존성
.venv/bin/pip install -r proposal_factory/requirements-adapters.txt   # pypdf·olefile
```

상시 가동(launchd)·환경변수·NAS 연동은 [운영 가이드](proposal_factory/docs/operations.md) 참고.

## 자기검증

6종 셀프체크(린터·파이프라인+학습·transform·AI 게이트웨이·웹 UI·입력 어댑터) 전부 PASS.
`engine`·`daemon`·`adapters` 는 외부 파서 없이도 시스템 `python3` 로 동작한다(보안망 원칙).

## 입력 형식

| 형식 | 처리 | 의존성 |
| --- | --- | --- |
| `.pptx` | 도형 추출 → 분류·린트(+사이드카/자동 source_slots 변환) | 없음 |
| `.txt` / `.md` / `.hwpx` | 텍스트 추출 → `source_slots` | 없음(stdlib) |
| `.pdf` | 텍스트 추출 | `pypdf` |
| `.hwp` (HWP 5.0) | 본문 추출 | `olefile` |

문서 입력(.pdf/.hwp/.hwpx/.txt)은 사이드카 `<name>.job.json` 에 `template_pptx`+`page_type` 를 지정해 변환한다.

## 라이선스

**Proprietary — All Rights Reserved.** 사내 독점 소프트웨어로, 저작권자의 사전 서면 허가
없이 복제·수정·배포·사용할 수 없다. 전문은 [`LICENSE`](LICENSE) 참고.

© 2026 Mostvisual. All rights reserved.
