"""run_pipeline_demo.py — 오케스트레이션 + 직원수정 학습 루프 자기검증.
[1][2][3]: 결정론 경로(classify→lint→route)와 diff 학습 검증.
[4]: PPTX 입력 + source_slots → transform → lint → ready_for_review 전(全) 결선 검증.
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE_SELF = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE_SELF))    # engine 임포트용
sys.path.insert(0, HERE_SELF)                     # 같은 selfcheck 모듈 임포트용
from engine.config import load_config
from engine import pipeline, learn, pptx_io
from run_transform_demo import (build_template, make_png,  # 픽스처 재사용
                                CONTENT_TYPES, PRESENTATION_XML)
from daemon import watch_inbox
from web.store import JobStore

CFG = load_config()
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = (6840538, 10261600)

# 표/이미지/연도박스 없는 텍스트 1개 슬라이드 → 결정론 분류 시 unknown(신규 유형).
MINI_TEXT_SLIDE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name="Group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="본문"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="400000" y="3000000"/><a:ext cx="5000000" cy="400000"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>
<a:p><a:r><a:rPr lang="ko-KR" sz="1000"/><a:t>일반 본문 텍스트</a:t></a:r></a:p>
</p:txBody></p:sp>
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def _pack_pptx(out_pptx, slide_xml, workdir):
    """CT/presentation/slide1 미니 트리를 만들어 out_pptx 로 pack."""
    tree = os.path.join(workdir, "tree")
    files = {"[Content_Types].xml": CONTENT_TYPES,
             "ppt/presentation.xml": PRESENTATION_XML,
             "ppt/slides/slide1.xml": slide_xml,
             "ppt/slides/_rels/slide1.xml.rels":
                 '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'}
    for rel, content in files.items():
        p = os.path.join(tree, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    pptx_io.pack(tree, out_pptx)


def txt(t, sz=700, wrap="square", autofit=False, paras=None):
    return {"text": t, "paras": paras if paras is not None else ([t] if t.strip() else []),
            "sz": sz, "wrap": wrap, "autofit": autofit, "fonts": set()}


def sp(name, x, y, cx, cy, texts=None, tag="sp", table_h=None):
    d = {"tag": tag, "name": name, "x": x, "y": y, "cx": cx, "cy": cy, "texts": texts or []}
    if table_h:
        d["table_h"] = table_h
    return d


class StubGateway:
    """파이프라인의 author_recipe 결선만 검증하기 위한 최소 게이트웨이.
    분류는 항상 unknown(local/cloud_classify=None) 으로 두고, author_recipe 만 제어한다.
    (HTTP 실호출 경로는 run_ai_demo.py 가 MockHttp 로 별도 검증.)
    """
    def __init__(self, recipe=None, cloud=True):
        self._recipe = recipe
        self._cloud = cloud
        self.calls = []

    def local_classify(self, sig):
        self.calls.append("local_classify"); return None

    def cloud_classify(self, sig):
        self.calls.append("cloud_classify"); return None

    def can_use_cloud(self):
        return self._cloud

    def author_recipe(self, sig, shapes, hint=""):
        self.calls.append("author_recipe"); return self._recipe


def company_overview_shapes():
    return [
        sp("회사개요표", 359570, 2560000, 6121399, 2046822, tag="graphicFrame", table_h=1750000),
        sp("연도박스2024", 300000, 4540000, 440000, 200000, [txt("2024", sz=1000, wrap="none")]),
        sp("불릿2024", 800000, 4540000, 2230000, 600000,
           [txt("", paras=["국가고객만족도 14년 연속 1위", "ISMS-P 인증 획득"])]),
        sp("연도박스2023", 300000, 5260000, 440000, 200000, [txt("2023", sz=1000, wrap="none")]),
    ]


def main():
    ok = True
    assets = {"page_types": json.load(open(os.path.join(HERE, "assets/page_types.example.json"), encoding="utf-8"))}

    # 1) 정상 작업 → 분류 + 검수대기
    job = {"id": "job-demo-001", "shapes": company_overview_shapes(), "size": SIZE}
    man = pipeline.run_job(job, assets, CFG)
    print(f"[1] 분류={man['page_type']} ({man['classify']['source']}) / 검증={man['lint']['verdict']} / 상태={man['status']}")
    if man["page_type"] != "body_company_overview" or man["status"] != "ready_for_review":
        print("   ✗ 실패"); ok = False
    else:
        print("   ✓ 통과 (정상 페이지 → 검수 대기)")

    # 2) 신규 유형 → 큐잉 (게이트웨이 없음 → 레시피 제안 없음)
    new_shapes = [sp("본문", 400000, 3000000, 5000000, 400000, [txt("일반 텍스트")])]
    job2 = {"id": "job-demo-002", "shapes": new_shapes, "size": SIZE}
    man2 = pipeline.run_job(job2, assets, CFG)
    print(f"\n[2] 분류={man2['page_type']} / 상태={man2['status']}")
    if man2["status"] != "new_type_queued" or "recipe_proposal" in man2:
        print("   ✗ 실패: 게이트웨이 없는데 recipe_proposal 생성됨"); ok = False
    else:
        print(f"   ✓ 통과 (신규 유형 → 큐잉, 제안 없음, 알림링크={man2['notify']['link']})")

    # 2b) 신규 유형 + cloud 가능 → AI 레시피 초안 자동 제안 (자동 적용 안 함)
    proposed = {"type": "new_body", "template_slide": "ppt/slides/slide1.xml",
                "ops": [{"op": "text_inject", "slot": "title", "from": "title"}]}
    gw = StubGateway(recipe=proposed, cloud=True)
    man2b = pipeline.run_job({"id": "job-demo-002b", "shapes": new_shapes, "size": SIZE},
                             assets, CFG, gateway=gw)
    rp = man2b.get("recipe_proposal") or {}
    print(f"\n[2b] 상태={man2b['status']} / 제안={rp.get('ok')} / author_recipe호출={'author_recipe' in gw.calls}")
    ok2b = (man2b["status"] == "new_type_queued"          # 자동 적용 금지 — 여전히 큐잉
            and rp.get("ok") is True
            and rp.get("recipe") == proposed
            and "author_recipe" in gw.calls
            and "AI 레시피" in man2b["notify"].get("body", ""))
    if not ok2b:
        print(f"   ✗ 실패: man={man2b}"); ok = False
    else:
        print("   ✓ 통과 (cloud → 레시피 초안 제안 첨부, 상태는 검수 큐 유지)")

    # 2c) 신규 유형 + cloud 불가 → 제안 시도 안 함(author_recipe 미호출)
    gw_off = StubGateway(recipe=proposed, cloud=False)
    man2c = pipeline.run_job({"id": "job-demo-002c", "shapes": new_shapes, "size": SIZE},
                             assets, CFG, gateway=gw_off)
    rpc = man2c.get("recipe_proposal") or {}
    print(f"\n[2c] 제안시도={rpc.get('attempted')} / author_recipe호출={'author_recipe' in gw_off.calls}")
    if rpc.get("attempted") is not False or "author_recipe" in gw_off.calls:
        print(f"   ✗ 실패: cloud 불가인데 레시피 작성 시도함 man={man2c}"); ok = False
    else:
        print("   ✓ 통과 (cloud 불가 → 레시피 작성 건너뜀)")

    # 3) 직원 수정 학습 루프: 후보 ↔ 직원 최종본 diff
    cand = [sp("불릿2024", 800000, 4540000, 2230000, 600000, [txt("국가고객만족도 14년 연속 1위", sz=700)])]
    final = [sp("불릿2024", 870000, 4540000, 2230000, 600000, [txt("국가고객만족도(NCSI) 14년 연속 1위 달성", sz=800)])]
    diffs = learn.diff_shapes(cand, final)
    kinds = sorted(d["kind"] for d in diffs)
    print(f"\n[3] 직원 수정 diff → {kinds}")
    if not ({"moved", "font_size", "text_edit"} <= set(kinds)):
        print("   ✗ 실패: 교정 미검출"); ok = False
    else:
        print("   ✓ 통과 (이동·폰트·텍스트 교정 검출 → 학습 누적 가능)")

    # 4) PPTX 입력 + source_slots → 결선된 transform → lint → ready_for_review
    tmp = tempfile.mkdtemp(prefix="pf_pipe4_")
    try:
        tpl_dir = os.path.join(tmp, "tpl_unpacked")
        os.makedirs(tpl_dir)
        build_template(tpl_dir)
        template_pptx = os.path.join(tmp, "template.pptx")
        pptx_io.pack(tpl_dir, template_pptx)
        shutil.rmtree(tpl_dir)  # 파이프라인이 다시 unpack 한다는 점을 보이기 위해 정리

        img = os.path.join(tmp, "cert.png")
        make_png(img)

        # 합성 템플릿은 slide1.xml 사용 → 레시피의 template_slide 를 in-memory 로 override
        with open(os.path.join(HERE, "assets/recipes/body_company_overview.json"),
                  encoding="utf-8") as fh:
            recipe = json.load(fh)
        recipe["template_slide"] = "ppt/slides/slide1.xml"

        assets2 = {
            "page_types": assets["page_types"],
            "recipes": {"body_company_overview": recipe},
        }

        job4 = {
            "id": "job-demo-004",
            "shapes": company_overview_shapes(),  # 분류용
            "size": SIZE,
            "template_pptx": template_pptx,
            "workdir": os.path.join(tmp, "wd"),
            "source_slots": {
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
                "images": {"certificate_iso":
                           {"path": img, "x": 6700000, "y": 1400000,
                            "cx": 2400000, "cy": 1800000}},
            },
        }

        man4 = pipeline.run_job(job4, assets2, CFG)
        print(f"\n[4] 분류={man4.get('page_type')} / 변환={bool(man4.get('transform'))} "
              f"/ 검증={man4.get('lint',{}).get('verdict')} / 상태={man4.get('status')}")
        tx = man4.get("transform") or {}
        out_pptx = tx.get("out_pptx")
        ok4 = (man4.get("page_type") == "body_company_overview"
               and man4.get("status") == "ready_for_review"
               and man4.get("lint", {}).get("fails") == 0
               and out_pptx and os.path.exists(out_pptx)
               and "error" not in tx)
        if not ok4:
            print(f"   ✗ 실패: manifest={man4}")
            ok = False
        else:
            with zipfile.ZipFile(out_pptx) as z:
                xml = z.read("ppt/slides/slide1.xml").decode("utf-8")
                names = z.namelist()
            checks = [
                ('name="year_box_0"' in xml, "shape_rebuild year_box_0"),
                ("주식회사 가나다" in xml, "table_rebuild 회사명"),
                (any(n.startswith("ppt/media/") for n in names),
                 "image_reuse 미디어 첨부"),
                ("1. 제안사 소개 &gt; 1.1 회사 개요" in xml,
                 "text_inject 브레드크럼 치환"),
            ]
            sub_ok = all(c[0] for c in checks)
            for cond, name in checks:
                print(f"   {'✓' if cond else '✗'} {name}")
            if not sub_ok:
                ok = False
            else:
                print("   ✓ 통과 (PPTX → 변환 → 출력 → 린트 PASS → 검수 대기)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 5) 데몬 ingest 결선: inbox PPTX → run_job → 검수 store 기록
    tmp5 = tempfile.mkdtemp(prefix="pf_daemon_")
    try:
        store = JobStore(os.path.join(tmp5, "store"))
        assets5 = watch_inbox.load_assets(CFG)  # 예시 page_types 로드

        # 5a) 표+헤더 초안(연도박스 없음) → body_table 결정론 분류 + 초안을 검수용 out.pptx 로 노출
        tpl_tree = os.path.join(tmp5, "draft_tree")
        os.makedirs(tpl_tree)
        build_template(tpl_tree)
        draft_a = os.path.join(tmp5, "고객초안.pptx")
        pptx_io.pack(tpl_tree, draft_a)
        man_a = watch_inbox.ingest(draft_a, CFG, store, assets5, gateway=None)
        print(f"\n[5a] 데몬 ingest → 분류={man_a.get('page_type')} "
              f"({man_a.get('classify', {}).get('source')}) / 상태={man_a.get('status')}")
        ok5a = (man_a.get("page_type") == "body_table"
                and man_a.get("classify", {}).get("source") == "deterministic"
                and store.read_manifest(man_a["id"]) is not None
                and store.out_pptx_path(man_a["id"]))   # 초안이 out.pptx 로 노출
        if not ok5a:
            print(f"   ✗ 실패: man={man_a}"); ok = False
        else:
            print("   ✓ 통과 (inbox→분류→store 기록, 초안 검수 노출)")

        # 5b) 신규 유형 초안 + 사이드카(template_pptx+source_slots) → transform_inputs 보존
        draft_b = os.path.join(tmp5, "신규유형.pptx")
        _pack_pptx(draft_b, MINI_TEXT_SLIDE, os.path.join(tmp5, "b"))
        with open(os.path.splitext(draft_b)[0] + ".job.json", "w", encoding="utf-8") as fh:
            json.dump({"template_pptx": draft_a,
                       "source_slots": {"section_path": "X > Y"}}, fh, ensure_ascii=False)
        man_b = watch_inbox.ingest(draft_b, CFG, store, assets5, gateway=None)
        saved_b = store.read_manifest(man_b["id"]) or {}
        ti = saved_b.get("transform_inputs") or {}
        print(f"\n[5b] 신규유형 ingest → 상태={man_b.get('status')} "
              f"/ transform_inputs={'있음' if ti else '없음'}")
        ok5b = (man_b.get("status") == "new_type_queued"
                and ti.get("template_pptx") == draft_a
                and ti.get("source_slots", {}).get("section_path") == "X > Y")
        if not ok5b:
            print(f"   ✗ 실패: saved={saved_b}"); ok = False
        else:
            print("   ✓ 통과 (사이드카 병합 → 신규 유형 큐잉 + 재생성용 입력 보존)")

        # 5c) process() 가 처리한 inbox 파일을 보관 폴더로 이동(재기동 시 재처리 방지)
        inbox = os.path.join(tmp5, "inbox")
        os.makedirs(inbox)
        processed_dir = os.path.join(tmp5, "_processed")
        draft_c = os.path.join(inbox, "초안C.pptx")
        pptx_io.pack(tpl_tree, draft_c)
        with open(os.path.splitext(draft_c)[0] + ".job.json", "w", encoding="utf-8") as fh:
            json.dump({"note": "사이드카도 함께 이동되어야 함"}, fh, ensure_ascii=False)
        man_c = watch_inbox.process(
            draft_c, rt=(store, assets5, None),
            archive_dirs=(processed_dir, os.path.join(tmp5, "_failed")))
        moved = (not os.path.exists(draft_c)
                 and os.path.exists(os.path.join(processed_dir, "초안C.pptx"))
                 and os.path.exists(os.path.join(processed_dir, "초안C.job.json")))
        print(f"\n[5c] process+보관 → 분류={man_c and man_c.get('page_type')} / inbox 비움={not os.path.exists(draft_c)}")
        ok5c = (man_c is not None
                and store.read_manifest(man_c["id"]) is not None
                and moved)
        if not ok5c:
            print(f"   ✗ 실패: moved={moved} man={man_c}"); ok = False
        else:
            print("   ✓ 통과 (초안+사이드카 → _processed 이동, store 기록 유지)")

        # 5d) 보관기간 만료 삭제: 오래된 파일만 지우고 최신은 보존
        old_f = os.path.join(processed_dir, "old.pptx")
        new_f = os.path.join(processed_dir, "fresh.pptx")
        for f in (old_f, new_f):
            with open(f, "wb") as fh:
                fh.write(b"PK")
        now = 1_700_000_000.0
        os.utime(old_f, (now - 40 * 86400, now - 40 * 86400))  # 40일 전
        os.utime(new_f, (now - 1 * 86400, now - 1 * 86400))    # 1일 전
        removed = watch_inbox.cleanup_old(processed_dir, 30, now=now)
        print(f"\n[5d] 보관기간 정리(>30일) → 삭제 {removed}건")
        ok5d = (removed == 1 and not os.path.exists(old_f) and os.path.exists(new_f))
        if not ok5d:
            print(f"   ✗ 실패: removed={removed} old={os.path.exists(old_f)} new={os.path.exists(new_f)}")
            ok = False
        else:
            print("   ✓ 통과 (40일 전 1건 삭제, 1일 전 보존)")

        # 5e) launchd plist 템플릿이 유효한 plist 인지(plistlib 파싱)
        import plistlib
        plist_path = os.path.join(HERE, "daemon", "com.proposalfactory.watch.plist")
        with open(plist_path, "rb") as fh:
            pl = plistlib.load(fh)
        args = pl.get("ProgramArguments", [])
        ok5e = (pl.get("Label") == "com.proposalfactory.watch"
                and any("watch_inbox.py" in a for a in args)
                and pl.get("RunAtLoad") is True and pl.get("KeepAlive") is True)
        print(f"\n[5e] launchd plist 파싱 → Label={pl.get('Label')}")
        if not ok5e:
            print(f"   ✗ 실패: {pl}"); ok = False
        else:
            print("   ✓ 통과 (유효 plist, watch_inbox 가동 인자·RunAtLoad·KeepAlive)")

        # 5f) source_slots 자동 추출: 사이드카가 template 만 줄 때 초안 slot:<key> 텍스트 → 표준 템플릿
        from engine import geometry
        slot_draft_xml = (MINI_TEXT_SLIDE
                          .replace('name="본문"', 'name="slot:breadcrumb"')
                          .replace('일반 본문 텍스트', '고객이 쓴 브레드크럼'))
        # 단위: 추출기 자체
        sslots = geometry.source_slots_from_shapes(geometry.extract_shapes(slot_draft_xml))
        unit_ok = sslots == {"breadcrumb": "고객이 쓴 브레드크럼"}

        std_tree = os.path.join(tmp5, "std")
        os.makedirs(std_tree)
        build_template(std_tree)             # 표준 템플릿(slot:breadcrumb 보유)
        std_pptx = os.path.join(tmp5, "standard.pptx")
        pptx_io.pack(std_tree, std_pptx)

        draft_f = os.path.join(tmp5, "filled.pptx")   # 고객 초안(다른 파일)
        _pack_pptx(draft_f, slot_draft_xml, os.path.join(tmp5, "f"))
        with open(os.path.splitext(draft_f)[0] + ".job.json", "w", encoding="utf-8") as fh:
            json.dump({"template_pptx": std_pptx}, fh, ensure_ascii=False)  # source_slots 없음
        assets_f = {
            "page_types": [{"type": "filled", "match": {"n_text_min": 1}}],
            "recipes": {"filled": {"type": "filled",
                                   "template_slide": "ppt/slides/slide1.xml",
                                   "ops": [{"op": "text_inject", "slot": "breadcrumb",
                                            "from": "breadcrumb"}]}},
            "base_dir": tmp5,
        }
        man_f = watch_inbox.ingest(draft_f, CFG, store, assets_f, gateway=None)
        txf = man_f.get("transform") or {}
        out_f = txf.get("out_pptx")
        content_ok = False
        if out_f and os.path.exists(out_f):
            import zipfile as _zf
            with _zf.ZipFile(out_f) as z:
                content_ok = "고객이 쓴 브레드크럼" in z.read("ppt/slides/slide1.xml").decode("utf-8")
        print(f"\n[5f] 자동 추출 → 단위={unit_ok} / 변환 오류={'있음' if 'error' in txf else '없음'} / 초안 텍스트 반영={content_ok}")
        ok5f = unit_ok and "error" not in txf and content_ok
        if not ok5f:
            print(f"   ✗ 실패: sslots={sslots} tx={txf}"); ok = False
        else:
            print("   ✓ 통과 (사이드카 template + 초안 slot 텍스트 자동 추출 → 표준 템플릿에 반영)")

        # 6) 다중 페이지 덱 분류(Round A): 페이지별 1:1 분류 + deck manifest
        from run_transform_demo import build_template_2slide
        deck_tree = os.path.join(tmp5, "deck_tree")
        os.makedirs(deck_tree)
        build_template_2slide(deck_tree)   # slide1=회사개요(표), slide2=텍스트1개
        deck_pptx = os.path.join(tmp5, "초안덱.pptx")
        pptx_io.pack(deck_tree, deck_pptx)
        man_d = watch_inbox.ingest(deck_pptx, CFG, store, assets5, gateway=None)
        pages = man_d.get("pages") or []
        print(f"\n[6] 덱 분류 → kind={man_d.get('kind')} pages={man_d.get('page_count')} 상태={man_d.get('status')}")
        ok6 = (man_d.get("kind") == "deck" and man_d.get("page_count") == 2
               and len(pages) == 2
               and pages[0]["index"] == 0 and pages[0]["page_type"] == "body_table"
               and pages[1]["page_type"] == "unknown"
               and man_d.get("status") == "new_type_queued"
               and store.read_manifest(man_d["id"]) is not None)
        if not ok6:
            print(f"   ✗ 실패: {[(p['index'], p['page_type']) for p in pages]}"); ok = False
        else:
            print(f"   ✓ 통과 (1:1 페이지별 분류 p0={pages[0]['page_type']}/p1={pages[1]['page_type']}, store 기록)")

        # 7) 1:1 덱 변환(B2): 페이지별 verbatim 매핑 → 표준 템플릿 (오프라인 + mock AI)
        from engine import transform as _tf
        std2_tree = os.path.join(tmp5, "std2")
        os.makedirs(std2_tree)
        build_template(std2_tree)                      # slot:breadcrumb, slot:key_point 보유
        std2 = os.path.join(tmp5, "std2.pptx")
        pptx_io.pack(std2_tree, std2)
        recipe_doc = {"type": "doc", "template_slide": "ppt/slides/slide1.xml",
                      "ops": [{"op": "text_inject", "slot": "breadcrumb", "from": "breadcrumb"},
                              {"op": "text_inject", "slot": "key_point", "from": "summary"}]}
        assets_doc = {"page_types": [{"type": "doc", "match": {"n_text_min": 1}}],
                      "recipes": {"doc": recipe_doc}, "base_dir": tmp5}

        def _sp(name, text):
            return {"tag": "sp", "name": name, "x": 0, "y": 0, "cx": 9, "cy": 9,
                    "texts": [{"text": text, "paras": [text], "sz": 700,
                               "wrap": "square", "autofit": False, "fonts": set()}]}
        p0 = [_sp("a", "페이지0 브레드크럼"), _sp("b", "페이지0 요약")]
        slides2 = [{"slide_path": "ppt/slides/slide1.xml", "shapes": p0, "size": SIZE},
                   {"slide_path": "ppt/slides/slide2.xml",
                    "shapes": [_sp("a", "페이지1 브레드크럼"), _sp("b", "페이지1 요약")], "size": SIZE}]

        def _slide_xml(pptx):
            with zipfile.ZipFile(pptx) as z:
                return z.read("ppt/slides/slide1.xml").decode("utf-8")

        # (a) 오프라인 — 순서 기반(positional), 문구 verbatim
        res_off = pipeline.run_deck(slides2, assets_doc, CFG, None, std2,
                                    os.path.join(tmp5, "deck_off"))
        pg = res_off["pages"]
        x0 = _slide_xml(pg[0]["transform"]["out_pptx"]) if pg[0].get("transform", {}).get("out_pptx") else ""
        ok7a = (res_off["page_count"] == 2 and pg[0].get("mapped") == "positional"
                and "페이지0 브레드크럼" in x0 and "페이지0 요약" in x0
                and res_off["status"] == "ready_for_review")
        print(f"\n[7a] 덱 변환 오프라인 → pages={res_off['page_count']} mapped={pg[0].get('mapped')} 상태={res_off['status']}")
        if not ok7a:
            print(f"   ✗ 실패: {pg}"); ok = False
        else:
            print("   ✓ 통과 (1:1·순서 매핑, 초안 문구 verbatim 반영)")

        # (b) mock AI — 인덱스 배정(swap) 준수, 문구 verbatim
        class _MapGW:
            def can_use_cloud(self): return True
            def local_classify(self, s): return None
            def cloud_classify(self, s): return None
            def map_content(self, blocks, slots, hint=""):
                return {"assign": {"breadcrumb": 1, "key_point": 0}}   # 0↔1 뒤바꿈
        res_ai = pipeline.run_deck([slides2[0]], assets_doc, CFG, _MapGW(), std2,
                                   os.path.join(tmp5, "deck_ai"))
        pa = res_ai["pages"][0]
        xa = _slide_xml(pa["transform"]["out_pptx"]) if pa.get("transform", {}).get("out_pptx") else ""
        _, _, bc_block, _ = _tf._find_slot(xa, "breadcrumb") if xa else (0, 0, "", "")
        ok7b = (pa.get("mapped") == "ai"
                and "페이지0 요약" in bc_block          # swap: breadcrumb ← block1(요약)
                and "페이지0 브레드크럼" in xa)          # block0 도 (key_point 로) verbatim 존재
        print(f"\n[7b] 덱 변환 mock AI → mapped={pa.get('mapped')} / breadcrumb 슬롯=verbatim swap")
        if not ok7b:
            print(f"   ✗ 실패: bc_block={bc_block[:80]!r}"); ok = False
        else:
            print("   ✓ 통과 (AI 인덱스 배정 준수 + 문구 변경 0)")

        # (c) 운영자 페이지별 타입 지정(forced_types) — 분류 대신 지정 타입 사용
        # page0 도형은 표 없음(원래 doc 매칭) 이지만 운영자가 'doc' 로 명시 지정
        res_op = pipeline.run_deck([{"slide_path": "ppt/slides/slide1.xml",
                                     "shapes": p0, "size": SIZE}],
                                   {"page_types": [], "recipes": {"doc": recipe_doc},
                                    "base_dir": tmp5},
                                   CFG, None, std2, os.path.join(tmp5, "deck_op"),
                                   forced_types={0: "doc"})
        po = res_op["pages"][0]
        ok7c = (po.get("source") == "operator" and po.get("page_type") == "doc"
                and po.get("transform", {}).get("recipe") == "doc")
        print(f"\n[7c] 운영자 지정 → source={po.get('source')} type={po.get('page_type')} (page_types 비어도 변환)")
        if not ok7c:
            print(f"   ✗ 실패: {po}"); ok = False
        else:
            print("   ✓ 통과 (forced_types 로 분류 우회·지정 타입 변환)")

        # (d) 내용 기반 자동 분류 — forced_types 없이 mock gateway 가 타입 선택(시그니처 미매칭)
        class _ClsGW:
            def can_use_cloud(self): return True
            def local_classify(self, s): return None
            def cloud_classify(self, s): return None
            def classify_page(self, profile, catalog, hint=""): return "doc"   # 내용 보고 선택
            def map_content(self, blocks, slots, hint=""): return {"assign": {}}
        assets_cls = {"page_types": [{"type": "doc", "desc": "문서", "match": {}}],
                      "recipes": {"doc": recipe_doc}, "base_dir": tmp5}
        res_cls = pipeline.run_deck([{"slide_path": "ppt/slides/slide1.xml", "shapes": p0, "size": SIZE}],
                                    assets_cls, CFG, _ClsGW(), std2, os.path.join(tmp5, "deck_cls"))
        pc = res_cls["pages"][0]
        ok7d = (pc.get("source") == "content_llm" and pc.get("page_type") == "doc"
                and pc.get("transform", {}).get("recipe") == "doc")
        print(f"\n[7d] 내용 기반 자동 분류 → source={pc.get('source')} type={pc.get('page_type')} (forced 없음·시그니처 미매칭)")
        if not ok7d:
            print(f"   ✗ 실패: {pc}"); ok = False
        else:
            print("   ✓ 통과 (gateway.classify_page 로 자동 타입 선택 → 변환)")

        # 8) 단일 파일 덱 조립 + 초안 이미지 carry-over (1:1)
        from run_transform_demo import build_template_2slide
        tmpl_tree = os.path.join(tmp5, "deck_tmpl")
        os.makedirs(tmpl_tree)
        build_template_2slide(tmpl_tree)                       # slide1(breadcrumb/key_point), slide2(page2_title)
        deck_tmpl = os.path.join(tmp5, "deck_tmpl.pptx")
        pptx_io.pack(tmpl_tree, deck_tmpl)
        # 초안: <p:pic> 1개 있는 1슬라이드 pptx (carry-over 소스)
        png = os.path.join(tmp5, "p.png")
        make_png(png)
        PIC = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
               'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
               '<p:cSld><p:spTree>'
               '<p:nvGrpSpPr><p:cNvPr id="1" name="G"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
               '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
               '<p:pic><p:nvPicPr><p:cNvPr id="5" name="img"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
               '<p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
               '<p:spPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="500" cy="500"/></a:xfrm>'
               '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
               '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
        pic_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/pic.png"/>'
                    '</Relationships>')
        ct_pic = CONTENT_TYPES.replace("</Types>", '<Default Extension="png" ContentType="image/png"/></Types>')
        dtree = os.path.join(tmp5, "draft_tree")
        for rel, content in {"[Content_Types].xml": ct_pic, "ppt/presentation.xml": PRESENTATION_XML,
                             "ppt/slides/slide1.xml": PIC, "ppt/slides/_rels/slide1.xml.rels": pic_rels}.items():
            p = os.path.join(dtree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(content)
        os.makedirs(os.path.join(dtree, "ppt/media"), exist_ok=True)
        shutil.copyfile(png, os.path.join(dtree, "ppt/media/pic.png"))
        draft_pptx = os.path.join(tmp5, "draft_pic.pptx")
        pptx_io.pack(dtree, draft_pptx)

        rc1 = {"type": "t1", "template_slide": "ppt/slides/slide1.xml",
               "ops": [{"op": "text_inject", "slot": "breadcrumb", "from": "breadcrumb"},
                       {"op": "text_inject", "slot": "key_point", "from": "summary"}]}
        rc2 = {"type": "t2", "template_slide": "ppt/slides/slide2.xml",
               "ops": [{"op": "text_inject", "slot": "page2_title", "from": "page2_title"}]}
        assets_d = {"page_types": [], "recipes": {"t1": rc1, "t2": rc2}, "base_dir": tmp5}
        dslides = [{"slide_path": "ppt/slides/slide1.xml",
                    "shapes": [_sp("a", "AAA브레드"), _sp("b", "BBB키포인트")], "size": SIZE},
                   {"slide_path": "ppt/slides/slide1.xml",   # 같은 초안 슬라이드(pic) 재사용
                    "shapes": [_sp("c", "CCC제목")], "size": SIZE}]
        out_deck = os.path.join(tmp5, "assembled.pptx")
        res_d = pipeline.run_deck(dslides, assets_d, CFG, None, deck_tmpl,
                                  os.path.join(tmp5, "deck_wd"),
                                  forced_types={0: "t1", 1: "t2"},
                                  out_deck=out_deck, draft_pptx=draft_pptx)
        from xml.dom import minidom
        import re as _re2
        with zipfile.ZipFile(out_deck) as z:
            names = z.namelist()
            xml_ok = True
            for n in names:
                if n.endswith((".xml", ".rels")):
                    try:
                        minidom.parseString(z.read(n))
                    except Exception:
                        xml_ok = False
            px = z.read("ppt/presentation.xml").decode("utf-8")
            n_sld = len(_re2.findall(r"<p:sldId\b", px))
            newp = sorted((n for n in names if _re2.match(r"ppt/slides/slide\d+\.xml$", n)),
                          key=lambda n: int(_re2.search(r"(\d+)", n.split("/")[-1]).group(1)))[-2:]
            x_p0 = z.read(newp[0]).decode("utf-8")
            x_p1 = z.read(newp[1]).decode("utf-8")
            carried = [n for n in names if n.startswith("ppt/media/p0_")]
        print(f"\n[8] 덱 조립+carry → 상태={res_d['status']} sldIdLst={n_sld} XML유효={xml_ok}")
        ok8 = (res_d.get("out_deck") == out_deck and xml_ok and n_sld == 2
               and "AAA브레드" in x_p0 and "BBB키포인트" in x_p0
               and "CCC제목" in x_p1
               and "<p:pic>" in x_p0 and len(carried) >= 1)
        if not ok8:
            print(f"   ✗ 실패: carried={carried} p0텍스트={'AAA브레드' in x_p0}"); ok = False
        else:
            print(f"   ✓ 통과 (1:1 단일덱 조립·verbatim·이미지 carry {len(carried)}개, 구조 유효)")
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    print("\n=== PIPELINE/LEARN SELF-CHECK:", "PASS" if ok else "FAIL", "===")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
