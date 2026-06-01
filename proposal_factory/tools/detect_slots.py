"""detect_slots.py — 슬롯 명명 누락 탐지.

표준 템플릿(.pptx)에서 **채워야 할 변동 텍스트인데 `slot:<key>` 로 명명되지 않은** 도형을
찾아 "이 텍스트 → slot:무엇" 후보를 제시한다. PowerPoint '선택 창'에서 이름만 부여하면 됨.

구분:
  - 이미 슬롯(slot:<key>)        → 건너뜀
  - 고정 디자인(여러 슬라이드 반복: 로고·푸터·챕터 라벨 등) → 건너뜀
  - 플레이스홀더("…입력하세요"/"제목"/"내용" 등) 또는 실질 콘텐츠 → **명명 후보**

그룹 내부 텍스트까지 본다(geometry.extract_shapes_deep). 표준 라이브러리 + engine 만 사용.
사용: python3 tools/detect_slots.py <template.pptx> [--all]
"""
import argparse
import os
import re
import sys
import zipfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import geometry  # noqa: E402

_PLACEHOLDER = ("입력하세요", "입력해주세요", "입력 하세요", "제목을", "내용을",
                "텍스트를", "메인 타이틀", "내용 입력", "제목 입력")
_KEY_HINT = [("제목", "title"), ("타이틀", "title"), ("title", "title"),
             ("내용", "body"), ("본문", "body"), ("요약", "summary"),
             ("리드", "lead"), ("머리", "head")]


def _norm(t):
    return re.sub(r"\s+", " ", t).strip()


def _shape_texts(shapes):
    out = []
    for s in shapes:
        if not s.get("texts"):
            continue
        parts = []
        for tx in s["texts"]:
            parts.extend(tx.get("paras") or ([tx["text"]] if tx.get("text") else []))
        t = _norm(" ".join(p for p in parts if p))
        if t:
            out.append((s, t))
    return out


def _suggest_key(text, n):
    low = text.lower()
    for kw, key in _KEY_HINT:
        if kw in low:
            return key
    return f"text{n}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--all", action="store_true", help="후보 없는 슬라이드도 표시")
    args = ap.parse_args()

    with zipfile.ZipFile(args.pptx) as z:
        slides = sorted((n for n in z.namelist()
                         if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.split("/")[-1]).group(1)))
        xmls = {n: z.read(n).decode("utf-8", "replace") for n in slides}

    # 1) 텍스트 빈도(여러 슬라이드 반복 = 고정 디자인)
    freq = Counter()
    per_slide = {}
    for n in slides:
        shapes = geometry.extract_shapes_deep(xmls[n])
        texts = _shape_texts(shapes)
        per_slide[n] = texts
        for _s, t in texts:
            freq[t] += 1
    boiler_cut = max(3, int(len(slides) * 0.25))

    total_cand = 0
    for n in slides:
        slotted = sum(1 for s, _ in per_slide[n] if s.get("name", "").startswith("slot:"))
        cands = []
        ci = 1
        for s, t in per_slide[n]:
            name = s.get("name", "")
            if name.startswith("slot:"):
                continue
            is_ph = any(p in t for p in _PLACEHOLDER)
            if not args.all and not is_ph:
                continue                      # 기본: 플레이스홀더만(--all 로 콘텐츠도)
            if not is_ph and freq[t] >= boiler_cut:
                continue                      # 반복 = 고정 디자인
            if not is_ph and len(t) <= 2:
                continue                      # 짧은 고정 라벨/번호
            cands.append((name, t, _suggest_key(t, ci), is_ph))
            ci += 1
        total_cand += len(cands)
        if cands or args.all:
            sn = n.split("/")[-1]
            print(f"── {sn}  (이미 슬롯 {slotted}개 / 명명 후보 {len(cands)}개)")
            for name, t, key, is_ph in cands:
                tag = "★플레이스홀더" if is_ph else "콘텐츠"
                print(f"   [{tag}] '{t[:38]}'")
                print(f"        도형이름={name!r} → slot:{key} 추천")

    mode = "플레이스홀더+콘텐츠" if args.all else "플레이스홀더"
    print(f"\n=== 요약({mode}): {len(slides)}슬라이드, 명명 후보 총 {total_cand}개 ===")
    print("PowerPoint 선택 창에서 위 도형에 slot:<key> 이름을 부여하세요(중복 키 금지).")
    if not args.all:
        print("(일반 콘텐츠 텍스트까지 보려면 --all)")


if __name__ == "__main__":
    main()
