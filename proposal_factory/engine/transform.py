"""transform.py — 레시피 기반 결정론적 PPTX 변환(4대 연산).

(1) text_inject   : 슬롯 텍스트 박스에 문자열 주입(폰트 속성 보존, one_line 룰 지원)
(2) table_rebuild : 슬롯 표의 헤더 보존 + 본문 행 재생성
(3) image_reuse   : 원본 이미지 → ppt/media 복사 + rels 갱신 + 슬롯 자리에 <p:pic>
(4) shape_rebuild : 슬롯의 기준 박스를 베이스로 균일 간격 그리드 도형 재구성

슬롯 식별 규약: 도형 name 속성이 'slot:<key>' 형태(예: 'slot:breadcrumb').
편집은 슬라이드 XML 문자열을 in-memory 로 순차 수정 → 마지막에 한 번 저장.
표준 라이브러리만 사용(geometry.py 와 동일 원칙).
"""
import os
import re
import shutil

from . import pptx_io


class MissingSlot(Exception):
    """레시피에서 참조한 slot 도형이 슬라이드에 없을 때."""


# ---------- 슬롯 탐색 ----------

_SHAPE_TAGS = ("sp", "pic", "graphicFrame", "grpSp")


def _match_block(x: str, start: int, tag: str) -> int:
    depth = 0
    for mm in re.finditer(rf'</?p:{tag}\b[^>]*>', x[start:]):
        if mm.group(0).startswith('</'):
            depth -= 1
            if depth == 0:
                return start + mm.end()
        else:
            depth += 1
    return len(x)


def _find_slot(xml: str, slot_key: str):
    """xml 안에서 name='slot:<slot_key>' 를 가진 가장 안쪽 도형 블록을 찾는다.
    반환: (start, end, block_text, tag)
    """
    needle = f'name="slot:{slot_key}"'
    pos = xml.find(needle)
    if pos < 0:
        raise MissingSlot(f"slot:{slot_key} not found in slide")
    best = None  # (start, end, tag)
    for tag in _SHAPE_TAGS:
        for m in re.finditer(rf'<p:{tag}\b[^>]*>', xml):
            st = m.start()
            if st > pos:
                break
            en = _match_block(xml, st, tag)
            if st < pos < en and (best is None or st > best[0]):
                best = (st, en, tag)
    if best is None:
        raise MissingSlot(f"slot:{slot_key} found but no enclosing shape")
    st, en, tag = best
    return st, en, xml[st:en], tag


# ---------- 유틸 ----------

def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _get_field(d, path):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def _load(template_dir: str, rel: str) -> str:
    with open(os.path.join(template_dir, rel), encoding="utf-8") as fh:
        return fh.read()


def _save(template_dir: str, rel: str, content: str) -> None:
    path = os.path.join(template_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _slide_index(slide_path: str) -> int:
    m = re.search(r'slide(\d+)\.xml', slide_path)
    return int(m.group(1)) if m else 1


def _max_rid(rels_xml: str) -> int:
    ids = re.findall(r'Id="rId(\d+)"', rels_xml or "")
    return max((int(i) for i in ids), default=0)


def _max_shape_id(slide_xml: str) -> int:
    ids = re.findall(r'<p:cNvPr id="(\d+)"', slide_xml or "")
    return max((int(i) for i in ids), default=1)


def _bbox(block: str):
    off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"', block)
    ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', block)
    return (
        int(off.group(1)) if off else 0,
        int(off.group(2)) if off else 0,
        int(ext.group(1)) if ext else 1000000,
        int(ext.group(2)) if ext else 1000000,
    )


# ---------- (1) text_inject ----------

def _extract_rpr(inner: str) -> str:
    m = re.search(r'<a:rPr\b[^>]*?/>', inner)
    if m:
        return m.group(0)
    m = re.search(r'<a:rPr\b[^>]*>.*?</a:rPr>', inner, re.S)
    if m:
        return m.group(0)
    return '<a:rPr lang="ko-KR"/>'


def _extract_ppr(inner: str) -> str:
    m = re.search(r'<a:pPr\b[^>]*?/>', inner)
    if m:
        return m.group(0)
    m = re.search(r'<a:pPr\b[^>]*>.*?</a:pPr>', inner, re.S)
    return m.group(0) if m else ""


def _replace_text(block: str, value: str) -> str:
    tx = re.search(r'<p:txBody>(.*?)</p:txBody>', block, re.S)
    if not tx:
        return block
    inner = tx.group(1)
    rpr = _extract_rpr(inner)
    ppr = _extract_ppr(inner)
    prefix_m = re.search(r'^(.*?)(?=<a:p\b)', inner, re.S)
    prefix = prefix_m.group(1) if prefix_m else ""
    paras = []
    for line in (value or "").split("\n"):
        t = _xml_escape(line)
        paras.append(f'<a:p>{ppr}<a:r>{rpr}<a:t>{t}</a:t></a:r></a:p>')
    new_inner = prefix + "".join(paras)
    return block[:tx.start(1)] + new_inner + block[tx.end(1):]


def _op_text_inject(op, src, state):
    value = _get_field(src, op["from"])
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value)
    value = "" if value is None else str(value)
    if op.get("rule") == "one_line":
        value = " ".join(value.split())
    xml = state["slide_xml"]
    st, en, block, _tag = _find_slot(xml, op["slot"])
    new_block = _replace_text(block, value)
    state["slide_xml"] = xml[:st] + new_block + xml[en:]


