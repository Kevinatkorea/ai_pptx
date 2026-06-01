"""geometry.py — PPTX 슬라이드 XML에서 도형 기하·텍스트를 추출한다.
린터가 '보지 않고 측정'할 수 있도록, 렌더 없이 좌표/줄수를 계산하는 토대.
표준 라이브러리만 사용(정규식 기반) — pptx 스킬의 편집 원칙과 동일.
"""
import re
import math

EMU_PER_PT = 12700  # 1pt = 12700 EMU
DEFAULT_SIZE = (6858000, 9906000)  # A4 portrait fallback


def slide_size(presentation_xml: str):
    m = re.search(r'sldSz cx="(\d+)" cy="(\d+)"', presentation_xml or "")
    return (int(m.group(1)), int(m.group(2))) if m else DEFAULT_SIZE


def _match_block(x: str, start: int, tag: str):
    """start 위치의 <p:tag> 와 균형 맞는 </p:tag> 까지의 블록 끝 인덱스."""
    depth = 0
    for mm in re.finditer(rf'</?p:{tag}>', x[start:]):
        if mm.group(0).startswith('</'):
            depth -= 1
            if depth == 0:
                return start + mm.end()
        else:
            depth += 1
    return len(x)


def _parse_shape_block(blk, tag):
    """도형 블록(sp/pic/graphicFrame) → 도형 dict(원본 좌표). off/ext 없으면 None."""
    off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"', blk)
    ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', blk)
    if not (off and ext):
        return None
    name = re.search(r'name="([^"]*)"', blk)
    shp = {
        'tag': tag,
        'name': name.group(1) if name else '',
        'x': int(off.group(1)), 'y': int(off.group(2)),
        'cx': int(ext.group(1)), 'cy': int(ext.group(2)),
        'texts': [],
    }
    if tag == 'sp' and '<a:t>' in blk:
        text = ''.join(re.findall(r'<a:t>([^<]*)</a:t>', blk))
        paras = []
        for p in re.findall(r'<a:p>(.*?)</a:p>', blk, re.S):
            pt = ''.join(re.findall(r'<a:t>([^<]*)</a:t>', p))
            if pt.strip():
                paras.append(pt)
        if not paras and text.strip():
            paras = [text]
        sz = re.search(r'\bsz="(\d+)"', blk)
        wrap = re.search(r'wrap="(\w+)"', blk)
        fonts = set(re.findall(r'typeface="([^"]+)"', blk))
        shp['texts'].append({
            'text': text,
            'paras': paras,
            'sz': int(sz.group(1)) if sz else 1800,   # 1/100 pt
            'wrap': wrap.group(1) if wrap else 'square',
            'autofit': ('spAutoFit' in blk or 'normAutofit' in blk),
            'fonts': fonts,
        })
    if tag == 'graphicFrame' and '<a:tbl>' in blk:
        rows = re.findall(r'<a:tr h="(\d+)"', blk)
        if rows:
            shp['table_h'] = sum(int(r) for r in rows)
        # 셀 텍스트(verbatim) — 행별 셀 문자열 목록(표 콘텐츠 자동 입력용)
        tbl = []
        for tr in re.findall(r'<a:tr\b.*?</a:tr>', blk, re.S):
            cells = []
            for tc in re.findall(r'<a:tc>.*?</a:tc>', tr, re.S):
                cells.append(''.join(re.findall(r'<a:t>([^<]*)</a:t>', tc)).strip())
            tbl.append(cells)
        if tbl:
            shp['table'] = tbl
    return shp


def extract_shapes(slide_xml: str):
    """spTree의 최상위 도형 목록을 반환(그룹은 그룹 자체 bbox 한 블록으로).
    각 항목: {tag,name,x,y,cx,cy,texts:[...]}. 좌표 EMU. 린터/시그니처용 — 동작 불변.
    그룹 내부까지 보려면 extract_shapes_deep 사용.
    """
    tree = re.search(r'<p:spTree>(.*)</p:spTree>', slide_xml, re.S)
    if not tree:
        return []
    x = tree.group(1)
    shapes = []
    i = 0
    while True:
        m = re.search(r'<p:(sp|pic|grpSp|graphicFrame)>', x[i:])
        if not m:
            break
        tag = m.group(1)
        st = i + m.start()
        en = _match_block(x, st, tag)
        blk = x[st:en]
        i = en
        shp = _parse_shape_block(blk, tag)
        if shp is not None:
            shapes.append(shp)
    return shapes


