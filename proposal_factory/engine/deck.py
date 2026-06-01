"""deck.py — 1:1 다중 페이지 덱 조립 + 초안 이미지 carry-over.

표준 템플릿(마스터/레이아웃/테마/미디어 포함)을 기반으로, 초안 페이지 순서대로(1:1) 각 유형의
템플릿 슬라이드를 **복제·변환**해 새 슬라이드로 추가하고, presentation 의 sldIdLst 를 그 순서로
재지정한다. 초안 슬라이드의 이미지(<p:pic>)는 **위치 그대로** 출력 슬라이드에 옮긴다(디자이너
추후 변경). 표준 라이브러리만 사용(transform 의 헬퍼 재사용).

원본 템플릿 슬라이드 파트는 남되 sldIdLst 에서 빠져 화면에는 출력 페이지만 보인다.
"""
import os
import re
import shutil
import zipfile

from . import pptx_io, transform

_CT_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
              "gif": "image/gif", "emf": "image/x-emf", "wmf": "image/x-wmf",
              "bmp": "image/bmp", "tiff": "image/tiff"}
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_IMG_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
_SLD_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"


def _rels_path(slide_path):
    d, b = os.path.split(slide_path)
    return f"{d}/_rels/{b}.rels"


def pics_from(z, slide_path):
    """draft 슬라이드의 [(pic_xml, media_arcname, media_bytes, ext)] 추출(위치/크기 포함)."""
    try:
        sx = z.read(slide_path).decode("utf-8", "replace")
    except KeyError:
        return []
    relmap = {}
    try:
        rx = z.read(_rels_path(slide_path)).decode("utf-8", "replace")
        for m in re.finditer(r'Id="(rId\d+)"[^>]*?Target="([^"]+)"', rx):
            relmap[m.group(1)] = m.group(2)
    except KeyError:
        pass
    out = []
    for m in re.finditer(r'<p:pic\b', sx):
        st = m.start()
        en = transform._match_block(sx, st, "pic")
        block = sx[st:en]
        bl = re.search(r'<a:blip[^>]*?r:embed="(rId\d+)"', block)
        if not bl:
            continue
        target = relmap.get(bl.group(1))
        if not target:
            continue
        arc = os.path.normpath(os.path.join("ppt/slides", target)).replace(os.sep, "/")
        try:
            data = z.read(arc)
        except KeyError:
            continue
        out.append((block, bl.group(1), arc, data, os.path.splitext(arc)[1].lstrip(".").lower()))
    return out


def _ensure_ct_ext(tree, ext):
    ctp = os.path.join(tree, "[Content_Types].xml")
    with open(ctp, encoding="utf-8") as fh:
        xml = fh.read()
    if f'Extension="{ext}"' in xml:
        return
    ct = _CT_BY_EXT.get(ext, "application/octet-stream")
    xml = xml.replace("</Types>", f'<Default Extension="{ext}" ContentType="{ct}"/></Types>')
    with open(ctp, "w", encoding="utf-8") as fh:
        fh.write(xml)