# ---------- (1b) group_fill — 그룹 내부 텍스트박스 채우기 ----------

def _fill_inner_texts(group_block, values):
    """그룹 블록 내부의 <p:sp> 텍스트박스들을 values(verbatim, 문서 순서)로 채운다.
    값 개수보다 많은 텍스트박스는 원본 유지. 그룹의 비텍스트 도형/구조는 보존."""
    out, i, vi = [], 0, 0
    while True:
        m = re.search(r'<p:sp\b', group_block[i:])
        if not m:
            out.append(group_block[i:])
            break
        st = i + m.start()
        en = _match_block(group_block, st, "sp")
        out.append(group_block[i:st])
        sp_block = group_block[st:en]
        if "<p:txBody>" in sp_block and vi < len(values):
            sp_block = _replace_text(sp_block, values[vi])
            vi += 1
        out.append(sp_block)
        i = en
    return "".join(out)


def _op_group_fill(op, src, state):
    value = _get_field(src, op["from"])
    if value is None:
        values = []
    elif isinstance(value, list):
        values = [str(v) for v in value]
    else:
        values = [str(value)]
    if op.get("rule") == "one_line":
        values = [" ".join(v.split()) for v in values]
    xml = state["slide_xml"]
    st, en, block, _tag = _find_slot(xml, op["slot"])
    new_block = _fill_inner_texts(block, values)
    state["slide_xml"] = xml[:st] + new_block + xml[en:]


# ---------- (2) table_rebuild ----------

def _make_cell(text: str, fill: str = None, sz: int = 900) -> str:
    fill_xml = (f'<a:tcPr><a:solidFill><a:srgbClr val="{fill}"/></a:solidFill></a:tcPr>'
                if fill else '<a:tcPr/>')
    return ('<a:tc>'
            '<a:txBody><a:bodyPr/><a:lstStyle/>'
            f'<a:p><a:r><a:rPr lang="ko-KR" sz="{sz}"/>'
            f'<a:t>{_xml_escape(text)}</a:t></a:r></a:p>'
            '</a:txBody>'
            f'{fill_xml}'
            '</a:tc>')


def _make_table_row(h: int, label: str, value: str,
                    label_fill: str, value_fill: str) -> str:
    return (f'<a:tr h="{h}">'
            f'{_make_cell(label, label_fill)}'
            f'{_make_cell(value, value_fill)}'
            '</a:tr>')


def _op_table_rebuild(op, src, state):
    rows = _get_field(src, op["from"])
    if rows is None:
        return  # 표 데이터 없음 → 건너뜀(템플릿 표 보존)
    style = op.get("style", {}) or {}
    label_fill = style.get("label_col_fill", "F2F2F2")
    hi_fill = style.get("highlight_fill", "DCEFFE")

    xml = state["slide_xml"]
    st, en, block, _tag = _find_slot(xml, op["slot"])
    tbl = re.search(r'<a:tbl>(.*?)</a:tbl>', block, re.S)
    if not tbl:
        raise ValueError(f"slot:{op['slot']} has no <a:tbl>")
    tbl_inner = tbl.group(1)
    prefix_m = re.search(r'^(.*?)(?=<a:tr\b)', tbl_inner, re.S)
    prefix = prefix_m.group(1) if prefix_m else ""
    rows_text = re.findall(r'<a:tr\b[^>]*>.*?</a:tr>', tbl_inner, re.S)
    if not rows_text:
        raise ValueError(f"slot:{op['slot']} table has no rows")
    header = rows_text[0]
    h_m = re.search(r'<a:tr\b[^>]*h="(\d+)"', header)
    row_h = int(h_m.group(1)) if h_m else 380000

    new_rows = [header]
    for r in rows:
        new_rows.append(_make_table_row(
            row_h, r["label"], r["value"],
            label_fill, hi_fill if r.get("highlight") else None))
    new_tbl_inner = prefix + "".join(new_rows)
    new_block = block[:tbl.start(1)] + new_tbl_inner + block[tbl.end(1):]
    state["slide_xml"] = xml[:st] + new_block + xml[en:]


