"""run_web_demo.py — FastAPI 검수 UI 자기검증.

fastapi.testclient.TestClient 로 in-process 호출(실제 포트 안 띄움).
실행: `.venv/bin/python selfcheck/run_web_demo.py`
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

HERE_SELF = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE_SELF))    # engine + web 임포트
sys.path.insert(0, HERE_SELF)                     # 같은 selfcheck 모듈 임포트

# 미리보기 렌더 비활성화 → 테스트 결정론 확보
os.environ["PROPOSAL_PREVIEW_DISABLE"] = "1"

from engine import classify, pptx_io  # noqa: E402
from engine.config import load_config  # noqa: E402
from run_transform_demo import build_template, make_png  # noqa: E402

try:
    from fastapi.testclient import TestClient
except ImportError:
    print("FastAPI/httpx 미설치. `.venv/bin/python` 으로 실행하거나 "
          "`pip install -r requirements-web.txt` 후 재시도.")
    sys.exit(2)

CFG = load_config()


def _modify_x(pptx_bytes: bytes) -> bytes:
    """slide1.xml 에서 slot:breadcrumb 의 x 좌표를 360000→500000 으로.
    edited.pptx 와 candidate(out.pptx) 사이의 'moved' diff 를 유발한다.
    """
    bio = io.BytesIO(pptx_bytes)
    out = io.BytesIO()
    with zipfile.ZipFile(bio, "r") as zin, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "ppt/slides/slide1.xml":
                xml = data.decode("utf-8")
                idx = xml.find('name="slot:breadcrumb"')
                if idx > 0:
                    sub = xml[idx:].replace('x="360000"', 'x="500000"', 1)
                    xml = xml[:idx] + sub
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    return out.getvalue()


def _txt_sp(name):
    return {"tag": "sp", "name": name,
            "texts": [{"text": "x", "paras": ["x"], "sz": 700,
                       "wrap": "square", "autofit": False, "fonts": set()}]}


def _table_gf():
    return {"tag": "graphicFrame", "name": "tbl", "texts": [], "table_h": 100}


def main():
    errors = []
    def chk(cond, msg):
        print(("   ✓ " if cond else "   ✗ ") + msg)
        if not cond:
            errors.append(msg)

    tmp = tempfile.mkdtemp(prefix="pf_web_")
    # 레시피 승격이 레포 자산을 건드리지 않도록 라이브러리·page_types 경로를 임시로 격리
    recipes_lib = os.path.join(tmp, "recipes_lib")
    pt_file = os.path.join(tmp, "page_types.json")
    os.environ["PROPOSAL_RECIPES_DIR"] = recipes_lib
    os.environ["PROPOSAL_PAGE_TYPES"] = pt_file
    pt_src = os.path.join(os.path.dirname(HERE_SELF), "assets", "page_types.example.json")
    shutil.copyfile(pt_src, pt_file)
    try:
        # 1) 합성 출력 PPTX 작성
        tpl_dir = os.path.join(tmp, "tpl_unpacked")
        os.makedirs(tpl_dir)
        build_template(tpl_dir)
        synth_pptx = os.path.join(tmp, "synth.pptx")
        pptx_io.pack(tpl_dir, synth_pptx)
        shutil.rmtree(tpl_dir)

        # 2) store_dir 에 job 디렉터리 작성
        store_dir = os.path.join(tmp, "store")
        os.makedirs(store_dir)
        job_id = "demo-web-001"
        job_dir = os.path.join(store_dir, job_id)
        os.makedirs(job_dir)
        local_out = os.path.join(job_dir, "out.pptx")
        shutil.copyfile(synth_pptx, local_out)
        manifest = {
            "id": job_id,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "page_type": "body_company_overview",
            "classify": {"confidence": 1.0, "source": "deterministic"},
            "transform": {"recipe": "body_company_overview",
                          "out_pptx": local_out, "workdir": job_dir},
            "lint": {"verdict": "PASS", "fails": 0, "warns": 0, "findings": []},
            "status": "ready_for_review",
        }
        with open(os.path.join(job_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

        # 2b) 신규 유형 + AI 레시피 초안(승격 가능) job
        np_id = "demo-web-np"
        np_recipe = {"type": "new_body", "template_slide": "ppt/slides/slide1.xml",
                     "ops": [{"op": "text_inject", "slot": "title", "from": "title"}]}
        os.makedirs(os.path.join(store_dir, np_id))
        # 시그니처는 예시 page_types 의 어떤 유형과도 충돌하지 않게(표/이미지/연도박스 0) 둔다.
        np_sig = {"n_table": 0, "n_image": 0, "n_text": 1, "n_year_box": 0, "has_title": False}
        with open(os.path.join(store_dir, np_id, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": np_id, "ts": "2026-05-31T00:00:00", "page_type": "unknown",
                       "classify": {"confidence": 0.0, "source": "none"},
                       "signature": np_sig,
                       "status": "new_type_queued",
                       "recipe_proposal": {"attempted": True, "ok": True,
                                           "recipe": np_recipe, "by": "cloud"}},
                      fh, ensure_ascii=False, indent=2)

        # 2c) 신규 유형 + 레시피 초안 실패(승격 불가) job
        npf_id = "demo-web-npf"
        os.makedirs(os.path.join(store_dir, npf_id))
        with open(os.path.join(store_dir, npf_id, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": npf_id, "ts": "2026-05-31T00:00:00", "page_type": "unknown",
                       "classify": {"confidence": 0.0, "source": "none"},
                       "status": "new_type_queued",
                       "recipe_proposal": {"attempted": True, "ok": False,
                                           "reason": "no_recipe"}},
                      fh, ensure_ascii=False, indent=2)

        # 2d) 신규 유형 + 변환 입력 보존 → 승격 시 슬라이드 즉시 재생성 job
        rg_id = "demo-web-rg"
        rg_img = os.path.join(tmp, "cert.png")
        make_png(rg_img)
        with open(os.path.join(os.path.dirname(HERE_SELF),
                               "assets/recipes/body_company_overview.json"),
                  encoding="utf-8") as fh:
            rg_recipe = json.load(fh)
        rg_recipe["type"] = "regen_demo"
        rg_recipe["template_slide"] = "ppt/slides/slide1.xml"
        rg_slots = {
            "section_path": "1. 제안사 소개 > 1.1 회사 개요",
            "summary": "국내\n최대\n통신사 제안",
            "company_fields": [
                {"label": "회사명", "value": "주식회사 가나다"},
                {"label": "대표자", "value": "홍길동", "highlight": True},
                {"label": "설립일", "value": "2001-03-15"},
            ],
            "year_bullets": [
                {"year": "2024", "text": "국가고객만족도 14년 연속 1위"},
                {"year": "2023", "text": "ISMS-P 인증 획득"},
                {"year": "2022", "text": "서비스품질지수 9년 연속 1위"},
            ],
            "images": {"certificate_iso": {"path": rg_img, "x": 6700000, "y": 1400000,
                                           "cx": 2400000, "cy": 1800000}},
        }
        os.makedirs(os.path.join(store_dir, rg_id))
        with open(os.path.join(store_dir, rg_id, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": rg_id, "ts": "2026-05-31T00:00:00", "page_type": "unknown",
                       "classify": {"confidence": 0.0, "source": "none"},
                       "signature": {"n_table": 1, "n_image": 1, "n_text": 2,
                                     "n_year_box": 1, "has_title": True},
                       "status": "new_type_queued",
                       "transform_inputs": {"template_pptx": synth_pptx,
                                            "source_slots": rg_slots},
                       "recipe_proposal": {"attempted": True, "ok": True,
                                           "recipe": rg_recipe, "by": "cloud"}},
                      fh, ensure_ascii=False, indent=2)

        # 3) 앱 + 클라이언트
        from web.app import create_app
        app = create_app(cfg=CFG, store_dir=store_dir)
        client = TestClient(app)

        print("\n[1] GET /healthz")
        r = client.get("/healthz")
        chk(r.status_code == 200 and r.json() == {"ok": True}, "헬스체크")

        print("\n[2] GET / (HTML 큐 목록)")
        r = client.get("/")
        chk(r.status_code == 200, f"200 응답 (실제 {r.status_code})")
        chk(job_id in r.text, "HTML 안에 job_id 포함")
        chk("ready_for_review" in r.text, "상태 표시")

        print("\n[3] GET /review/{job_id} (HTML 상세)")
        r = client.get(f"/review/{job_id}")
        chk(r.status_code == 200, f"200 응답 (실제 {r.status_code})")
        chk("body_company_overview" in r.text, "페이지 유형 표시")
        chk("승인" in r.text and "수정본" in r.text, "액션 버튼 렌더")

        print("\n[4] GET /jobs (JSON 배열)")
        r = client.get("/jobs")
        chk(r.status_code == 200, "200 응답")
        chk(isinstance(r.json(), list) and len(r.json()) == 4,
            f"jobs JSON 4건 (실제 {len(r.json())})")

        print("\n[5] GET /jobs/{job_id}/manifest")
        r = client.get(f"/jobs/{job_id}/manifest")
        chk(r.status_code == 200, "200 응답")
        m = r.json()
        chk(m["id"] == job_id, "id 매치")
        chk(bool((m.get("transform") or {}).get("out_pptx")),
            "transform.out_pptx 존재")

        print("\n[6] GET /jobs/{job_id}/out.pptx (다운로드)")
        r = client.get(f"/jobs/{job_id}/out.pptx")
        chk(r.status_code == 200, "200 응답")
        ct = r.headers.get("content-type", "")
        chk("presentationml.presentation" in ct, f"PPTX content-type ({ct})")
        chk(r.content[:2] == b"PK", "ZIP(PK) 시그니처")

        print("\n[7] GET /jobs/{job_id}/preview.png (PREVIEW_DISABLE=1)")
        r = client.get(f"/jobs/{job_id}/preview.png")
        chk(r.status_code == 404, f"렌더 비활성화 → 404 (실제 {r.status_code})")

        print("\n[8] POST /jobs/{job_id}/approve")
        r = client.post(f"/jobs/{job_id}/approve", follow_redirects=False)
        chk(r.status_code == 303, f"303 리다이렉트 (실제 {r.status_code})")
        chk(r.headers.get("location", "").endswith(f"/review/{job_id}"),
            f"location 헤더 (실제 {r.headers.get('location')})")
        r2 = client.get(f"/jobs/{job_id}/manifest")
        chk(r2.json().get("status") == "approved", "status → approved 전이")
        chk(len(r2.json().get("audit", [])) == 1, "감사 로그 1건")

        print("\n[9] POST /jobs/{job_id}/upload-edit (수정본 + diff)")
        with open(local_out, "rb") as fh:
            orig = fh.read()
        edited = _modify_x(orig)
        chk(orig != edited, "수정본 바이트 다름 (xml 변경 확인)")
        files = {"file": ("edit.pptx", edited,
                          "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
        r = client.post(f"/jobs/{job_id}/upload-edit", files=files,
                        follow_redirects=False)
        chk(r.status_code == 303, f"303 리다이렉트 (실제 {r.status_code})")
        diffs_path = os.path.join(job_dir, "diffs.json")
        chk(os.path.exists(diffs_path), "diffs.json 저장")
        if os.path.exists(diffs_path):
            with open(diffs_path, encoding="utf-8") as fh:
                diffs = json.load(fh)
            moved = [d for d in diffs if d.get("kind") == "moved"
                     and d.get("shape") == "slot:breadcrumb"]
            chk(len(moved) == 1,
                f"slot:breadcrumb moved diff 1건 (실제 {len(moved)})")
            if moved:
                chk(moved[0]["dx"] == 140000,
                    f"dx=140000 (실제 {moved[0].get('dx')})")

        r3 = client.get(f"/jobs/{job_id}/manifest")
        audit = r3.json().get("audit", [])
        chk(len(audit) == 2, f"감사 로그 2건 (실제 {len(audit)})")
        chk(any(a.get("action") == "upload_edit" for a in audit),
            "upload_edit 액션 기록")

        print("\n[10] GET /jobs/UNKNOWN/manifest → 404")
        r = client.get("/jobs/UNKNOWN/manifest")
        chk(r.status_code == 404, f"404 응답 (실제 {r.status_code})")

        print("\n[11] GET /review/UNKNOWN → 404")
        r = client.get("/review/UNKNOWN")
        chk(r.status_code == 404, f"404 응답 (실제 {r.status_code})")

        print("\n[12] GET /review/demo-web-np (AI 레시피 초안 카드)")
        r = client.get(f"/review/{np_id}")
        chk(r.status_code == 200, f"200 응답 (실제 {r.status_code})")
        chk("AI 레시피 초안" in r.text, "초안 카드 헤더 렌더")
        chk("new_body" in r.text and "text_inject" in r.text, "레시피 유형·op 표시")
        chk(f"/jobs/{np_id}/promote-recipe" in r.text, "승격 버튼 폼 렌더")

        print("\n[13] POST /jobs/demo-web-np/promote-recipe (승격)")
        r = client.post(f"/jobs/{np_id}/promote-recipe", follow_redirects=False)
        chk(r.status_code == 303, f"303 리다이렉트 (실제 {r.status_code})")
        m = client.get(f"/jobs/{np_id}/manifest").json()
        chk(m.get("status") == "recipe_promoted", "status → recipe_promoted 전이")
        chk(m.get("page_type") == "new_body", "page_type → 레시피 유형으로 갱신")
        rp = m.get("recipe_proposal") or {}
        chk(rp.get("promoted") is True, "recipe_proposal.promoted=True")
        lib_file = os.path.join(recipes_lib, "new_body.json")
        chk(os.path.exists(lib_file), "레시피 라이브러리 파일 작성")
        if os.path.exists(lib_file):
            with open(lib_file, encoding="utf-8") as fh:
                saved = json.load(fh)
            chk(saved == np_recipe, "라이브러리 레시피 내용 일치")
        chk(any(a.get("action") == "promote_recipe" for a in m.get("audit", [])),
            "promote_recipe 감사 기록")
        # page_types 자동 등록 + 결정론 라우팅 검증
        chk(rp.get("page_type_registered") is True, "recipe_proposal.page_type_registered=True")
        with open(pt_file, encoding="utf-8") as fh:
            pt_lib = json.load(fh)
        np_entry = next((e for e in pt_lib if e.get("type") == "new_body"), None)
        chk(np_entry is not None and isinstance(np_entry.get("match"), dict) and np_entry["match"],
            "page_types 에 new_body 엔트리(match 포함) 추가")
        # 같은 구조의 새 작업이 이제 결정론으로 new_body 에 분류되는지 확인
        np_shapes = [{"tag": "sp", "name": "본문문단", "x": 0, "y": 0, "cx": 9, "cy": 9,
                      "texts": [{"text": "일반 본문", "paras": ["일반 본문"], "sz": 700,
                                 "wrap": "square", "autofit": False, "fonts": set()}]}]
        ptype, conf, source = classify.classify(np_shapes, pt_lib)
        chk(ptype == "new_body" and source == "deterministic",
            f"승격 후 결정론 분류 → new_body (실제 {ptype}/{source})")
        # 기존 유형 라우팅은 깨지지 않음(표+연도박스 → body_company_overview 유지)
        co_shapes = [{"tag": "graphicFrame", "name": "표", "x": 0, "y": 0, "cx": 9, "cy": 9,
                      "texts": [], "table_h": 100},
                     {"tag": "sp", "name": "연도2024", "x": 0, "y": 0, "cx": 9, "cy": 9,
                      "texts": [{"text": "2024", "paras": ["2024"], "sz": 700,
                                 "wrap": "none", "autofit": False, "fonts": set()}]}]
        cptype, _, _ = classify.classify(co_shapes, pt_lib)
        chk(cptype == "body_company_overview", f"기존 유형 라우팅 보존 (실제 {cptype})")

        print("\n[14] POST 재승격 (멱등) + 실패 초안 승격 차단")
        r = client.post(f"/jobs/{np_id}/promote-recipe", follow_redirects=False)
        m2 = client.get(f"/jobs/{np_id}/manifest").json()
        promote_audits = [a for a in m2.get("audit", []) if a.get("action") == "promote_recipe"]
        chk(r.status_code == 303 and len(promote_audits) == 1,
            f"재승격은 멱등 (감사 1건 유지, 실제 {len(promote_audits)})")
        r = client.get(f"/review/{npf_id}")
        chk("no_recipe" in r.text and f"/jobs/{npf_id}/promote-recipe" not in r.text,
            "실패 초안 → 사유 표시·승격 버튼 없음")
        r = client.post(f"/jobs/{npf_id}/promote-recipe", follow_redirects=False)
        chk(r.status_code == 400, f"실패 초안 승격 시도 → 400 (실제 {r.status_code})")

        print("\n[15] POST /jobs/demo-web-rg/promote-recipe (승격 → 슬라이드 즉시 재생성)")
        r = client.post(f"/jobs/{rg_id}/promote-recipe", follow_redirects=False)
        chk(r.status_code == 303, f"303 리다이렉트 (실제 {r.status_code})")
        m = client.get(f"/jobs/{rg_id}/manifest").json()
        tx = m.get("transform") or {}
        chk(tx.get("recipe") == "regen_demo" and "error" not in tx,
            f"transform 성공(recipe=regen_demo) (실제 {tx})")
        chk((m.get("lint") or {}).get("fails") == 0,
            f"재생성 슬라이드 린트 fails=0 (실제 {(m.get('lint') or {}).get('fails')})")
        chk(m.get("status") == "ready_for_review",
            f"상태 → ready_for_review 재진입 (실제 {m.get('status')})")
        chk((m.get("recipe_proposal") or {}).get("regenerated") is True,
            "recipe_proposal.regenerated=True")
        out_file = os.path.join(store_dir, rg_id, "out.pptx")
        chk(os.path.exists(out_file), "out.pptx 생성")
        r = client.get(f"/jobs/{rg_id}/out.pptx")
        chk(r.status_code == 200 and r.content[:2] == b"PK",
            f"재생성 PPTX 다운로드 200·PK (실제 {r.status_code})")
        rv = client.get(f"/review/{rg_id}")
        chk("슬라이드 재생성됨" in rv.text, "재생성 결과 UI 표시")

        print("\n[16] match 정교화: shadow 자동 해소(특수 규칙을 일반 규칙 앞에 삽입)")
        from web.app import _register_page_type
        pt_sh = os.path.join(tmp, "pt_shadow.json")
        with open(pt_sh, "w", encoding="utf-8") as fh:
            json.dump([{"type": "gen_table", "desc": "표 일반",
                        "match": {"n_table_min": 1}, "recipe": "recipes/gen.json"}],
                      fh, ensure_ascii=False)
        sig = {"n_table": 1, "n_image": 0, "n_text": 2, "n_year_box": 0, "has_title": False}
        recipe_sp = {"type": "special2", "template_slide": "ppt/slides/slide1.xml",
                     "ops": [{"op": "text_inject", "slot": "breadcrumb", "from": "title"}]}
        reg = _register_page_type(pt_sh, recipe_sp, sig, "recipes/special2.json")
        chk(reg.get("registered") and (reg.get("resolution") or "").startswith("specialized_before:gen_table"),
            f"shadow 해소: specialized_before:gen_table (실제 {reg.get('resolution')})")
        lib = json.load(open(pt_sh, encoding="utf-8"))
        i_sp = next((i for i, e in enumerate(lib) if e["type"] == "special2"), -1)
        i_gen = next((i for i, e in enumerate(lib) if e["type"] == "gen_table"), -1)
        chk(0 <= i_sp < i_gen, f"특수 규칙이 일반 규칙 앞 (special2={i_sp}, gen={i_gen})")
        p1, _, s1 = classify.classify([_table_gf(), _txt_sp("a"), _txt_sp("b")], lib)
        p2, _, _ = classify.classify([_table_gf()] + [_txt_sp(f"t{i}") for i in range(5)], lib)
        chk(p1 == "special2" and s1 == "deterministic",
            f"이 구조 → special2 결정론 (실제 {p1}/{s1})")
        chk(p2 == "gen_table", f"다른 표 구조 → gen_table 라우팅 보존 (실제 {p2})")

        print("\n=== WEB SELF-CHECK:",
              "PASS" if not errors else f"FAIL ({len(errors)})", "===")
        sys.exit(0 if not errors else 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