def _add_slide_ct(tree, k):
    ctp = os.path.join(tree, "[Content_Types].xml")
    with open(ctp, encoding="utf-8") as fh:
        xml = fh.read()
    part = f"/ppt/slides/slide{k}.xml"
    if f'PartName="{part}"' in xml:
        return
    ov = (f'<Override PartName="{part}" ContentType='
          '"application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
    with open(ctp, "w", encoding="utf-8") as fh:
        fh.write(xml.replace("</Types>", ov + "</Types>"))


def _carry_pics(tree, slide_path, pics, tag):
    """draft pics 를 tree 의 slide_path 에 추가(미디어 namespaced + rels + CT). 위치 보존."""
    if not pics:
        return
    sp = os.path.join(tree, slide_path)
    with open(sp, encoding="utf-8") as fh:
        sx = fh.read()
    relp = os.path.join(tree, _rels_path(slide_path))
    os.makedirs(os.path.dirname(relp), exist_ok=True)
    if os.path.exists(relp):
        with open(relp, encoding="utf-8") as fh:
            rx = fh.read()
    else:
        rx = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<Relationships xmlns="{_REL_NS}"></Relationships>')
    next_rid = transform._max_rid(rx) + 1
    media_dir = os.path.join(tree, "ppt", "media")
    os.makedirs(media_dir, exist_ok=True)
    add_pics, add_rels = [], []
    for block, _rid, arc, data, ext in pics:
        newname = f"{tag}_{os.path.basename(arc)}"
        with open(os.path.join(media_dir, newname), "wb") as fh:
            fh.write(data)
        _ensure_ct_ext(tree, ext)
        newrid = f"rId{next_rid}"
        next_rid += 1
        add_rels.append(f'<Relationship Id="{newrid}" Type="{_IMG_REL}" '
                        f'Target="../media/{newname}"/>')
        add_pics.append(re.sub(r'(r:embed=")rId\d+(")', rf'\g<1>{newrid}\g<2>', block, count=1))
    # 초안 이미지는 z-order 최하위(spTree 그룹 속성 직후)에 넣어 템플릿 텍스트/타이틀이
    # 위에 보이게 한다(디자이너가 추후 앞으로 가져오거나 위치 조정).
    pics_xml = "".join(add_pics)
    m = re.search(r'</p:grpSpPr>', sx)
    if m:
        sx = sx[:m.end()] + pics_xml + sx[m.end():]
    else:
        sx = sx.replace("</p:spTree>", pics_xml + "</p:spTree>", 1)
    rx = rx.replace("</Relationships>", "".join(add_rels) + "</Relationships>")
    with open(sp, "w", encoding="utf-8") as fh:
        fh.write(sx)
    with open(relp, "w", encoding="utf-8") as fh:
        fh.write(rx)


def _repoint_presentation(tree, slide_ks):
    """presentation.xml 의 sldIdLst 를 새 슬라이드(slide_ks 순서)로 재지정 + rels 추가."""
    pres = os.path.join(tree, "ppt", "presentation.xml")
    relp = os.path.join(tree, "ppt", "_rels", "presentation.xml.rels")
    with open(pres, encoding="utf-8") as fh:
        px = fh.read()
    if os.path.exists(relp):
        with open(relp, encoding="utf-8") as fh:
            rx = fh.read()
    else:
        os.makedirs(os.path.dirname(relp), exist_ok=True)
        rx = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<Relationships xmlns="{_REL_NS}"></Relationships>')
    next_rid = transform._max_rid(rx) + 1
    rels, sldids, sid = [], [], 256
    for k in slide_ks:
        rid = f"rId{next_rid}"
        next_rid += 1
        rels.append(f'<Relationship Id="{rid}" Type="{_SLD_REL}" Target="slides/slide{k}.xml"/>')
        sldids.append(f'<p:sldId id="{sid}" r:id="{rid}"/>')
        sid += 1
    rx = rx.replace("</Relationships>", "".join(rels) + "</Relationships>")
    new_lst = "<p:sldIdLst>" + "".join(sldids) + "</p:sldIdLst>"
    if re.search(r'<p:sldIdLst\b.*?</p:sldIdLst>', px, re.S):
        px = re.sub(r'<p:sldIdLst\b.*?</p:sldIdLst>', new_lst, px, count=1, flags=re.S)
    elif '<p:sldIdLst/>' in px:
        px = px.replace('<p:sldIdLst/>', new_lst, 1)
    elif '</p:sldMasterIdLst>' in px:
        px = px.replace('</p:sldMasterIdLst>', '</p:sldMasterIdLst>' + new_lst, 1)
    elif '<p:sldSz' in px:           # 마스터 목록이 없으면 슬라이드 크기 앞에 삽입
        px = re.sub(r'(<p:sldSz\b)', new_lst + r'\1', px, count=1)
    else:
        px = px.replace('</p:presentation>', new_lst + '</p:presentation>', 1)
    with open(pres, "w", encoding="utf-8") as fh:
        fh.write(px)
    with open(relp, "w", encoding="utf-8") as fh:
        fh.write(rx)


def validate(pptx_path):
    """조립 결과 PPTX 의 OOXML 무결성 점검 → 문제 목록(빈 리스트면 정상).

    검사: ① 모든 파트의 content-type 존재(Default 확장자 또는 Override),
    ② 모든 .rels 의 내부 Target 이 실제 파트를 가리킴(dangling 없음),
    ③ 슬라이드 XML 이 참조하는 r:id/r:embed 가 해당 .rels 에 정의됨.
    """
    issues = []
    with zipfile.ZipFile(pptx_path) as z:
        names = set(z.namelist())
        try:
            ct = z.read("[Content_Types].xml").decode("utf-8", "replace")
        except KeyError:
            return ["[Content_Types].xml 없음"]
        defaults = {e.lower() for e in re.findall(r'<Default Extension="([^"]+)"', ct)}
        overrides = set(re.findall(r'<Override PartName="([^"]+)"', ct))
        for n in names:
            if n.endswith("/") or n == "[Content_Types].xml" or "/_rels/" in n or n.endswith(".rels"):
                continue
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            if ("/" + n) not in overrides and ext not in defaults:
                issues.append(f"content-type 누락: {n}")
        for n in names:
            if not n.endswith(".rels"):
                continue
            base = os.path.dirname(os.path.dirname(n))   # _rels 의 부모 디렉터리
            rx = z.read(n).decode("utf-8", "replace")
            # 이 .rels 가 기술하는 파트의 xml(r:id 사용처)
            described = os.path.join(base, os.path.basename(n)[:-5])  # .rels 제거
            ids = set(re.findall(r'Id="(rId\d+)"', rx))
            for rid, tgt in re.findall(r'Id="(rId\d+)"[^>]*?Target="([^"]+)"', rx):
                if tgt.startswith(("http://", "https://")) or 'TargetMode="External"' in rx:
                    continue
                resolved = os.path.normpath(os.path.join(base, tgt)).replace(os.sep, "/")
                if resolved not in names:
                    issues.append(f"rels 대상 없음: {n} -> {tgt}")
            if described.replace(os.sep, "/") in names:
                dx = z.read(described.replace(os.sep, "/")).decode("utf-8", "replace")
                for used in re.findall(r'r:(?:id|embed)="(rId\d+)"', dx):
                    if used not in ids:
                        issues.append(f"미정의 r:id: {described} 의 {used}")
    return issues


def _prune(tree, keep_ks):
    """출력에 표시되지 않는 원본 템플릿 슬라이드와 고아 미디어를 제거해 파일을 줄인다.

    keep_ks: 최종 sldIdLst 에 남길 슬라이드 번호. 마스터/레이아웃/테마는 보존하므로
    유효성에 영향 없음. 어떤 .rels 에서도 참조되지 않는 ppt/media 파일만 삭제한다.
    """
    slidedir = os.path.join(tree, "ppt", "slides")
    keep = {f"slide{k}.xml" for k in keep_ks}
    for n in list(os.listdir(slidedir)):
        if re.match(r'slide\d+\.xml$', n) and n not in keep:
            os.remove(os.path.join(slidedir, n))
            rel = os.path.join(slidedir, "_rels", n + ".rels")
            if os.path.exists(rel):
                os.remove(rel)
    # 발표자 노트(notesSlides)는 표준 출력에 불필요 + 제거된 원본 슬라이드를 참조해 dangling 유발
    # → 노트 파트 전부 삭제 + 남은 슬라이드 rels 의 notesSlide 관계 제거.
    notesdir = os.path.join(tree, "ppt", "notesSlides")
    if os.path.isdir(notesdir):
        shutil.rmtree(notesdir)
    relsdir = os.path.join(slidedir, "_rels")
    if os.path.isdir(relsdir):
        for rn in os.listdir(relsdir):
            rp = os.path.join(relsdir, rn)
            with open(rp, encoding="utf-8") as fh:
                rx = fh.read()
            rx2 = re.sub(r'<Relationship[^>]*notesSlide[^>]*/>', '', rx)
            if rx2 != rx:
                with open(rp, "w", encoding="utf-8") as fh:
                    fh.write(rx2)
    # [Content_Types].xml: 제거된 슬라이드 Override 삭제
    ctp = os.path.join(tree, "[Content_Types].xml")
    with open(ctp, encoding="utf-8") as fh:
        ct = fh.read()
    ct = re.sub(
        r'<Override PartName="/ppt/slides/slide(\d+)\.xml"[^>]*/>',
        lambda m: m.group(0) if int(m.group(1)) in set(keep_ks) else "", ct)
    ct = re.sub(r'<Override PartName="/ppt/notesSlides/[^"]*"[^>]*/>', '', ct)  # 노트 제거
    with open(ctp, "w", encoding="utf-8") as fh:
        fh.write(ct)
    # presentation.xml.rels: 제거된 슬라이드 관계 삭제
    prel = os.path.join(tree, "ppt", "_rels", "presentation.xml.rels")
    if os.path.exists(prel):
        with open(prel, encoding="utf-8") as fh:
            rx = fh.read()
        rx = re.sub(
            r'<Relationship[^>]*Target="slides/slide(\d+)\.xml"[^>]*/>',
            lambda m: m.group(0) if int(m.group(1)) in set(keep_ks) else "", rx)
        with open(prel, "w", encoding="utf-8") as fh:
            fh.write(rx)
    # 고아 미디어 제거: 남은 어떤 .rels 에서도 참조 안 되는 ppt/media 파일 삭제
    referenced = set()
    for root, _d, files in os.walk(tree):
        for f in files:
            if f.endswith(".rels"):
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    for m in re.finditer(r'Target="([^"]*?media/[^"]+)"', fh.read()):
                        referenced.add(os.path.basename(m.group(1)))
    media_dir = os.path.join(tree, "ppt", "media")
    if os.path.isdir(media_dir):
        for f in list(os.listdir(media_dir)):
            if f not in referenced:
                os.remove(os.path.join(media_dir, f))


def assemble(template_pptx, pages, out_pptx, cfg, draft_pptx=None):
    """표준 템플릿 기반 1:1 덱 조립.

    pages: [{src_slide, ops, source_slots, draft_slide_path?}].
      각 page → 표준 템플릿의 src_slide 를 복제 → ops 변환 → (초안 이미지 carry) → 새 slideK.
    draft_pptx: 초안 PPTX 경로(이미지 carry-over 소스). page.draft_slide_path 와 함께 사용.
    반환: out_pptx 경로.
    """
    workroot = os.path.dirname(os.path.abspath(out_pptx)) or "."
    tree = os.path.join(workroot, f"_assemble_{os.path.basename(out_pptx)}")
    if os.path.isdir(tree):
        shutil.rmtree(tree)
    pptx_io.unpack(template_pptx, tree)

    slidedir = os.path.join(tree, "ppt", "slides")
    existing = [int(re.match(r'slide(\d+)\.xml$', n).group(1))
                for n in os.listdir(slidedir) if re.match(r'slide(\d+)\.xml$', n)]
    m = max(existing) if existing else 0

    dz = zipfile.ZipFile(draft_pptx) if draft_pptx else None
    try:
        ks = []
        for i, pg in enumerate(pages):
            k = m + 1 + i
            src = pg["src_slide"]
            shutil.copyfile(os.path.join(tree, src), os.path.join(slidedir, f"slide{k}.xml"))
            src_rels = os.path.join(tree, _rels_path(src))
            if os.path.exists(src_rels):
                os.makedirs(os.path.join(slidedir, "_rels"), exist_ok=True)
                shutil.copyfile(src_rels, os.path.join(slidedir, "_rels", f"slide{k}.xml.rels"))
            slide_path = f"ppt/slides/slide{k}.xml"
            transform._apply_one(slide_path, pg.get("ops", []),
                                 pg.get("source_slots", {}), tree, cfg)
            if dz is not None and pg.get("draft_slide_path"):
                _carry_pics(tree, slide_path, pics_from(dz, pg["draft_slide_path"]), f"p{i}")
            _add_slide_ct(tree, k)
            ks.append(k)
        _repoint_presentation(tree, ks)
        _prune(tree, ks)
    finally:
        if dz is not None:
            dz.close()
    pptx_io.clean(tree)
    pptx_io.pack(tree, out_pptx)
    shutil.rmtree(tree, ignore_errors=True)
    return out_pptx, [f"ppt/slides/slide{k}.xml" for k in ks]
