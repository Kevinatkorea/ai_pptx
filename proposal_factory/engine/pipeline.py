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

from . import classify, deck, geometry, linter, notify, pptx_io, transform


def route(verdict, findings, cfg):
    """검증 결과 → 다음 행동. 구조 결함=사람 승인, 그 외=검수 대기."""
    fails = [c for sev, c, _, _ in findings if sev == "fail"]
    autofixable = set(cfg["autofix"].get("autofix_fail_codes", []))
    if fails and not set(fails).issubset(autofixable):
        return "needs_human_approval"
    if fails:
        return "autofix_then_recheck"
    return "ready_for_review"


def _classify_page(shapes, assets, gateway=None):
    """결정론(시그니처) → 내용 기반 LLM 분류 → unknown. (ptype, conf, source) 반환.

    시그니처로 구분 안 되는 grouped 텍스트 타입은 gateway.classify_page(내용 프로필+타입 카탈로그)
    로 분류한다. gateway 없거나 클라우드 불가면 결정론 결과만(보안망 안전).
    """
    ptype, conf, src = classify.classify(shapes, assets.get("page_types", []), gateway)
    if ptype == "unknown" and gateway is not None and hasattr(gateway, "classify_page"):
        catalog = [{"type": e.get("type"), "desc": e.get("desc", "")}
                   for e in assets.get("page_types", []) if e.get("type")]
        try:
            res = gateway.classify_page(classify.content_profile(shapes), catalog)
        except Exception:
            res = None
        if res:
            return res, 0.6, "content_llm"
    return ptype, conf, src


def classify_deck(slides, assets, gateway=None):
    """다중 페이지 덱 분류(페이지 1:1). slides: [{slide_path, shapes, size}].

    각 슬라이드를 독립 분류 → 페이지별 결과 리스트. 변환/매핑은 하지 않는다(분석 레이어).
    반환: [{index, slide_path, page_type, confidence, source, signature, has_recipe}].
    """
    rec_names = set((assets.get("recipes") or {}).keys())
    rec_names |= {e.get("type") for e in assets.get("page_types", []) if e.get("recipe")}
    pages = []
    for i, sl in enumerate(slides):
        shapes = sl.get("shapes", [])
        ptype, conf, src = _classify_page(shapes, assets, gateway)
        pages.append({
            "index": i,
            "slide_path": sl.get("slide_path"),
            "page_type": ptype,
            "confidence": conf,
            "source": src,
            "signature": classify.signature(shapes),
            "has_recipe": ptype in rec_names,
        })
    return pages


def _text_blocks(shapes):
    """초안 도형에서 텍스트 블록 추출(verbatim·순서 보존). [{i, text}]."""
    blocks = []
    for s in shapes:
        if s.get("tag") == "sp" and s.get("texts"):
            paras = []
            for t in s["texts"]:
                paras.extend(t.get("paras") or ([t["text"]] if t.get("text") else []))
            txt = "\n".join(p for p in paras if p).strip()
            if txt:
                blocks.append({"i": len(blocks), "text": txt})
    return blocks


def build_page_source_slots(draft_shapes, recipe, gateway=None):
    """초안 페이지 → recipe 의 text_inject 슬롯용 source_slots(**문구 verbatim**).

    AI(map_content)로 블록↔슬롯 배정하되, 값은 항상 초안 블록에서 **그대로 복사**한다
    (모델은 인덱스만 결정 → 문구 변경 불가). gateway 없거나 실패하면 순서 기반 폴백.
    반환: (source_slots dict, method) — method ∈ {"ai", "positional", "none"}.
    """
    text_ops = [op for op in recipe.get("ops", [])
                if op.get("op") == "text_inject" and op.get("slot") and op.get("from")]
    if not text_ops:
        return {}, "none"
    blocks = _text_blocks(draft_shapes)
    slots = [{"key": op["slot"], "op": "text_inject"} for op in text_ops]
    assign, method = {}, "positional"
    if gateway is not None:
        try:
            res = gateway.map_content(blocks, slots, hint=recipe.get("type", ""))
        except Exception:
            res = None
        if res and isinstance(res.get("assign"), dict) and res["assign"]:
            assign, method = dict(res["assign"]), "ai"
    if not assign:  # 폴백: 블록 순서 ↔ 슬롯 순서
        for n, op in enumerate(text_ops):
            if n < len(blocks):
                assign[op["slot"]] = blocks[n]["i"]
    text_by_i = {b["i"]: b["text"] for b in blocks}
    from_by_slot = {op["slot"]: op["from"] for op in text_ops}
    src = {}
    for slot_key, idx in assign.items():
        frm = from_by_slot.get(slot_key)
        if frm and idx in text_by_i:
            src[frm] = text_by_i[idx]   # ← 초안 텍스트 그대로(verbatim)
    return src, method