def _group_xfrm(blk):
    """grpSp 블록의 (off, ext, chOff, chExt) → 좌표 변환 파라미터. 없으면 None."""
    g = re.search(r'<p:grpSpPr>.*?<a:xfrm[^>]*>(.*?)</a:xfrm>', blk, re.S)
    if not g:
        return None
    s = g.group(1)
    off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"', s)
    ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', s)
    cho = re.search(r'<a:chOff x="(-?\d+)" y="(-?\d+)"', s)
    che = re.search(r'<a:chExt cx="(\d+)" cy="(\d+)"', s)
    if not (off and ext and cho and che):
        return None
    return (int(off.group(1)), int(off.group(2)), int(ext.group(1)), int(ext.group(2)),
            int(cho.group(1)), int(cho.group(2)), int(che.group(1)), int(che.group(2)))


def _group_fn(g, outer):
    """그룹 변환 함수(자식 좌표 → 절대 좌표). outer 변환과 합성."""
    ox, oy, ecx, ecy, cox, coy, chx, chy = g
    sx = ecx / chx if chx else 1.0
    sy = ecy / chy if chy else 1.0

    def fn(x, y, cx, cy):
        ax, ay = ox + (x - cox) * sx, oy + (y - coy) * sy
        acx, acy = cx * sx, cy * sy
        return outer(ax, ay, acx, acy) if outer else (ax, ay, acx, acy)
    return fn


def extract_shapes_deep(slide_xml=None, _content=None, _xf=None):
    """그룹 내부까지 **재귀** 추출 → 리프 도형(sp/pic/graphicFrame) 목록. 좌표는 절대(EMU).

    그룹(grpSp) 컨테이너는 내보내지 않고 그 안의 리프만 좌표 변환해 내보낸다(그룹 안 텍스트
    까지 포함) → 분류(content_profile)·콘텐츠 매핑에 사용. 린터용 extract_shapes 와 별개.
    """
    if _content is None:
        tree = re.search(r'<p:spTree>(.*)</p:spTree>', slide_xml or "", re.S)
        if not tree:
            return []
        _content = tree.group(1)
    out, i = [], 0
    while True:
        m = re.search(r'<p:(sp|pic|grpSp|graphicFrame)>', _content[i:])
        if not m:
            break
        tag = m.group(1)
        st = i + m.start()
        en = _match_block(_content, st, tag)
        blk = _content[st:en]
        i = en
        if tag == 'grpSp':
            g = _group_xfrm(blk)
            inner = blk[blk.find("</p:grpSpPr>") + len("</p:grpSpPr>"):] if "</p:grpSpPr>" in blk else ""
            fn = _group_fn(g, _xf) if g else _xf
            out.extend(extract_shapes_deep(_content=inner, _xf=fn))
            continue
        shp = _parse_shape_block(blk, tag)
        if shp is None:
            continue
        if _xf:
            ax, ay, acx, acy = _xf(shp['x'], shp['y'], shp['cx'], shp['cy'])
            shp['x'], shp['y'] = int(ax), int(ay)
            shp['cx'], shp['cy'] = int(acx), int(acy)
        out.append(shp)
    return out


def source_slots_from_shapes(shapes):
    """`slot:<key>` 로 명명된 텍스트 도형에서 결정론적으로 source_slots(텍스트) 추출.

    텍스트 도형만 대상으로 한다(표·이미지는 사이드카 또는 LLM 이 필요).
    도형 내 여러 문단은 줄바꿈으로 결합 → text_inject 가 다시 문단으로 분리.
    값이 비어있는 슬롯은 건너뛴다. 반환: {key: text, ...}.
    """
    out = {}
    for s in shapes:
        name = s.get("name", "")
        if not name.startswith("slot:"):
            continue
        key = name[len("slot:"):]
        paras = []
        for t in s.get("texts") or []:
            p = t.get("paras")
            if p:
                paras.extend(p)
            elif t.get("text"):
                paras.append(t["text"])
        text = "\n".join(x for x in paras if x is not None).strip()
        if text:
            out[key] = text
    return out


def estimate_lines(text: str, box_cx_emu: int, sz_centi_pt: int, cpl_factor: float):
    """줄바꿈 예상 줄 수. box 폭과 폰트로 줄당 글자수(CPL)를 추정."""
    if not text.strip() or box_cx_emu <= 0:
        return 0
    pt = sz_centi_pt / 100.0
    char_w = pt * EMU_PER_PT * cpl_factor
    if char_w <= 0:
        return 1
    cpl = max(1.0, box_cx_emu / char_w)
    return max(1, math.ceil(len(text) / cpl))
