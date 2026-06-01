"""run_transform_demo.py — transform.py 4-op 자기검증.

외부 PPTX/이미지 없이 합성 미니 템플릿 + 8x8 PNG 를 즉석 생성하여
text_inject/table_rebuild/image_reuse/shape_rebuild 가 모두 작동함을 검증한다.

검증 항목:
  1) text_inject: breadcrumb 텍스트 치환 (XML 이스케이프 포함)
  2) text_inject + one_line: summary 의 줄바꿈 압축
  3) table_rebuild: 헤더 보존 + 본문 3행 재생성 + highlight 채움
  4) image_reuse: <p:pic> 생성 + ppt/media/ 복사 + slide rels 갱신
  5) shape_rebuild: year_box_i / bullet_i 도형들 균일 간격 배치
  6) linter.lint(): 출력 슬라이드에 fail 0
종료코드 0 = 통과.
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import transform, pptx_io, geometry, linter
from engine.config import load_config

CFG = load_config()
SLIDE_SIZE = (6840538, 10261600)  # cx, cy (보건복지부 템플릿 기준)


# ---------------- 합성 픽스처 ----------------

def make_png(path, w=8, h=8, rgb=(255, 0, 0)):
    """표준 라이브러리만으로 8x8 단색 PNG 생성."""
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    idat = zlib.compress(raw, 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>"""

PRESENTATION_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldSz cx="10261600" cy="6840538"/>
</p:presentation>"""

SLIDE1_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name="Group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>

<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="slot:breadcrumb"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="360000" y="360000"/><a:ext cx="6000000" cy="300000"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>
<a:p><a:r><a:rPr lang="ko-KR" sz="900"/><a:t>(템플릿 브레드크럼)</a:t></a:r></a:p>
</p:txBody></p:sp>

<p:sp>
<p:nvSpPr><p:cNvPr id="3" name="slot:key_point"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="360000" y="800000"/><a:ext cx="6000000" cy="400000"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>
<a:p><a:r><a:rPr lang="ko-KR" sz="1100" b="1"/><a:t>(템플릿 KEY POINT)</a:t></a:r></a:p>
</p:txBody></p:sp>

<p:graphicFrame>
<p:nvGraphicFramePr><p:cNvPr id="4" name="slot:company_overview"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
<p:xfrm><a:off x="360000" y="1400000"/><a:ext cx="6121000" cy="1800000"/></p:xfrm>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">
<a:tbl><a:tblPr/>
<a:tblGrid><a:gridCol w="2000000"/><a:gridCol w="4121000"/></a:tblGrid>
<a:tr h="400000">
<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR" sz="900" b="1"/><a:t>구분</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc>
<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR" sz="900" b="1"/><a:t>내용</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc>
</a:tr>
</a:tbl>
</a:graphicData></a:graphic>
</p:graphicFrame>

<p:sp>
<p:nvSpPr><p:cNvPr id="5" name="slot:history_timeline"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="360000" y="3600000"/><a:ext cx="6121000" cy="2500000"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>
</p:sp>

<p:sp>
<p:nvSpPr><p:cNvPr id="6" name="slot:certificate_iso"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="6700000" y="1400000"/><a:ext cx="2400000" cy="1800000"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>
</p:sp>

</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""

SLIDE1_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
</Relationships>"""