# ---------- (3) image_reuse ----------

_CT_BY_EXT = {"png": "image/png", "jpg": "image/jpeg",
              "jpeg": "image/jpeg", "gif": "image/gif"}


def _ensure_content_type(template_dir: str, ext_no_dot: str) -> None:
    ct_path = os.path.join(template_dir, "[Content_Types].xml")
    if not os.path.exists(ct_path):
        return
    with open(ct_path, encoding="utf-8") as fh:
        xml = fh.read()
    if f'Extension="{ext_no_dot}"' in xml:
        return
    ct = _CT_BY_EXT.get(ext_no_dot, "application/octet-stream")
    ins = f'<Default Extension="{ext_no_dot}" ContentType="{ct}"/>'
    xml = xml.replace("</Types>", ins + "</Types>")
    with open(ct_path, "w", encoding="utf-8") as fh:
        fh.write(xml)


def _copy_media(template_dir: str, src_path: str) -> str:
    media_dir = os.path.join(template_dir, "ppt", "media")
    os.makedirs(media_dir, exist_ok=True)
    base = os.path.basename(src_path)
    stem, ext = os.path.splitext(base)
    target = os.path.join(media_dir, base)
    n = 1
    while os.path.exists(target):
        target = os.path.join(media_dir, f"{stem}_{n}{ext}")
        n += 1
    shutil.copyfile(src_path, target)
    return os.path.basename(target)


def _make_pic(sid: int, name: str, rid: str, x: int, y: int, cx: int, cy: int) -> str:
    return (
        '<p:pic>'
        '<p:nvPicPr>'
        f'<p:cNvPr id="{sid}" name="{name}"/>'
        '<p:cNvPicPr/>'
        '<p:nvPr/>'
        '</p:nvPicPr>'
        '<p:blipFill>'
        f'<a:blip r:embed="{rid}"/>'
        '<a:stretch><a:fillRect/></a:stretch>'
        '</p:blipFill>'
        '<p:spPr>'
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '</p:spPr>'
        '</p:pic>'
    )


def _op_image_reuse(op, src, state):
    img = _get_field(src, op["from"])
    if not img or "path" not in img:
        return  # 이미지 데이터 없음 → 건너뜀(템플릿 자리 보존; 초안 이미지는 carry-over 가 담당)
    src_path = img["path"]
    if not os.path.isfile(src_path):
        return  # 파일 없음 → 건너뜀

    fname = _copy_media(state["template_dir"], src_path)
    ext = os.path.splitext(fname)[1].lstrip(".").lower()
    _ensure_content_type(state["template_dir"], ext)

    rid = f"rId{state['next_rid']}"
    state["next_rid"] += 1
    new_rel = (f'<Relationship Id="{rid}" '
               f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
               f'Target="../media/{fname}"/>')
    state["rels_xml"] = state["rels_xml"].replace("</Relationships>", new_rel + "</Relationships>")

    xml = state["slide_xml"]
    st, en, block, _tag = _find_slot(xml, op["slot"])
    bx, by, bcx, bcy = _bbox(block)
    x = img.get("x") if img.get("x") is not None else bx
    y = img.get("y") if img.get("y") is not None else by
    cx = img.get("cx") if img.get("cx") is not None else bcx
    cy = img.get("cy") if img.get("cy") is not None else bcy

    sid = state["next_id"]
    state["next_id"] += 1
    pic_xml = _make_pic(sid, f"slot:{op['slot']}", rid, x, y, cx, cy)
    state["slide_xml"] = xml[:st] + pic_xml + xml[en:]


# ---------- (4) shape_rebuild ----------

def _make_text_rect(sid: int, name: str, x: int, y: int, cx: int, cy: int,
                    text: str, fill: str = None, font_color: str = "333333",
                    sz: int = 700, wrap: str = "square", bold: bool = False) -> str:
    fill_xml = (f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>'
                if fill else '<a:noFill/>')
    bold_attr = ' b="1"' if bold else ''
    return (
        '<p:sp>'
        '<p:nvSpPr>'
        f'<p:cNvPr id="{sid}" name="{name}"/>'
        '<p:cNvSpPr txBox="1"/>'
        '<p:nvPr/>'
        '</p:nvSpPr>'
        '<p:spPr>'
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'{fill_xml}'
        '</p:spPr>'
        '<p:txBody>'
        f'<a:bodyPr wrap="{wrap}" anchor="ctr"/>'
        '<a:lstStyle/>'
        '<a:p>'
        '<a:r>'
        f'<a:rPr lang="ko-KR" sz="{sz}"{bold_attr}>'
        f'<a:solidFill><a:srgbClr val="{font_color}"/></a:solidFill>'
        '</a:rPr>'
        f'<a:t>{_xml_escape(text)}</a:t>'
        '</a:r>'
        '</a:p>'
        '</p:txBody>'
        '</p:sp>'
    )


