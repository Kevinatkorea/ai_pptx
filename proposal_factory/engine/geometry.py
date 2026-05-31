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


def extract_shapes(slide_xml: str):
    """spTree의 최상위 도형 목록을 반환.
    각 항목: {tag,name,x,y,cx,cy,texts:[{text,sz,wrap,autofit,fonts}]}
    좌표는 EMU. 그룹은 그룹 자체 bbox를 사용(겹침/이탈 검사 충분).
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
        off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"', blk)
        ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', blk)
        if not (off and ext):
            continue
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
        # 표: 실제 높이 = 행 높이 합 (프레임 ext.cy는 과대평가일 수 있음)
        if tag == 'graphicFrame' and '<a:tbl>' in blk:
            rows = re.findall(r'<a:tr h="(\d+)"', blk)
            if rows:
                shp['table_h'] = sum(int(r) for r in rows)
        shapes.append(shp)
    return shapes


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