def build_template(workdir):
    """workdir 안에 미니 PPTX 트리를 생성한다(이미 unpack 된 상태)."""
    files = {
        "[Content_Types].xml": CONTENT_TYPES,
        "ppt/presentation.xml": PRESENTATION_XML,
        "ppt/slides/slide1.xml": SLIDE1_XML,
        "ppt/slides/_rels/slide1.xml.rels": SLIDE1_RELS,
    }
    for rel, content in files.items():
        path = os.path.join(workdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# 슬롯 1개짜리 간단 슬라이드(다중 슬라이드 테스트용 2번째 페이지).
_SLIDE2_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name="Group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="slot:page2_title"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="360000" y="360000"/><a:ext cx="6000000" cy="400000"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>
<a:p><a:r><a:rPr lang="ko-KR" sz="1100"/><a:t>(2페이지 제목)</a:t></a:r></a:p>
</p:txBody></p:sp>
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""

_CONTENT_TYPES_2 = CONTENT_TYPES.replace(
    '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>',
    '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
    '<Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')


def build_template_2slide(workdir):
    """slide1(회사개요) + slide2(제목 슬롯) 2장짜리 미니 PPTX 트리."""
    files = {
        "[Content_Types].xml": _CONTENT_TYPES_2,
        "ppt/presentation.xml": PRESENTATION_XML,
        "ppt/slides/slide1.xml": SLIDE1_XML,
        "ppt/slides/_rels/slide1.xml.rels": SLIDE1_RELS,
        "ppt/slides/slide2.xml": _SLIDE2_XML,
        "ppt/slides/_rels/slide2.xml.rels": SLIDE1_RELS,
    }
    for rel, content in files.items():
        path = os.path.join(workdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# ---------------- 검증 ----------------

def assert_true(cond, msg, errors):
    if cond:
        print(f"   ✓ {msg}")
    else:
        print(f"   ✗ {msg}")
        errors.append(msg)


def main():
    errors = []
    tmp = tempfile.mkdtemp(prefix="pf_transform_")
    try:
        template_dir = os.path.join(tmp, "tpl")
        os.makedirs(template_dir)
        build_template(template_dir)

        # 이미지 픽스처
        img_path = os.path.join(tmp, "cert.png")
        make_png(img_path)

        recipe = {
            "type": "body_company_overview",
            "template_slide": "ppt/slides/slide1.xml",
            "ops": [
                {"op": "text_inject", "slot": "breadcrumb", "from": "section_path"},
                {"op": "text_inject", "slot": "key_point",
                 "from": "summary", "rule": "one_line"},
                {"op": "table_rebuild", "slot": "company_overview",
                 "from": "company_fields",
                 "style": {"label_col_fill": "F2F2F2", "highlight_fill": "DCEFFE"}},
                {"op": "image_reuse", "slot": "certificate_iso",
                 "from": "images.certificate_iso"},
                {"op": "shape_rebuild", "slot": "history_timeline",
                 "from": "year_bullets",
                 "design": {"year_box_fill": "2B8ECB", "year_text_color": "FFFFFF",
                            "pointer_fill": "1A3D46",
                            "uniform_gap_emu": 46000, "columns": 1}},
            ],
        }

        source = {
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
            "images": {
                "certificate_iso": {"path": img_path,
                                    "x": 6700000, "y": 1400000,
                                    "cx": 2400000, "cy": 1800000},
            },
        }

        out_pptx = os.path.join(tmp, "out.pptx")
        transform.apply(recipe, source, template_dir, out_pptx, CFG)

        print(f"[OUT] {out_pptx}")
        print(f"      size = {os.path.getsize(out_pptx)} bytes")

        # ---- 검증 ----
        print("\n[1] 출력 PPTX zip 무결성")
        with zipfile.ZipFile(out_pptx) as z:
            names = z.namelist()
            assert_true("ppt/slides/slide1.xml" in names,
                        "slide1.xml 포함", errors)
            assert_true("[Content_Types].xml" in names,
                        "[Content_Types].xml 포함", errors)
            media = [n for n in names if n.startswith("ppt/media/")]
            assert_true(len(media) == 1,
                        f"미디어 1건 추가 ({media})", errors)
            slide_xml = z.read("ppt/slides/slide1.xml").decode("utf-8")
            rels_xml = z.read("ppt/slides/_rels/slide1.xml.rels").decode("utf-8")
            ct_xml = z.read("[Content_Types].xml").decode("utf-8")

        print("\n[2] text_inject — breadcrumb")
        # > 는 XML 이스케이프되어 &gt; 로 들어감
        expect = "1. 제안사 소개 &gt; 1.1 회사 개요"
        assert_true(expect in slide_xml, f"치환 텍스트 존재: '{expect}'", errors)

        print("\n[3] text_inject + one_line — summary")
        expect = "국내 최대 통신사 제안"
        assert_true(f"<a:t>{expect}</a:t>" in slide_xml,
                    f"줄바꿈 압축 결과 한 줄: '{expect}'", errors)

        print("\n[4] table_rebuild")
        # 본문 행 3개 + 헤더 1개 = 총 4 행
        import re as _re
        tr_count = len(_re.findall(r"<a:tr\b", slide_xml))
        assert_true(tr_count == 4, f"표 행 수 = {tr_count} (기대 4)", errors)
        assert_true("주식회사 가나다" in slide_xml, "회사명 행 존재", errors)
        assert_true('val="DCEFFE"' in slide_xml,
                    "highlight 채움 적용(대표자 행)", errors)
        assert_true('val="F2F2F2"' in slide_xml,
                    "라벨 컬럼 채움 적용", errors)

        print("\n[5] image_reuse")
        assert_true('<p:pic>' in slide_xml, "<p:pic> 생성", errors)
        assert_true('name="slot:certificate_iso"' in slide_xml,
                    "슬롯 이름 보존", errors)
        # rels 에 image relationship 추가
        assert_true('Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"'
                    in rels_xml, "rels 에 image 관계 추가", errors)
        assert_true('Target="../media/cert.png"' in rels_xml,
                    "rels Target 정확", errors)
        # Content_Types 에 png 확장자 추가
        assert_true('Extension="png"' in ct_xml,
                    "[Content_Types].xml 에 png 등록", errors)

        print("\n[6] shape_rebuild")
        for i in range(3):
            assert_true(f'name="year_box_{i}"' in slide_xml,
                        f"year_box_{i} 도형 생성", errors)
            assert_true(f'name="bullet_{i}"' in slide_xml,
                        f"bullet_{i} 도형 생성", errors)
        assert_true('slot:history_timeline' not in slide_xml,
                    "원본 슬롯 도형 제거됨", errors)

        # 균일 간격 검증: year_box_i 의 y 좌표 차이가 일정
        ys = []
        for i in range(3):
            m = _re.search(
                rf'name="year_box_{i}".*?<a:off x="\d+" y="(\d+)"',
                slide_xml, _re.S)
            if m:
                ys.append(int(m.group(1)))
        if len(ys) == 3:
            gap1 = ys[1] - ys[0]
            gap2 = ys[2] - ys[1]
            assert_true(gap1 == gap2 and gap1 > 0,
                        f"균일 간격 (gap1={gap1}, gap2={gap2})", errors)
        else:
            assert_true(False,
                        f"year_box y 좌표 파싱 실패 (찾은 개수={len(ys)})", errors)

        print("\n[7] linter 통과")
        shapes = geometry.extract_shapes(slide_xml)
        print(f"   추출된 도형 {len(shapes)}개")
        findings = linter.lint(shapes, SLIDE_SIZE[1], SLIDE_SIZE[0], CFG)
        # linter.lint(shapes, slide_cx, slide_cy, cfg) — cx=가로, cy=세로
        # presentation 의 cx=10261600(가로 와이드) cy=6840538(세로)
        verdict, nf, nw, text = linter.report(findings)
        print(f"   판정={verdict} fails={nf} warns={nw}")
        if text:
            print(text)
        assert_true(nf == 0, f"린터 fail 0", errors)

        print("\n[8] 다중 슬라이드 변환(template_slides[])")
        tdir2 = os.path.join(tmp, "tpl2")
        os.makedirs(tdir2)
        build_template_2slide(tdir2)
        img2 = os.path.join(tmp, "cert2.png")
        make_png(img2)
        multi_recipe = {
            "type": "multi_demo",
            "template_slides": [
                {"template_slide": "ppt/slides/slide1.xml",
                 "ops": [{"op": "text_inject", "slot": "breadcrumb", "from": "section_path"},
                         {"op": "image_reuse", "slot": "certificate_iso",
                          "from": "images.certificate_iso"}]},
                {"template_slide": "ppt/slides/slide2.xml",
                 "ops": [{"op": "text_inject", "slot": "page2_title", "from": "page2_title"}]},
            ],
        }
        multi_src = {
            "section_path": "A > B",
            "page2_title": "둘째 장 제목 주입됨",
            "images": {"certificate_iso": {"path": img2,
                                           "x": 6700000, "y": 1400000,
                                           "cx": 2400000, "cy": 1800000}},
        }
        out2 = os.path.join(tmp, "out2.pptx")
        transform.apply(multi_recipe, multi_src, tdir2, out2, CFG)
        with zipfile.ZipFile(out2) as z:
            s1 = z.read("ppt/slides/slide1.xml").decode("utf-8")
            s2 = z.read("ppt/slides/slide2.xml").decode("utf-8")
            names2 = z.namelist()
        assert_true("A &gt; B" in s1, "slide1 text_inject 적용", errors)
        assert_true("<p:pic>" in s1, "slide1 image_reuse 적용", errors)
        assert_true("둘째 장 제목 주입됨" in s2, "slide2 text_inject 적용", errors)
        assert_true("(2페이지 제목)" not in s2, "slide2 원본 텍스트 치환됨", errors)
        assert_true(any(n.startswith("ppt/media/") for n in names2),
                    "다중 슬라이드 미디어 첨부", errors)

        print("\n[9] group_fill — 그룹 내부 텍스트박스 순서대로 verbatim 채움")
        # slot:grp 그룹 안에 sp 2개(텍스트박스) 를 가진 미니 슬라이드
        grp_slide = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name="Group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
<p:grpSp>
<p:nvGrpSpPr><p:cNvPr id="2" name="slot:grp"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="500" cy="500"/><a:chOff x="0" y="0"/><a:chExt cx="500" cy="500"/></a:xfrm></p:grpSpPr>
<p:sp><p:nvSpPr><p:cNvPr id="3" name="head"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="500" cy="200"/></a:xfrm></p:spPr>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR"/><a:t>(머리말)</a:t></a:r></a:p></p:txBody></p:sp>
<p:sp><p:nvSpPr><p:cNvPr id="4" name="body"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="100" y="320"/><a:ext cx="500" cy="200"/></a:xfrm></p:spPr>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR"/><a:t>(본문)</a:t></a:r></a:p></p:txBody></p:sp>
</p:grpSp>
</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"""
        gtree = os.path.join(tmp, "gtree")
        for rel, content in {"[Content_Types].xml": CONTENT_TYPES,
                             "ppt/presentation.xml": PRESENTATION_XML,
                             "ppt/slides/slide1.xml": grp_slide,
                             "ppt/slides/_rels/slide1.xml.rels": SLIDE1_RELS}.items():
            p = os.path.join(gtree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(content)
        grp_recipe = {"type": "g", "template_slide": "ppt/slides/slide1.xml",
                      "ops": [{"op": "group_fill", "slot": "grp", "from": "lines"}]}
        gout = os.path.join(tmp, "gout.pptx")
        transform.apply(grp_recipe, {"lines": ["새 머리말ZZ", "새 본문YY"]}, gtree, gout, CFG)
        gx = zipfile.ZipFile(gout).read("ppt/slides/slide1.xml").decode("utf-8")
        assert_true("새 머리말ZZ" in gx, "그룹 박스1 verbatim 채움", errors)
        assert_true("새 본문YY" in gx, "그룹 박스2 verbatim 채움", errors)
        assert_true("(머리말)" not in gx and "(본문)" not in gx, "원본 텍스트 치환됨", errors)
        assert_true(gx.count("<p:txBody>") == 2, "그룹 구조(박스 2개) 보존", errors)

        print("\n[10] extract_shapes_deep — 그룹 내부 재귀 추출(절대좌표)")
        top = geometry.extract_shapes(grp_slide)
        deep = geometry.extract_shapes_deep(grp_slide)
        deep_txt = [s for s in deep if s.get("texts")]
        assert_true(len(top) == 1 and top[0]["tag"] == "grpSp",
                    "top-level: 그룹 1블록(내부 미추출)", errors)
        assert_true(len(deep_txt) == 2, f"deep: 그룹 내부 텍스트박스 2개 (실제 {len(deep_txt)})", errors)
        alltext = str([t["paras"][0] for s in deep_txt for t in s["texts"] if t["paras"]])
        assert_true("(머리말)" in alltext and "(본문)" in alltext, "deep: 내부 텍스트 추출", errors)
        b0 = deep_txt[0]
        assert_true(b0["x"] == 200 and b0["y"] == 200,
                    f"deep: 절대좌표 변환 (200,200) 실제 ({b0['x']},{b0['y']})", errors)

        print("\n[11] 폰트 리맵 — 미설치/비렌더 폰트 → 설치 폰트")
        # 공체 typeface 를 가진 슬롯 슬라이드
        font_slide = grp_slide.replace('<a:rPr lang="ko-KR"/>',
                                       '<a:rPr lang="ko-KR"><a:latin typeface="공체 Bold"/></a:rPr>')
        ftree = os.path.join(tmp, "ftree")
        for rel, content in {"[Content_Types].xml": CONTENT_TYPES,
                             "ppt/presentation.xml": PRESENTATION_XML,
                             "ppt/slides/slide1.xml": font_slide,
                             "ppt/slides/_rels/slide1.xml.rels": SLIDE1_RELS}.items():
            p = os.path.join(ftree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(content)
        cfg_remap = dict(CFG)
        cfg_remap["fonts"] = {"remap": {"공체 Bold": "KoPub돋움체_Pro Bold"}}
        fr = {"type": "f", "template_slide": "ppt/slides/slide1.xml",
              "ops": [{"op": "group_fill", "slot": "grp", "from": "x"}]}
        fout = os.path.join(tmp, "fout.pptx")
        transform.apply(fr, {"x": ["A", "B"]}, ftree, fout, cfg_remap)
        fx = zipfile.ZipFile(fout).read("ppt/slides/slide1.xml").decode("utf-8")
        assert_true('typeface="공체 Bold"' not in fx, "공체 제거됨", errors)
        assert_true('typeface="KoPub돋움체_Pro Bold"' in fx, "KoPub 으로 리맵됨", errors)

        print("\n[12] 미매핑 슬롯 플레이스홀더 비우기")
        # slot:filled(매핑) + slot:left(미매핑, 플레이스홀더) 슬라이드
        ph_slide = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                    '<p:cSld><p:spTree>'
                    '<p:nvGrpSpPr><p:cNvPr id="1" name="G"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="slot:filled"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
                    '<p:spPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="500" cy="200"/></a:xfrm></p:spPr>'
                    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR"/><a:t>(원본A)</a:t></a:r></a:p></p:txBody></p:sp>'
                    '<p:sp><p:nvSpPr><p:cNvPr id="3" name="slot:left"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
                    '<p:spPr><a:xfrm><a:off x="100" y="320"/><a:ext cx="500" cy="200"/></a:xfrm></p:spPr>'
                    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR"/><a:t>내용을 입력하세요</a:t></a:r></a:p></p:txBody></p:sp>'
                    '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
        ptree = os.path.join(tmp, "ptree")
        for rel, content in {"[Content_Types].xml": CONTENT_TYPES, "ppt/presentation.xml": PRESENTATION_XML,
                             "ppt/slides/slide1.xml": ph_slide,
                             "ppt/slides/_rels/slide1.xml.rels": SLIDE1_RELS}.items():
            p = os.path.join(ptree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(content)
        pr = {"type": "p", "template_slide": "ppt/slides/slide1.xml",
              "ops": [{"op": "text_inject", "slot": "filled", "from": "v"}]}  # left 는 미매핑
        pout = os.path.join(tmp, "pout.pptx")
        transform.apply(pr, {"v": "채운값X"}, ptree, pout, CFG)   # CFG.transform.clear_unmapped_slots=true
        px = zipfile.ZipFile(pout).read("ppt/slides/slide1.xml").decode("utf-8")
        assert_true("채운값X" in px, "매핑 슬롯 채워짐", errors)
        assert_true("내용을 입력하세요" not in px, "미매핑 슬롯 플레이스홀더 비워짐", errors)
        assert_true('name="slot:left"' in px, "미매핑 슬롯 도형은 보존(텍스트만 비움)", errors)

        print("\n=== TRANSFORM SELF-CHECK:",
              "PASS" if not errors else f"FAIL ({len(errors)})", "===")
        sys.exit(0 if not errors else 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
