# 운영 가이드 — 맥미니 상시 가동(launchd)

inbox 감시 데몬(`daemon/watch_inbox.py`)을 launchd 로 상시 가동하고, 검수 웹 UI 를
함께 띄우는 절차. 데몬은 **표준 라이브러리 + engine + web.store** 만 쓰므로 soffice 없이도
동작한다(미리보기 PNG 는 검수 UI 의 선택 기능).

## 1. 사전 준비

```bash
cd /path/to/proposal_factory
python3 -m venv ../.venv                      # 웹 UI 용(데몬은 시스템 python3 로도 가동 가능)
../.venv/bin/pip install -r requirements-web.txt
mkdir -p logs
```

NAS 마운트(`config.json` 의 `nas.mount`)와 그 아래 `inbox/` 가 존재해야 한다.
보관 폴더(`inbox/_processed`, `inbox/_failed`)는 데몬이 자동 생성한다.

## 2. launchd 등록

`daemon/com.proposalfactory.watch.plist` 템플릿의 placeholder 를 치환한다:

| placeholder | 값 |
| --- | --- |
| `__PYTHON__` | `which python3` 결과(예: `/usr/bin/python3` 또는 venv 의 python) |
| `__PROJECT_DIR__` | `proposal_factory` 절대경로 |

```bash
sed -e "s#__PYTHON__#$(which python3)#g" \
    -e "s#__PROJECT_DIR__#$(pwd)#g" \
    daemon/com.proposalfactory.watch.plist \
    > ~/Library/LaunchAgents/com.proposalfactory.watch.plist

launchctl load  ~/Library/LaunchAgents/com.proposalfactory.watch.plist   # 등록 + 즉시 가동
launchctl list | grep proposalfactory                                    # 상태 확인
tail -f logs/watch.out.log                                               # 처리 로그
```

해제/재적용:

```bash
launchctl unload ~/Library/LaunchAgents/com.proposalfactory.watch.plist
# plist 수정 후 다시 load
```

## 3. 동작

- `inbox/*.pptx` 감지(5초 폴링) → 분류 → 린트 → 검수 store 기록.
- `inbox/*.{pdf,hwp,hwpx,txt,md}` 는 입력 어댑터가 텍스트를 추출해 `source_slots` 로 만든다.
  `.txt`/`.md`/`.hwpx` 는 표준 라이브러리만으로 동작(추가 설치 불필요).
  `.pdf`/`.hwp` 는 `../.venv/bin/pip install -r requirements-adapters.txt`(pypdf·olefile) 필요 —
  미설치 시 해당 건은 `needs_human_approval` 로 큐잉되고 데몬은 계속 가동된다.
  문서 입력은 사이드카가 `template_pptx` + `page_type` 를 지정해야 변환된다.
- 사이드카 `X.job.json` 이 있으면 `template_pptx`/`source_slots` 로 결정론 변환,
  없고 초안 도형이 `slot:<key>` 로 명명돼 있으면 source_slots 를 자동 추출한다.
- 처리한 초안(+사이드카)은 `inbox/_processed`(성공)/`inbox/_failed`(실패)로 이동 →
  재기동 시 재처리 방지.
- 보관 폴더의 `retention.source_delete_days`(기본 30) 초과 파일은 기동 직후 + 하루 1회 삭제.

수동 1건 처리(이동 없음):

```bash
python3 daemon/watch_inbox.py /path/to/draft.pptx
```

## 4. 검수 웹 UI

```bash
../.venv/bin/uvicorn web.app:app --host 127.0.0.1 --port 8000
# 브라우저: http://127.0.0.1:8000/
```

데몬과 웹은 같은 검수 store 를 공유해야 한다 — 둘 다 `PROPOSAL_WEB_STORE`(또는
`config.json` 의 `web.store_dir`)가 같은 경로를 가리키게 한다.

## 5. 환경변수 오버라이드

| 변수 | 용도 |
| --- | --- |
| `PROPOSAL_WEB_STORE` | 검수 job 저장소 경로(데몬·웹 공유) |
| `PROPOSAL_PAGE_TYPES` | page_types 라이브러리 파일 경로 |
| `PROPOSAL_RECIPES_DIR` | 승격 레시피 라이브러리 디렉터리 |
| `PROPOSAL_PROCESSED_DIR` / `PROPOSAL_FAILED_DIR` | inbox 보관 폴더 |
| `ANTHROPIC_API_KEY` | 클라우드(분류·레시피 작성) 사용 시 |
| `PROPOSAL_PREVIEW_DISABLE` | 검수 UI 미리보기 렌더 강제 비활성화 |

`config.ai.mode=secure_offline` 이면 LLM 호출 없이 결정론만으로 동작한다(보안망).