def _forced_type(i, forced_types):
    """페이지 i 의 운영자 지정 타입(있으면). forced_types: list[idx] 또는 dict{idx|str:type}."""
    if not forced_types:
        return None
    if isinstance(forced_types, dict):
        return forced_types.get(i) or forced_types.get(str(i))
    if isinstance(forced_types, list) and i < len(forced_types):
        return forced_types[i] or None
    return None


def run_deck(slides, assets, cfg, gateway, std_template_pptx, workdir,
             forced_types=None, out_deck=None, draft_pptx=None):
    """1:1 다중 페이지 변환. 페이지별: (분류/운영자 지정) → verbatim 매핑 → 표준 템플릿 변환 → 린트.

    페이지는 1:1(분리/병합 없음). forced_types 가 있으면 그 페이지는 분류 대신 지정 타입 사용.
    out_deck 가 주어지면 표준 템플릿 기반으로 **단일 파일 덱**으로 조립하고(deck.assemble),
    draft_pptx 가 함께 있으면 각 초안 슬라이드의 **이미지를 위치 그대로 carry-over** 한다.
    out_deck 가 없으면 페이지별 개별 출력(`<workdir>/page_<i>.pptx`).
    반환 manifest 조각: {kind:"deck", page_count, pages:[...], status, [out_deck]}.
    """
    os.makedirs(workdir, exist_ok=True)
    pages, specs = [], []
    any_fail = any_unknown = False
    for i, sl in enumerate(slides):
        shapes = sl.get("shapes", [])
        forced = _forced_type(i, forced_types)
        if forced:
            ptype, conf, src = forced, 1.0, "operator"
        else:
            ptype, conf, src = _classify_page(shapes, assets, gateway)
        page = {"index": i, "slide_path": sl.get("slide_path"),
                "page_type": ptype, "confidence": conf, "source": src}
        recipe = None if ptype == "unknown" else _resolve_recipe(ptype, assets)
        if ptype == "unknown" or not recipe:
            page["status"] = "new_type_queued" if ptype == "unknown" else "no_recipe"
            any_unknown = any_unknown or ptype == "unknown"
            any_fail = True
            pages.append(page)
            continue
        source_slots, method = build_page_source_slots(shapes, recipe, gateway)
        page["mapped"] = method
        pages.append(page)
        specs.append((page, recipe, source_slots, sl.get("slide_path")))

    if out_deck and specs:
        page_specs = [{"src_slide": rc.get("template_slide") or _recipe_slide_paths(rc)[0],
                       "ops": rc.get("ops", []), "source_slots": ss,
                       "draft_slide_path": sp}
                      for (pg, rc, ss, sp) in specs]
        try:
            _, new_paths = deck.assemble(std_template_pptx, page_specs, out_deck, cfg,
                                         draft_pptx=draft_pptx)
            for (pg, rc, _ss, _sp), npath in zip(specs, new_paths):
                shapes2, size2 = _shapes_from_pptx(out_deck, npath)
                findings = linter.lint(shapes2, size2[0], size2[1], cfg)
                verdict, nf, nw, _ = linter.report(findings)
                pg["transform"] = {"recipe": rc.get("type"), "slide_path": npath,
                                   "out_deck": out_deck}
                pg["lint"] = {"verdict": verdict, "fails": nf, "warns": nw}
                pg["status"] = route(verdict, findings, cfg)
                any_fail = any_fail or pg["status"] != "ready_for_review"
        except Exception as e:
            for pg, *_ in specs:
                pg["status"] = "needs_human_approval"
            any_fail = True
            out_deck = {"error": f"{type(e).__name__}: {e}"}
    else:
        for pg, rc, ss, _sp in specs:
            out_pptx = os.path.join(workdir, f"page_{pg['index']}.pptx")
            try:
                _produce(rc, std_template_pptx, ss, cfg,
                         os.path.join(workdir, f"wd_{pg['index']}"), out_pptx)
                findings, _sz = _lint_output(out_pptx, _recipe_slide_paths(rc), cfg)
                verdict, nf, nw, _ = linter.report(findings)
                pg["transform"] = {"recipe": rc.get("type"), "out_pptx": out_pptx}
                pg["lint"] = {"verdict": verdict, "fails": nf, "warns": nw}
                pg["status"] = route(verdict, findings, cfg)
                any_fail = any_fail or pg["status"] != "ready_for_review"
            except Exception as e:
                pg["transform"] = {"error": f"{type(e).__name__}: {e}"}
                pg["status"] = "needs_human_approval"
                any_fail = True

    status = ("new_type_queued" if any_unknown
              else "needs_human_approval" if any_fail else "ready_for_review")
    man = {"kind": "deck", "page_count": len(pages), "pages": pages, "status": status}
    if isinstance(out_deck, str):
        man["out_deck"] = out_deck
    return man


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