def _op_shape_rebuild(op, src, state):
    items = _get_field(src, op["from"]) or []
    d = op.get("design", {}) or {}
    box_fill = d.get("year_box_fill", "2B8ECB")
    text_color = d.get("year_text_color", "FFFFFF")
    gap = int(d.get("uniform_gap_emu", 46000))
    columns = max(1, int(d.get("columns", 1)))

    xml = state["slide_xml"]
    st, en, block, _tag = _find_slot(xml, op["slot"])
    x0, y0, cx0, cy0 = _bbox(block)

    year_w, year_h = 440000, 200000
    row_h = 300000
    col_w = cx0 // columns
    bullet_off = year_w + 60000
    bullet_w = max(200000, col_w - bullet_off - 60000)

    rows_per_col = max(1, (len(items) + columns - 1) // columns)
    out = []
    for i, item in enumerate(items):
        col = i // rows_per_col
        row = i % rows_per_col
        x = x0 + col * col_w
        y = y0 + row * (max(year_h, row_h) + gap)
        sid_y = state["next_id"]; state["next_id"] += 1
        sid_b = state["next_id"]; state["next_id"] += 1
        out.append(_make_text_rect(
            sid_y, f"year_box_{i}", x, y, year_w, year_h,
            str(item["year"]), fill=box_fill, font_color=text_color,
            sz=1000, wrap="none", bold=True))
        out.append(_make_text_rect(
            sid_b, f"bullet_{i}", x + bullet_off, y, bullet_w, row_h,
            str(item["text"]), fill=None, font_color="333333",
            sz=700, wrap="square", bold=False))
    state["slide_xml"] = xml[:st] + "".join(out) + xml[en:]


# ---------- 디스패처 ----------

_OPS = {
    "text_inject": _op_text_inject,
    "group_fill": _op_group_fill,
    "table_rebuild": _op_table_rebuild,
    "image_reuse": _op_image_reuse,
    "shape_rebuild": _op_shape_rebuild,
}


def _apply_one(slide_path: str, ops: list, source_slots: dict,
               template_dir: str, cfg: dict) -> None:
    """한 슬라이드(slide_path)에 ops 를 순서대로 적용하고 트리에 저장(in-place)."""
    slide_path = slide_path or "ppt/slides/slide1.xml"
    idx = _slide_index(slide_path)
    rels_path = f"ppt/slides/_rels/slide{idx}.xml.rels"

    slide_xml = _load(template_dir, slide_path)
    rels_xml = _load(template_dir, rels_path) if os.path.exists(
        os.path.join(template_dir, rels_path)) else (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '</Relationships>')

    state = {
        "template_dir": template_dir,
        "slide_path": slide_path,
        "rels_path": rels_path,
        "slide_xml": slide_xml,
        "rels_xml": rels_xml,
        "next_rid": _max_rid(rels_xml) + 1,
        "next_id": _max_shape_id(slide_xml) + 100,
        "cfg": cfg,
    }

    for op in ops or []:
        kind = op.get("op")
        fn = _OPS.get(kind)
        if not fn:
            raise ValueError(f"unknown op: {kind}")
        fn(op, source_slots, state)

    _save(template_dir, slide_path, state["slide_xml"])
    _save(template_dir, rels_path, state["rels_xml"])


def apply(recipe: dict, source_slots: dict, template_dir: str,
          out_pptx: str, cfg: dict) -> str:
    """recipe 를 적용 → out_pptx 생성. 반환: out_pptx 경로.

    단일 슬라이드: recipe = {template_slide, ops:[...]}.
    다중 슬라이드: recipe = {template_slides:[{template_slide, ops}, ...]}.
    각 슬라이드는 자신의 rels 로 독립 처리되고, 마지막에 한 번 pack 한다.
    template_dir 는 이미 unpack 된 PPTX 트리.
    """
    specs = recipe.get("template_slides")
    if specs:
        for spec in specs:
            _apply_one(spec.get("template_slide"), spec.get("ops", []),
                       source_slots, template_dir, cfg)
    else:
        _apply_one(recipe.get("template_slide"), recipe.get("ops", []),
                   source_slots, template_dir, cfg)

    pptx_io.clean(template_dir)
    pptx_io.pack(template_dir, out_pptx)
    return out_pptx
