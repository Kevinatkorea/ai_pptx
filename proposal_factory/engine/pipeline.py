"""pipeline.py — 오케스트레이터: ingest→classify→transform→capture→lint→route.

job 에 template_pptx + source_slots 가 있으면 결정론 변환 경로를 실행하고,
없으면 기존 shapes-only 경로(분류·검증만) 그대로 유지한다.

보안망(AI 불가)에서도 동작: gateway=None 이면 결정론 경로만 사용.
"""
import json
import os
import tempfile
import time
import zipfile

from . import classify, geometry, linter, notify, pptx_io, transform


def route(verdict, findings, cfg):
    """검증 결과 → 다음 행동. 구조 결함=사람 승인, 그 외=검수 대기."""
    fails = [c for sev, c, _, _ in findings if sev == "fail"]
    autofixable = set(cfg["autofix"].get("autofix_fail_codes", []))
    if fails and not set(fails).issubset(autofixable):
        return "needs_human_approval"
    if fails:
        return "autofix_then_recheck"
    return "ready_for_review"


def _resolve_recipe(page_type, assets):
    """page_type 에 대응하는 recipe(dict) 를 반환.
    우선순위: assets['recipes'][page_type] (in-memory) → page_types 엔트리의
    'recipe' 경로를 assets['base_dir'] 기준으로 로드.
    """
    rec_map = assets.get("recipes") or {}
    if page_type in rec_map:
        return rec_map[page_type]
    entry = next((e for e in assets.get("page_types", []) if e.get("type") == page_type), None)
    if not entry or not entry.get("recipe"):
        return None
    base = assets.get("base_dir", ".")
    with open(os.path.join(base, entry["recipe"]), encoding="utf-8") as fh:
        return json.load(fh)


def _try_author_recipe(job, sig, cfg, gateway):
    """신규 유형(unknown)일 때 클라우드(Opus)로 레시피 초안 작성 시도.

    자동 적용하지 않는다 — 검수자가 검토할 '제안'으로만 manifest 에 첨부한다
    (human-in-the-loop 유지). 게이트웨이 단에서 예외/실패는 None 으로 흡수되므로
    여기서도 절대 raise 하지 않고 항상 dict 를 반환한다.

    반환: {"attempted": bool, "ok": bool, "recipe"?: dict, "by"?: str, "reason"?: str}
          게이트웨이가 없으면 None.
    """
    if gateway is None:
        return None
    if not cfg.get("ai", {}).get("author_recipe_on_new_type", True):
        return {"attempted": False, "reason": "disabled_by_config"}
    try:
        if not gateway.can_use_cloud():
            return {"attempted": False, "reason": "cloud_unavailable"}
        recipe = gateway.author_recipe(sig, job.get("shapes", []),
                                       job.get("recipe_hint", ""))
    except Exception as e:  # 방어: 게이트웨이가 규약을 어겨도 파이프라인은 죽지 않는다
        return {"attempted": True, "ok": False, "reason": f"{type(e).__name__}: {e}"}
    if not recipe:
        return {"attempted": True, "ok": False, "reason": "no_recipe"}
    return {"attempted": True, "ok": True, "recipe": recipe, "by": "cloud"}


def _shapes_from_pptx(pptx_path, slide_path):
    """출력 PPTX에서 슬라이드 도형/크기 추출(린터 입력)."""
    with zipfile.ZipFile(pptx_path) as z:
        slide_xml = z.read(slide_path).decode("utf-8")
        try:
            pres_xml = z.read("ppt/presentation.xml").decode("utf-8")
        except KeyError:
            pres_xml = ""
    return geometry.extract_shapes(slide_xml), geometry.slide_size(pres_xml)


def _produce(recipe, template_pptx, source_slots, cfg, workdir, out_pptx):
    """recipe + 입력으로 transform 실행 → out_pptx 생성(도형 추출은 별도)."""
    os.makedirs(workdir, exist_ok=True)
    unpack_dir = os.path.join(workdir, "unpacked")
    pptx_io.unpack(template_pptx, unpack_dir)
    transform.apply(recipe, source_slots, unpack_dir, out_pptx, cfg)
    return out_pptx


def _recipe_slide_paths(recipe):
    """recipe 가 생성/수정하는 슬라이드 경로 목록(단일/다중 슬라이드 모두 지원)."""
    specs = recipe.get("template_slides")
    if specs:
        return [s.get("template_slide") or "ppt/slides/slide1.xml" for s in specs]
    return [recipe.get("template_slide") or "ppt/slides/slide1.xml"]


def _lint_output(out_pptx, slide_paths, cfg):
    """out_pptx 의 각 슬라이드를 린트해 findings 를 합산. (findings, size) 반환.
    다중 슬라이드도 슬라이드별로 검사하므로 슬라이드 간 거짓 겹침이 생기지 않는다."""
    findings, size = [], (10261600, 6840538)
    for sp in slide_paths:
        shapes, size = _shapes_from_pptx(out_pptx, sp)
        findings.extend(linter.lint(shapes, size[0], size[1], cfg))
    return findings, size


