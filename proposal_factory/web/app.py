"""app.py — FastAPI 앱 정의.

실행: `.venv/bin/uvicorn web.app:app --reload --host 127.0.0.1 --port 8000`
(working directory 는 proposal_factory/ 디렉터리)
"""
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine import classify, geometry, learn, pipeline  # noqa: E402
from engine.config import load_config  # noqa: E402

from . import preview
from .store import JobStore


HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


def _shapes_from_pptx_bytes(b: bytes):
    """PPTX bytes → 첫 슬라이드의 도형 리스트(없으면 [])."""
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            slides = sorted(n for n in z.namelist()
                            if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
            if not slides:
                return []
            xml = z.read(slides[0]).decode("utf-8")
    except zipfile.BadZipFile:
        return []
    return geometry.extract_shapes(xml)


def _actor(request: Request) -> str:
    """감사 로그에 기록할 사용자 식별자. Cloudflare Access 헤더 우선."""
    return (request.headers.get("Cf-Access-Authenticated-User-Email")
            or request.headers.get("X-User-Email")
            or "local")


def _safe_slug(name: str, fallback: str) -> str:
    """레시피 파일명용 안전 슬러그. LLM 이 만든 type 문자열을 신뢰하지 않는다."""
    slug = re.sub(r"[^0-9A-Za-z가-힣_-]", "_", (name or "").strip())
    slug = slug.strip("_")
    return slug or fallback


def _resolve_recipes_dir(cfg: dict) -> str:
    """승격된 레시피를 기록할 라이브러리 디렉터리.
    우선순위: env PROPOSAL_RECIPES_DIR → cfg.assets.recipes_dir → <root>/assets/recipes.
    상대경로는 PROJECT_ROOT 기준으로 해석한다."""
    raw = (os.environ.get("PROPOSAL_RECIPES_DIR")
           or (cfg.get("assets") or {}).get("recipes_dir")
           or str(PROJECT_ROOT / "assets" / "recipes"))
    return raw if os.path.isabs(raw) else str(PROJECT_ROOT / raw)


def _resolve_page_types_path(cfg: dict) -> str:
    """page_types 라이브러리 파일 경로.
    우선순위: env PROPOSAL_PAGE_TYPES → cfg.assets.page_types → <root>/assets/page_types.example.json.
    상대경로는 PROJECT_ROOT 기준으로 해석한다."""
    raw = (os.environ.get("PROPOSAL_PAGE_TYPES")
           or (cfg.get("assets") or {}).get("page_types")
           or str(PROJECT_ROOT / "assets" / "page_types.example.json"))
    return raw if os.path.isabs(raw) else str(PROJECT_ROOT / raw)


# match 유도에 쓰는 구조적 시그니처 키(텍스트 수 n_text 는 과도하게 일반적이라 제외).
_SIG_COUNT_KEYS = ("n_table", "n_image", "n_year_box")


def _match_from_signature(sig: dict) -> dict:
    """결정론 분류용 match 술어를 시그니처에서 유도.
    카운트>0 → `_min:1`, ==0 → `_max:0`. has_title 은 참일 때만 고정.
    항상 비어있지 않은 dict 를 반환(classify._match 가 빈 match 를 거부)."""
    m = {}
    for k in _SIG_COUNT_KEYS:
        if int(sig.get(k, 0) or 0) > 0:
            m[k + "_min"] = 1
        else:
            m[k + "_max"] = 0
    if sig.get("has_title"):
        m["has_title"] = True
    return m


# shadow 해소 시 가리는 규칙을 좁히는 데 쓰는 정확-카운트 키.
_SIG_EXACT_KEYS = ("n_table", "n_image", "n_year_box", "n_text")


def _specialized_against(other_match: dict, sig: dict):
    """`other_match`(가리는 기존 규칙)를 sig 로 더 좁힌 match 를 만든다.

    other_match 의 모든 술어를 포함(⊇ 제약)하므로 결과 규칙에 매칭되는 슬라이드는 반드시
    other 에도 매칭된다 → other 앞에 삽입해도 other 가 잡던 '다른' 슬라이드는 그대로 유지되고,
    이 sig 부분집합만 신규 유형으로 분기된다(기존 라우팅 보존). 시그니처의 정확 카운트를
    아직 고정되지 않은 키에 추가(count weighting)해 구분력을 만든다.
    구분 술어를 하나도 더할 수 없으면(이미 전부 정확 고정) None.
    """
    spec = dict(other_match)
    added = False
    for k in _SIG_EXACT_KEYS:
        if k not in spec:                      # _min/_max 접미 키와 별개의 정확값
            spec[k] = int(sig.get(k, 0) or 0)
            added = True
    if "has_title" not in spec:
        spec["has_title"] = bool(sig.get("has_title"))
        added = True
    if not added or spec == other_match:
        return None
    return spec


def _register_page_type(pt_path: str, recipe: dict, sig: dict,
                        recipe_rel: str) -> dict:
    """승격된 레시피의 page_types 매칭 엔트리를 자동 등록.

    - 시그니처에서 match 술어 유도 → `{type, desc, match, recipe}` 엔트리.
    - 같은 type 이 있으면 갱신.
    - 더 앞선 기존 엔트리가 이미 이 시그니처를 잡으면(shadow): 시그니처 정확 카운트로 규칙을
      좁혀(specialize) 그 엔트리 **앞에 삽입** → 신규 유형이 도달 가능해지고 기존 라우팅은 보존.
      좁힐 수 없으면(구분 불가) 말미에 추가하고 미해소로 기록.
    - shadow 없으면 말미에 추가(기존 우선순위 보존).
    반환: {registered, match?, updated?, resolution?, shadowed_by?, reason?}.
      resolution ∈ {appended, specialized_before:<type>, shadowed_unresolved:<type>}.
    """
    rtype = recipe.get("type")
    if not rtype:
        return {"registered": False, "reason": "no_recipe_type"}
    if not sig:
        return {"registered": False, "reason": "no_signature"}
    try:
        with open(pt_path, encoding="utf-8") as fh:
            lib = json.load(fh)
        if not isinstance(lib, list):
            lib = []
    except (OSError, json.JSONDecodeError):
        lib = []

    def _entry(match):
        return {"type": rtype,
                "desc": recipe.get("desc") or f"AI 승격 레시피 — {rtype}",
                "match": match, "recipe": recipe_rel}

    # 같은 type 갱신(자기 자신은 shadow 후보에서 제외)
    for i, e in enumerate(lib):
        if e.get("type") == rtype:
            match = _match_from_signature(sig)
            lib[i] = _entry(match)
            if _write_page_types(pt_path, lib) is not None:
                return {"registered": False, "reason": "write_failed"}
            return {"registered": True, "match": match, "updated": True,
                    "resolution": "appended", "path": pt_path}

    # 가장 앞선 매칭(shadow) 기존 엔트리 탐색
    shadow_idx = shadow_type = None
    for i, e in enumerate(lib):
        if classify._match(sig, e.get("match", {})):
            shadow_idx, shadow_type = i, e.get("type")
            break

    match = _match_from_signature(sig)
    resolution = "appended"
    insert_at = len(lib)
    if shadow_idx is not None:
        spec = _specialized_against(lib[shadow_idx].get("match", {}), sig)
        if spec is not None and classify._match(sig, spec):
            match, resolution, insert_at = spec, f"specialized_before:{shadow_type}", shadow_idx
        else:
            resolution = f"shadowed_unresolved:{shadow_type}"

    lib.insert(insert_at, _entry(match))
    if _write_page_types(pt_path, lib) is not None:
        return {"registered": False, "reason": "write_failed"}

    res = {"registered": True, "match": match, "updated": False,
           "resolution": resolution, "path": pt_path}
    if shadow_type:
        res["shadowed_by"] = shadow_type
    return res


def _write_page_types(pt_path, lib):
    """page_types 파일 저장. 성공 시 None, 실패 시 예외 메시지."""
    try:
        d = os.path.dirname(pt_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(pt_path, "w", encoding="utf-8") as fh:
            json.dump(lib, fh, ensure_ascii=False, indent=2)
        return None
    except OSError as e:
        return str(e)


def create_app(cfg: dict = None, store_dir: str = None) -> FastAPI:
    cfg = cfg or load_config()
    web_cfg = cfg.get("web", {}) or {}
    store_dir = (store_dir
                 or os.environ.get("PROPOSAL_WEB_STORE")
                 or web_cfg.get("store_dir")
                 or str(PROJECT_ROOT / "jobs"))
    store = JobStore(store_dir)
    recipes_dir = _resolve_recipes_dir(cfg)
    page_types_path = _resolve_page_types_path(cfg)

    app = FastAPI(title="Proposal Factory — 검수 UI", version="0.1")
    app.state.store = store
    app.state.cfg = cfg
    app.state.recipes_dir = recipes_dir
    app.state.page_types_path = page_types_path

    # ---- HTML ----
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        manifests = store.list_manifests()
        groups = {}
        for m in manifests:
            groups.setdefault(m.get("status", "unknown"), []).append(m)
        ordered = sorted(groups.items(), key=lambda kv: _status_order(kv[0]))
        return TEMPLATES.TemplateResponse(
            request, "list.html",
            {"groups": ordered, "total": len(manifests)})

    @app.get("/review/{job_id}", response_class=HTMLResponse)
    def review(request: Request, job_id: str):
        man = store.read_manifest(job_id)
        if not man:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        diffs = store.read_diffs(job_id) or []
        out_exists = bool(store.out_pptx_path(job_id))
        preview_exists = bool(store.preview_png_path(job_id)) or preview.available()
        return TEMPLATES.TemplateResponse(
            request, "review.html",
            {"job_id": job_id, "man": man, "diffs": diffs,
             "out_exists": out_exists, "preview_exists": preview_exists})

    # ---- JSON / static ----
    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/jobs")
    def jobs():
        return store.list_manifests()

    @app.get("/jobs/{job_id}/manifest")
    def get_manifest(job_id: str):
        man = store.read_manifest(job_id)
        if not man:
            raise HTTPException(status_code=404)
        return man

    @app.get("/jobs/{job_id}/out.pptx")
    def get_out_pptx(job_id: str):
        path = store.out_pptx_path(job_id)
        if not path:
            raise HTTPException(status_code=404, detail="out.pptx not available")
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename=f"{job_id}.pptx")

    @app.get("/jobs/{job_id}/preview.png")
    def get_preview(job_id: str):
        cached = store.preview_png_path(job_id)
        if cached:
            return FileResponse(cached, media_type="image/png")
        out = store.out_pptx_path(job_id)
        if out:
            target = os.path.join(store.job_dir(job_id), "preview.png")
            if preview.try_render(out, target):
                return FileResponse(target, media_type="image/png")
        raise HTTPException(
            status_code=404,
            detail="preview not available (soffice/pdftoppm 미설치 또는 렌더 실패)")

    # ---- 액션 ----
    @app.post("/jobs/{job_id}/approve")
    def approve(job_id: str, request: Request):
        man = store.read_manifest(job_id)
        if not man:
            raise HTTPException(status_code=404)
        new_status = ("output_done" if store.edited_pptx_path(job_id)
                      else "approved")
        man["status"] = new_status
        man.setdefault("audit", []).append({
            "action": "approve", "by": _actor(request),
            "new_status": new_status,
        })
        store.save_manifest(job_id, man)
        return RedirectResponse(f"/review/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/promote-recipe")
    def promote_recipe(job_id: str, request: Request):
        """AI 레시피 초안(recipe_proposal)을 레시피 라이브러리로 승격.

        - 자동 적용은 하지 않는다 — 사람이 검토 후 명시적으로 승격한 것만 라이브러리에 기록.
        - 레시피 파일 작성 + 감사 사본(job 디렉터리) + manifest 갱신(상태 recipe_promoted).
        - 멱등: 이미 승격된 건은 재작성 없이 상세로 돌아간다.
        - 페이지 유형 분류 시그니처(page_types) 자동 등록은 범위 밖 — 운영자가 별도 처리.
        """
        man = store.read_manifest(job_id)
        if not man:
            raise HTTPException(status_code=404)
        rp = man.get("recipe_proposal") or {}
        recipe = rp.get("recipe")
        if not rp.get("ok") or not isinstance(recipe, dict):
            raise HTTPException(status_code=400,
                                detail="no promotable recipe proposal for this job")
        if rp.get("promoted"):
            return RedirectResponse(f"/review/{job_id}", status_code=303)

        slug = _safe_slug(recipe.get("type"), job_id)
        os.makedirs(recipes_dir, exist_ok=True)
        lib_path = os.path.join(recipes_dir, f"{slug}.json")
        with open(lib_path, "w", encoding="utf-8") as fh:
            json.dump(recipe, fh, ensure_ascii=False, indent=2)
        store.save_promoted_recipe(job_id, recipe)

        rp["promoted"] = True
        rp["promoted_by"] = _actor(request)
        rp["promoted_path"] = lib_path

        # page_types 매칭 시그니처 자동 등록(시그니처/파일이 있고 토글이 켜진 경우).
        # 등록 실패는 승격 자체를 막지 않는다(레시피 라이브러리 기록은 이미 끝남).
        reg = None
        if (cfg.get("assets") or {}).get("register_page_type_on_promote", True):
            recipe_rel = os.path.relpath(lib_path, os.path.dirname(page_types_path))
            reg = _register_page_type(page_types_path, recipe, man.get("signature"),
                                      recipe_rel)
            rp["page_type_registered"] = bool(reg.get("registered"))
            if reg.get("match"):
                rp["page_type_match"] = reg["match"]
            if reg.get("resolution"):
                rp["page_type_resolution"] = reg["resolution"]
            if reg.get("reason"):
                rp["page_type_register_reason"] = reg["reason"]

        if man.get("page_type") in (None, "", "unknown"):
            man["page_type"] = recipe.get("type") or man.get("page_type")

        # 승격 레시피로 이 작업의 본 슬라이드 즉시 재생성(입력이 보존돼 있고 토글이 켜진 경우).
        # 성공 시 정상 검수 흐름(ready_for_review / needs_human_approval)으로 재진입.
        # 실패/입력 없음/토글 off 면 레시피 등록만 반영하고 recipe_promoted 로 둔다.
        man["status"] = "recipe_promoted"
        ti = man.get("transform_inputs") or {}
        regen_toggle = (cfg.get("assets") or {}).get("regenerate_on_promote", True)
        if regen_toggle and ti.get("template_pptx") and ti.get("source_slots") is not None:
            if os.path.exists(ti["template_pptx"]):
                workdir = os.path.join(store.job_dir(job_id), "regen")
                out_pptx = os.path.join(store.job_dir(job_id), "out.pptx")
                upd = pipeline.regenerate(recipe, ti["template_pptx"], ti["source_slots"],
                                          cfg, workdir, out_pptx, job_id)
                man.update(upd)  # transform/lint/status/(notify) 병합
                rp["regenerated"] = "error" not in (upd.get("transform") or {})
                if not rp["regenerated"]:
                    rp["regenerate_reason"] = (upd.get("transform") or {}).get("error")
            else:
                rp["regenerated"] = False
                rp["regenerate_reason"] = "template_pptx_missing"
        elif regen_toggle and not ti:
            rp["regenerated"] = False
            rp["regenerate_reason"] = "no_transform_inputs"

        man["recipe_proposal"] = rp
        audit = {"action": "promote_recipe", "by": _actor(request),
                 "recipe_type": recipe.get("type"), "path": lib_path,
                 "new_status": man["status"]}
        if reg is not None:
            audit["page_type_registered"] = bool(reg.get("registered"))
            if reg.get("resolution"):
                audit["page_type_resolution"] = reg["resolution"]
            if reg.get("shadowed_by"):
                audit["shadowed_by"] = reg["shadowed_by"]
        if "regenerated" in rp:
            audit["regenerated"] = rp["regenerated"]
        man.setdefault("audit", []).append(audit)
        store.save_manifest(job_id, man)
        return RedirectResponse(f"/review/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/upload-edit")
    async def upload_edit(job_id: str, request: Request,
                          file: UploadFile = File(...)):
        man = store.read_manifest(job_id)
        if not man:
            raise HTTPException(status_code=404)
        out_path = store.out_pptx_path(job_id)
        if not out_path:
            raise HTTPException(status_code=400,
                                detail="candidate out.pptx not available")
        edited_bytes = await file.read()
        if not edited_bytes:
            raise HTTPException(status_code=400, detail="empty upload")
        store.save_edited(job_id, edited_bytes)

        with open(out_path, "rb") as fh:
            cand_bytes = fh.read()
        cand_shapes = _shapes_from_pptx_bytes(cand_bytes)
        final_shapes = _shapes_from_pptx_bytes(edited_bytes)
        diffs = learn.diff_shapes(cand_shapes, final_shapes)
        store.save_diffs(job_id, diffs)

        man.setdefault("audit", []).append({
            "action": "upload_edit", "by": _actor(request),
            "diff_count": len(diffs),
        })
        store.save_manifest(job_id, man)
        return RedirectResponse(f"/review/{job_id}", status_code=303)

    return app


_STATUS_ORDER = {
    "needs_human_approval": 0,
    "ready_for_review": 1,
    "new_type_queued": 2,
    "recipe_promoted": 3,
    "autofix_then_recheck": 4,
    "approved": 5,
    "output_done": 6,
}


def _status_order(s: str) -> int:
    return _STATUS_ORDER.get(s, 99)


# uvicorn 엔트리포인트
app = create_app()
