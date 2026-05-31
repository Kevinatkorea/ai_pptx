"""linter.py — 레이아웃 린터 v1 (잉크 박스 기반).
'검증 직전 캡처'에서 뽑은 도형 기하를 받아 5종 결함을 측정한다.
핵심: 선언된 bbox가 아니라 '실제 내용이 차지하는 잉크 박스'로 겹침/간격을 판단해
짧은 텍스트가 든 큰 박스끼리의 거짓 겹침을 제거한다.
반환: findings = [(severity, code, where, message), ...]
"""
from statistics import pstdev, mean

from .geometry import EMU_PER_PT, estimate_lines


def effective_box(s, cfg):
    """선언 bbox 대신 실제 내용이 차지하는 잉크 박스.
    표(graphicFrame): 행 높이 합 / 텍스트 박스: 문단별 예상 줄수로 축소
    (빈 박스 has_ink=False) / 그 외(이미지·도형): 선언 bbox."""
    L = cfg['linter']
    pad = L['text_pad_emu']
    x, y, cx, cy = s['x'], s['y'], s['cx'], s['cy']
    has_ink = True
    if s.get('table_h'):
        cy = min(cy, s['table_h'])
    elif s['tag'] == 'sp' and s['texts']:
        t = s['texts'][0]
        paras = t.get('paras') or ([t['text']] if t['text'].strip() else [])
        if not paras:
            has_ink = False
        elif not t['autofit']:
            pt = t['sz'] / 100.0
            lineh = pt * EMU_PER_PT * L['line_factor']
            char_w = pt * EMU_PER_PT * L['cpl_factor']
            maxchars = max((len(p) for p in paras), default=0)
            if t['wrap'] == 'none':          # 줄바꿈 없음: 글자 길이만큼만 차지
                total_lines = len(paras)
            else:
                total_lines = sum(estimate_lines(p, cx, t['sz'], L['cpl_factor']) for p in paras)
            cy = min(cy, total_lines * lineh + 2 * pad)
            cx = min(cx, maxchars * char_w + 2 * pad)
    return {'name': s['name'], 'tag': s['tag'], 'x': x, 'y': y,
            'cx': cx, 'cy': cy, 'has_ink': has_ink}


def _overlap_area(a, b):
    ix = max(0, min(a['x'] + a['cx'], b['x'] + b['cx']) - max(a['x'], b['x']))
    iy = max(0, min(a['y'] + a['cy'], b['y'] + b['cy']) - max(a['y'], b['y']))
    return ix * iy


def lint(shapes, slide_cx, slide_cy, cfg):
    f = []
    L = cfg['linter']
    eff = [effective_box(s, cfg) for s in shapes]

    # 1) 화면 밖 이탈 (선언 bbox 기준)
    tol = L['offslide_tol_emu']
    for s in shapes:
        if (s['x'] < -tol or s['y'] < -tol or
                s['x'] + s['cx'] > slide_cx + tol or
                s['y'] + s['cy'] > slide_cy + tol):
            f.append(('fail', 'off_slide', s['name'] or s['tag'],
                      '요소가 슬라이드 경계를 벗어남'))

    # 2) 겹침 (잉크 박스 + allowlist + 최소 면적)
    allow = L['overlap_allow_keywords']
    minor = L['overlap_minor_area_emu2']
    for i in range(len(eff)):
        for j in range(i + 1, len(eff)):
            a, b = eff[i], eff[j]
            if not (a['has_ink'] and b['has_ink']):
                continue
            if any(k in a['name'] for k in allow) or any(k in b['name'] for k in allow):
                continue
            if _overlap_area(a, b) > minor:
                f.append(('fail', 'overlap',
                          f"{a['name'] or a['tag']} ↔ {b['name'] or b['tag']}",
                          '도형이 서로 포개짐'))

    # 3) 줄넘침
    cplf, linef, overrun = L['cpl_factor'], L['line_factor'], L['overflow_ratio']
    for s in shapes:
        for t in s['texts']:
            paras = t.get('paras') or ([t['text']] if t['text'].strip() else [])
            if not paras or t['autofit'] or t['wrap'] == 'none':
                continue
            lines = sum(estimate_lines(p, s['cx'], t['sz'], cplf) for p in paras)
            text_h = lines * (t['sz'] / 100.0) * EMU_PER_PT * linef
            if text_h > s['cy'] * overrun:
                f.append(('fail', 'text_overflow', s['name'] or s['tag'],
                          f'예상 {lines}줄 높이가 박스를 초과(줄넘침)'))

    # 4) 간격 (잉크 박스 기준)
    f += _check_spacing(eff, slide_cx, L)

    # 5) 미렌더 폰트(공체 등) → 경고
    bad = set(L['nonrender_fonts'])
    for s in shapes:
        for t in s['texts']:
            if t['fonts'] & bad:
                f.append(('warn', 'nonrender_font', s['name'] or s['tag'],
                          f"미렌더 폰트: {', '.join(sorted(t['fonts'] & bad))}"))
    return f


def _check_spacing(eff, slide_cx, L):
    out = []
    items = [s for s in eff if s['has_ink'] and s['y'] >= L['body_top_emu']
             and s['cy'] < L['spacing_item_max_h']]
    if len(items) < 3:
        return out
    mid = slide_cx / 2
    for tag, col in (('L', [s for s in items if s['x'] + s['cx'] / 2 < mid]),
                     ('R', [s for s in items if s['x'] + s['cx'] / 2 >= mid])):
        col = sorted(col, key=lambda s: s['y'])
        gaps = [col[k + 1]['y'] - (col[k]['y'] + col[k]['cy']) for k in range(len(col) - 1)]
        gaps = [g for g in gaps if g >= 0]
        if len(gaps) < 2:
            continue
        if min(gaps) < L['min_gap_emu']:
            out.append(('warn', 'tight_gap', f'{tag}컬럼', '콘텐츠 그룹 간격이 너무 좁음'))
        mu = mean(gaps)
        if mu > 0 and pstdev(gaps) / mu > L['gap_cov_threshold']:
            out.append(('warn', 'uneven_gap', f'{tag}컬럼', '콘텐츠 그룹 간격이 불균일'))
    return out


def report(findings):
    fails = [x for x in findings if x[0] == 'fail']
    warns = [x for x in findings if x[0] == 'warn']
    lines = [f"  [{s.upper()}] {c} @ {w} — {m}" for s, c, w, m in findings]
    verdict = 'FAIL' if fails else ('PASS(경고 있음)' if warns else 'PASS')
    return verdict, len(fails), len(warns), "\n".join(lines) if lines else "  (결함 없음)"