def regenerate(recipe, template_pptx, source_slots, cfg,
               workdir, out_pptx, job_id="job"):
    """승격된 recipe 로 슬라이드를 즉시 재생성하고 lint→route 까지 수행.

    run_job 의 transform→lint 경로와 동일한 결선을 재사용한다(검수 승격 후 호출용).
    manifest 갱신 조각(dict)을 반환:
      성공: {transform, lint, status, [notify]}
      실패: {transform:{error}, status:"needs_human_approval", notify}
    """
    try:
        _produce(recipe, template_pptx, source_slots, cfg, workdir, out_pptx)
    except transform.MissingSlot as e:
        return {"transform": {"error": f"MissingSlot: {e}"},
                "status": "needs_human_approval",
                "notify": notify.notify_review(job_id, cfg, f"슬롯 누락: {e}")}
    except Exception as e:
        return {"transform": {"error": f"{type(e).__name__}: {e}"},
                "status": "needs_human_approval",
                "notify": notify.notify_review(job_id, cfg, f"변환 실패: {e}")}
    findings, _size = _lint_output(out_pptx, _recipe_slide_paths(recipe), cfg)
    verdict, nf, nw, _ = linter.report(findings)
    upd = {"transform": {"recipe": recipe.get("type"),
                         "out_pptx": out_pptx, "workdir": workdir},
           "lint": {"verdict": verdict, "fails": nf, "warns": nw, "findings": findings},
           "status": route(verdict, findings, cfg)}
    if upd["status"] != "ready_for_review":
        upd["notify"] = notify.notify_review(job_id, cfg, "구조 수정 승인 필요")
    return upd


def run_job(job, assets, cfg, gateway=None, capture_fn=None):
    """job 필드:
        id (필수)
        shapes (분류용 도형 — PPTX 입력일 땐 '초안' 도형)
        size  (선택; 기본 (10261600, 6840538))
        template_pptx + source_slots (선택; 둘 다 있으면 transform 수행)
        workdir (선택; 작업/출력 디렉터리)
        out_pptx (선택; 출력 파일 경로)
    """
    man = {"id": job["id"], "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # 분류 도형이 없는 입력(PDF/HWP 등)은 운영자가 job["page_type"] 로 유형을 지정한다.
    forced = job.get("page_type")
    if forced:
        ptype, conf, src = forced, 1.0, "forced"
    else:
        ptype, conf, src = classify.classify(job["shapes"], assets["page_types"], gateway)
    man["page_type"] = ptype
    man["classify"] = {"confidence": conf, "source": src}
    if ptype == "unknown":
        man["status"] = "new_type_queued"
        sig = classify.signature(job["shapes"])
        man["signature"] = sig  # 승격 시 page_types 매칭 규칙 유도에 사용
        # 승격 후 슬라이드 즉시 재생성에 쓰도록 transform 입력 보존(있을 때만)
        if job.get("template_pptx") and job.get("source_slots") is not None:
            man["transform_inputs"] = {"template_pptx": job["template_pptx"],
                                       "source_slots": job["source_slots"]}
        proposal = _try_author_recipe(job, sig, cfg, gateway)
        msg = "신규 유형 검토"
        if proposal is not None:
            man["recipe_proposal"] = proposal
            if proposal.get("ok"):
                msg = "신규 유형 — AI 레시피 초안 검토"
        man["notify"] = notify.notify_review(job["id"], cfg, msg)
        return man

    size = job.get("size", (10261600, 6840538))
    findings = None  # transform 경로면 출력 슬라이드(들)에서 산출, 아니면 초안 도형 린트

    if job.get("template_pptx") and job.get("source_slots") is not None:
        try:
            recipe = _resolve_recipe(ptype, assets)
            if not recipe:
                raise ValueError(f"no recipe for page_type {ptype}")
            workdir = job.get("workdir") or tempfile.mkdtemp(prefix=f"pf_{job['id']}_")
            out_pptx = job.get("out_pptx") or os.path.join(workdir, f"{job['id']}.pptx")
            _produce(recipe, job["template_pptx"], job["source_slots"],
                     cfg, workdir, out_pptx)
            man["transform"] = {"recipe": recipe.get("type"),
                                "out_pptx": out_pptx, "workdir": workdir}
            findings, size = _lint_output(out_pptx, _recipe_slide_paths(recipe), cfg)
        except transform.MissingSlot as e:
            man["transform"] = {"error": f"MissingSlot: {e}"}
            man["status"] = "needs_human_approval"
            man["notify"] = notify.notify_review(job["id"], cfg, f"슬롯 누락: {e}")
            return man
        except Exception as e:
            man["transform"] = {"error": f"{type(e).__name__}: {e}"}
            man["status"] = "needs_human_approval"
            man["notify"] = notify.notify_review(job["id"], cfg, f"변환 실패: {e}")
            return man

    if findings is None:
        findings = linter.lint(job["shapes"], size[0], size[1], cfg)
    verdict, nf, nw, text = linter.report(findings)
    man["lint"] = {"verdict": verdict, "fails": nf, "warns": nw, "findings": findings}
    man["status"] = route(verdict, findings, cfg)
    if man["status"] != "ready_for_review":
        man["notify"] = notify.notify_review(job["id"], cfg, "구조 수정 승인 필요")
    return man
