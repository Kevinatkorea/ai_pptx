"""watch_inbox.py — /inbox 폴더 감시(폴링) + 수동 실행 진입점.

Mac: launchd 로 상시 가동. 새 PPTX 감지 → ingest(분류→[변환]→린트→라우팅) →
검수 store 기록 + 메일 알림. 표준 라이브러리 + engine 만 사용(soffice 불필요).

검수 저장소 레이아웃은 `web.store.JobStore` 를 재사용한다 — store.py 는 json/os
만 쓰는 의존성 없는 모듈이라 FastAPI 없이도 import 된다(중복 구현으로 인한 레이아웃
불일치 방지).

사이드카 잡 스펙: 초안 `X.pptx` 옆에 `X.job.json` 이 있으면 그 안의
`template_pptx`/`source_slots`/`workdir`/`out_pptx`/`size` 를 job 에 병합해 결정론
변환 경로(또는 신규 유형일 때 transform_inputs 보존)를 활성화한다.
"""
import json
import os
import re
import shutil
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.config import load_config
from engine import ai, geometry, notify, pipeline
from web.store import JobStore
import adapters

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = load_config()
INBOX = os.path.join(CFG["nas"]["mount"], "inbox")
POLL = 5
_DEFAULT_SIZE = (10261600, 6840538)


def _resolve(env, cfgval, default):
    """env > config 값 > 기본값. 상대경로는 프로젝트 루트 기준으로 해석."""
    raw = os.environ.get(env) or cfgval or default
    return raw if os.path.isabs(raw) else os.path.join(ROOT, raw)


def load_assets(cfg):
    """page_types 라이브러리 로드 + recipe 경로 해석용 base_dir.
    경로: env PROPOSAL_PAGE_TYPES → cfg.assets.page_types → assets/page_types.example.json."""
    pt = _resolve("PROPOSAL_PAGE_TYPES", (cfg.get("assets") or {}).get("page_types"),
                  "assets/page_types.example.json")
    try:
        with open(pt, encoding="utf-8") as fh:
            page_types = json.load(fh)
    except (OSError, json.JSONDecodeError):
        page_types = []
    return {"page_types": page_types, "base_dir": os.path.dirname(pt)}


def build_runtime(cfg):
    """(store, assets, gateway) 묶음. 실제 가동/CLI 진입점에서 1회 생성해 재사용."""
    store_dir = _resolve("PROPOSAL_WEB_STORE", (cfg.get("web") or {}).get("store_dir"), "jobs")
    store = JobStore(store_dir)
    assets = load_assets(cfg)
    gateway = ai.AIGateway(cfg, known_page_types=[e.get("type") for e in assets["page_types"]])
    return store, assets, gateway


def _job_id(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^0-9A-Za-z가-힣_-]", "_", stem).strip("_") or "job"
    return f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}"


