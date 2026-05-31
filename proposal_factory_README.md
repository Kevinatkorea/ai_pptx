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
    transform.py         레시피 적용(4대 변환연산) — Phase-1 로직 이식 지점
    capture.py           검증 직전 캡처(PNG + 기하 스냅샷)
    learn.py             직원 수정 diff → 교정 기록(학습)
    pipeline.py          오케스트레이터(ingest→classify→transform→capture→lint→route)
    ai.py                3단 지능 게이트웨이 + 월 예산 상한
    notify.py            검수/승인 메일(직링크)
    pptx_io.py           unpack/pack/clean/render 래퍼
  schemas/               JSON Schema 6종(자산·잡·교정기록)
  assets/                자산 예시(page_types, recipe, design_guide=실제 결함 7종 시드)
  selfcheck/             자기검증 2종
  daemon/watch_inbox.py  /inbox 폴더 감시(launchd 상시 가동) + 수동 실행
```

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

## AI 모드
- 평상시: `ai.mode=tiered` — 결정론+로컬LLM이 대부분 처리, Claude는 신규/모호/레시피 작성만.
- 보안망: `ai.cloud.enabled=false` → 1~2단만 사용(토큰 0). 신규 케이스는 사람 큐로.
- 월 예산 초과 시 자동으로 로컬/사람 폴백(`ai.fallback_when_over_budget`).

## 실행
```
python3 selfcheck/run_selfcheck.py        # 린터 v1 자기검증
python3 selfcheck/run_pipeline_demo.py    # 파이프라인 + 학습 루프 자기검증
python3 daemon/watch_inbox.py             # /inbox 상시 감시
python3 daemon/watch_inbox.py <파일>      # 수동 1건 처리
```

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
- 파이프라인: 결정론 분류 → 검수 대기, 신규 유형 → 큐잉, 직원수정 diff 검출 모두 PASS.
- 자기검증 중 발견·보완: 텍스트 박스 거짓 겹침 → 잉크 박스 도입, `wrap=none` 라벨 폭 보정, 표 높이는 행합 사용.

## 다음 단계
1. `transform.py`에 Phase-1 변환 로직(텍스트주입/표재생성/이미지이식/도형재구성) 이식.
2. 웹 UI(FastAPI) — 검수·승인·학습 화면.
3. 로컬 LLM(Ollama) 분류 연동, Claude 레시피 작성 연동.
4. PDF·HWP 입력 어댑터, 다종 템플릿 확장.