def _draft_shapes(pptx_path):
    """초안 PPTX 첫 슬라이드의 (shapes, size). soffice 불필요(zip 직접 파싱)."""
    try:
        with zipfile.ZipFile(pptx_path) as z:
            slides = sorted(n for n in z.namelist()
                            if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
            if not slides:
                return [], _DEFAULT_SIZE
            xml = z.read(slides[0]).decode("utf-8")
            try:
                pres = z.read("ppt/presentation.xml").decode("utf-8")
            except KeyError:
                pres = ""
    except (zipfile.BadZipFile, OSError):
        return [], _DEFAULT_SIZE
    size = geometry.slide_size(pres) if pres else _DEFAULT_SIZE
    return geometry.extract_shapes(xml), size


def _deck_slides(pptx_path):
    """덱의 모든 슬라이드: [{slide_path, shapes, size}] (슬라이드 번호 순). soffice 불필요."""
    out = []
    try:
        with zipfile.ZipFile(pptx_path) as z:
            names = sorted(
                (n for n in z.namelist()
                 if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                key=lambda n: int("".join(filter(str.isdigit, n.rsplit("/", 1)[-1])) or 0))
            try:
                pres = z.read("ppt/presentation.xml").decode("utf-8")
            except KeyError:
                pres = ""
            size = geometry.slide_size(pres) if pres else _DEFAULT_SIZE
            for n in names:
                xml = z.read(n).decode("utf-8", "replace")
                # 분류·매핑은 그룹 내부 텍스트까지 보는 deep 추출 사용(초안 측). 린터는 출력 측 별도.
                out.append({"slide_path": n, "shapes": geometry.extract_shapes_deep(xml),
                            "size": size})
    except (zipfile.BadZipFile, OSError):
        return []
    return out


def _load_sidecar(path):
    """`<name>.job.json` 사이드카(있으면) 로드. 없거나 깨졌으면 {}."""
    p = os.path.splitext(path)[0] + ".job.json"
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _merge_sidecar(job, spec):
    for k in ("template_pptx", "source_slots", "workdir", "out_pptx", "size", "page_type"):
        if k in spec:
            job[k] = spec[k]


def ingest(path, cfg, store, assets, gateway=None, job_id=None):
    """inbox 입력 1건 처리 → run_job → 검수 store 기록. 반환: 저장된 manifest(dict).

    - PPTX: 초안 도형 추출 → 분류. 사이드카 template 만 있으면 slot:<key> 텍스트 자동 추출.
    - PDF/HWP/텍스트(adapters): 텍스트 추출 → source_slots. 문서엔 슬라이드 구조가 없으므로
      변환하려면 사이드카가 `template_pptx`(+필요시 `page_type`)를 제공해야 한다.
      외부 파서 미설치 등 추출 실패는 검수(needs_human_approval)로 큐잉(데몬은 죽지 않음).
    - 변환 산출물이 없으면(PPTX 한정) 초안을 검수용 out.pptx 로 노출.
    """
    job_id = job_id or _job_id(path)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    spec = _load_sidecar(path)

    if ext == "pptx":
        slides = _deck_slides(path)
        # 다중 페이지 덱(사이드카 변환 지정이 없을 때): 페이지별 분류 → deck manifest
        if len(slides) > 1 and not spec.get("template_pptx"):
            pages = pipeline.classify_deck(slides, assets, gateway)
            unknown = sum(1 for p in pages if p["page_type"] == "unknown")
            man = {"id": job_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "kind": "deck", "page_count": len(pages), "pages": pages,
                   "status": "new_type_queued" if unknown else "ready_for_review",
                   "notify": notify.notify_review(
                       job_id, cfg, f"덱 {len(pages)}페이지 분류 — 미정의 {unknown}")}
            store.save_manifest(job_id, man)
            try:
                with open(path, "rb") as fh:
                    store.save_out_pptx_local(job_id, fh.read())  # 초안 원본 검수 노출
            except OSError:
                pass
            return man
        # 단일 페이지(또는 사이드카 변환 지정) → 기존 단건 경로
        shapes, size = (slides[0]["shapes"], slides[0]["size"]) if slides else ([], _DEFAULT_SIZE)
        job = {"id": job_id, "shapes": shapes, "size": size}
        _merge_sidecar(job, spec)
        if (job.get("template_pptx") and job.get("source_slots") is None
                and (cfg.get("daemon") or {}).get("auto_extract_slots", True)):
            auto = geometry.source_slots_from_shapes(shapes)
            if auto:
                job["source_slots"] = auto
    elif adapters.is_supported(path):
        try:
            result = adapters.extract(path)
        except adapters.AdapterError as e:   # AdapterUnavailable 포함
            man = {"id": job_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "status": "needs_human_approval",
                   "source": {"path": path, "ext": ext},
                   "adapter_error": str(e),
                   "notify": notify.notify_review(job_id, cfg, f"입력 추출 실패: {e}")}
            store.save_manifest(job_id, man)
            return man
        job = {"id": job_id, "shapes": [],
               "size": spec.get("size", _DEFAULT_SIZE),
               "source_slots": result.get("source_slots", {}),
               "extracted": {"kind": result.get("kind"), "title": result.get("title")}}
        _merge_sidecar(job, spec)   # template_pptx / page_type 등(명시값이 우선)
    else:
        return None  # 미지원 확장자(loop 가 먼저 거르지만 방어)

    man = pipeline.run_job(job, assets, cfg, gateway)
    if job.get("extracted"):
        man["extracted"] = job["extracted"]
    store.save_manifest(job_id, man)

    if ext == "pptx" and not (man.get("transform") or {}).get("out_pptx"):
        try:
            with open(path, "rb") as fh:
                store.save_out_pptx_local(job_id, fh.read())
        except OSError:
            pass
    return man


def _archive(path, dest_dir):
    """처리한 초안(+사이드카)을 dest_dir 로 이동. inbox 를 비워 재기동 시 재처리를 막는다.
    이름 충돌 시 시각 접미사를 붙인다. 이동 실패는 흐름을 멈추지 않는다."""
    try:
        os.makedirs(dest_dir, exist_ok=True)
        for src in (path, os.path.splitext(path)[0] + ".job.json"):
            if not os.path.exists(src):
                continue
            base = os.path.basename(src)
            dst = os.path.join(dest_dir, base)
            if os.path.exists(dst):
                stem, ext = os.path.splitext(base)
                dst = os.path.join(dest_dir, f"{stem}-{time.strftime('%H%M%S')}{ext}")
            shutil.move(src, dst)
    except OSError as e:
        print(f"[watch] 보관 실패: {path} — {e}")


def cleanup_old(directory, days, now=None):
    """directory 내 파일 중 mtime 이 days 일보다 오래된 것 삭제 → 삭제 개수 반환.
    days<=0 이면 비활성(아무것도 삭제 안 함). now 는 테스트용 기준시각 주입."""
    if not days or days <= 0 or not os.path.isdir(directory):
        return 0
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    removed = 0
    for name in os.listdir(directory):
        p = os.path.join(directory, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                removed += 1
        except OSError:
            pass
    return removed


def _cleanup_all(cfg, archive_dirs):
    """보관 폴더의 오래된 초안 정리(retention.source_delete_days)."""
    days = (cfg.get("retention") or {}).get("source_delete_days", 0)
    total = sum(cleanup_old(d, days) for d in archive_dirs)
    if total:
        print(f"[watch] 보관기간 만료 {total}건 삭제(>{days}일)")
    return total


_RT = None


def process(path, rt=None, archive_dirs=None):
    """단건 처리(가동 루프 + CLI 공용). rt 미지정 시 기본 런타임을 1회 생성·재사용.

    archive_dirs=(처리완료_dir, 실패_dir) 이 주어지면 처리 후 초안을 해당 폴더로 이동한다
    (가동 루프 전용; CLI 단건 호출은 사용자가 지정한 파일을 그대로 둔다)."""
    global _RT
    print(f"[watch] 처리 시작: {path}")
    if rt is None:
        _RT = _RT or build_runtime(CFG)
        rt = _RT
    store, assets, gateway = rt
    try:
        man = ingest(path, CFG, store, assets, gateway)
    except Exception as e:
        print(f"[watch] 실패: {path} — {type(e).__name__}: {e}")
        man = None
    if archive_dirs:
        _archive(path, archive_dirs[0] if man else archive_dirs[1])
    if man:
        print(f"[watch] 완료: {man['id']} type={man.get('page_type')} status={man.get('status')}")
    return man


def _archive_dirs(cfg):
    """(처리완료_dir, 실패_dir). 기본은 inbox 하위 _processed/_failed."""
    d = cfg.get("daemon") or {}
    processed = _resolve("PROPOSAL_PROCESSED_DIR", d.get("processed_dir"),
                         os.path.join(INBOX, "_processed"))
    failed = _resolve("PROPOSAL_FAILED_DIR", d.get("failed_dir"),
                      os.path.join(INBOX, "_failed"))
    return processed, failed


def loop():
    seen = set()
    rt = build_runtime(CFG)
    archive_dirs = _archive_dirs(CFG)
    # 하루(≈) 1회 보관기간 만료 정리. 기동 직후 1회 + 이후 주기적.
    cleanup_every = max(1, 86400 // POLL)
    iters = 0
    _cleanup_all(CFG, archive_dirs)
    print(f"[watch] 감시 시작: {INBOX}")
    while True:
        try:
            for f in os.listdir(INBOX):
                p = os.path.join(INBOX, f)
                if f.endswith(".job.json"):
                    continue   # 사이드카는 단독 처리 대상 아님
                if (p not in seen and os.path.isfile(p)
                        and (p.lower().endswith(".pptx") or adapters.is_supported(p))):
                    seen.add(p)
                    process(p, rt, archive_dirs)
        except FileNotFoundError:
            pass
        iters += 1
        if iters % cleanup_every == 0:
            _cleanup_all(CFG, archive_dirs)
        time.sleep(POLL)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        process(sys.argv[1])
    else:
        loop()
