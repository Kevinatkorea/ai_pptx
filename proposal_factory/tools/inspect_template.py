"""inspect_template.py — 템플릿 PPTX 작성 도우미.

실제 .pptx 템플릿을 넣으면 슬라이드별로:
  - 도형 목록(tag·이름·slot 여부·bbox·텍스트/표/이미지)
  - 분류 시그니처(classify.signature)
  - 붙여넣기 가능한 page_types 엔트리(match) 제안
  - 슬롯↔op 매핑 recipe 골격 제안
  - source_slots 키 목록
을 출력한다. 표준 라이브러리 + engine 만 사용(외부 의존 없음).

사용:
    python3 tools/inspect_template.py <template.pptx> [--type TYPE] [--slide ppt/slides/slideN.xml]
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import classify, geometry  # noqa: E402

_STRUCT_KEYS = ("n_table", "n_image", "n_year_box")


def _slides(z):
    return sorted(n for n in z.namelist()
                  if n.startswith("ppt/slides/slide") and n.endswith(".xml"))


def _op_for(shape):
    """슬롯 도형 → 추천 op(휴리스틱)과 비고."""
    tag = shape.get("tag")
    if tag == "graphicFrame" and shape.get("table_h"):
        return "table_rebuild", "표(헤더 1행 + 본문 N행 재생성)"
    if tag == "pic":
        return "image_reuse", "기존 이미지 자리"
    if tag == "sp" and shape.get("texts"):
        return "text_inject", "텍스트 박스"
    if tag == "sp":
        return "image_reuse", "빈 박스 → image_reuse(이미지) 또는 shape_rebuild(그리드) 중 택1"
    return "text_inject", "확인 필요"


def _suggest_match(sig):
    """시그니처 → page_types match(존재=_min:1 / 부재=_max:0, 구조 키 한정)."""
    m = {}
    for k in _STRUCT_KEYS:
        if int(sig.get(k, 0) or 0) > 0:
            m[k + "_min"] = 1
        else:
            m[k + "_max"] = 0
    if sig.get("has_title"):
        m["has_title"] = True
    return m


def _text_preview(shape, n=24):
    parts = []
    for t in shape.get("texts") or []:
        parts.extend(t.get("paras") or ([t["text"]] if t.get("text") else []))
    s = " / ".join(p for p in parts if p)
    return (s[:n] + "…") if len(s) > n else s


def analyze_slide(slide_path, xml):
    shapes = geometry.extract_shapes(xml)
    sig = classify.signature(shapes)
    slots, unnamed = [], []
    for s in shapes:
        name = s.get("name", "")
        row = {"tag": s.get("tag"), "name": name,
               "bbox": (s.get("x"), s.get("y"), s.get("cx"), s.get("cy")),
               "preview": _text_preview(s)}
        if name.startswith("slot:"):
            op, note = _op_for(s)
            row["key"] = name[len("slot:"):]
            row["op"], row["note"] = op, note
            slots.append(row)
        else:
            unnamed.append(row)
    return shapes, sig, slots, unnamed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--type", default="body_new_type", help="페이지 유형 식별자")
    ap.add_argument("--slide", default=None, help="recipe 대상 슬라이드 XML 경로")
    args = ap.parse_args()

    with zipfile.ZipFile(args.pptx) as z:
        slide_names = _slides(z)
        if not slide_names:
            print("슬라이드 XML 이 없습니다(올바른 .pptx 아님)."); sys.exit(1)
        slides = {n: z.read(n).decode("utf-8", "replace") for n in slide_names}

    print(f"# 템플릿 분석: {args.pptx}")
    print(f"슬라이드 {len(slide_names)}장: {', '.join(slide_names)}\n")

    per_slide = {}
    for n in slide_names:
        shapes, sig, slots, unnamed = analyze_slide(n, slides[n])
        per_slide[n] = (sig, slots, unnamed)
        print(f"── {n} ──  (도형 {len(shapes)}개, slot {len(slots)}개)")
        print(f"   시그니처: {json.dumps(sig, ensure_ascii=False)}")
        for r in slots:
            x, y, cx, cy = r["bbox"]
            pv = f' \"{r["preview"]}\"' if r["preview"] else ""
            print(f"   ✓ slot:{r['key']:<18} [{r['tag']:<12}] → {r['op']:<13} ({r['note']}){pv}")
            print(f"       bbox(EMU) off=({x},{y}) ext=({cx},{cy})")
        for r in unnamed:
            pv = f' \"{r["preview"]}\"' if r["preview"] else ""
            print(f"   · (이름없음) [{r['tag']:<12}] {r['name']!r}{pv}  ← 변동 요소면 slot:<key> 명명 필요")
        print()

    # recipe 대상 슬라이드: 지정값 또는 slot 이 가장 많은 슬라이드
    target = args.slide or max(slide_names, key=lambda n: len(per_slide[n][1]))
    sig, slots, _ = per_slide[target]

    print("=" * 70)
    print(f"# 제안 — 대상 슬라이드: {target}\n")

    print("## page_types 엔트리 (assets/page_types.json 에 추가)")
    pt_entry = {"type": args.type,
                "desc": "(설명 작성)",
                "match": _suggest_match(sig),
                "recipe": f"recipes/{args.type}.json"}
    print(json.dumps(pt_entry, ensure_ascii=False, indent=2))
    print("   ※ match 는 구조 시그니처 기반 제안 — 다른 유형과 겹치면 카운트를 좁히세요.\n")

    print(f"## recipe 골격 (assets/recipes/{args.type}.json)")
    ops = [{"op": r["op"], "slot": r["key"], "from": r["key"]} for r in slots]
    recipe = {"type": args.type, "template_slide": target, "ops": ops}
    print(json.dumps(recipe, ensure_ascii=False, indent=2))
    if not slots:
        print("   ⚠ slot:<key> 도형이 없습니다. PowerPoint '선택 창'에서 변동 요소를 slot:<key> 로 명명하세요.")
    print()

    print("## source_slots 키 (실제 데이터로 채우기)")
    print("   " + (", ".join(r["key"] for r in slots) or "(없음)"))
    print("   table_rebuild→[{label,value}], shape_rebuild→[{year,text}], "
          "image_reuse→{path,x?,y?,cx?,cy?} 형태")


if __name__ == "__main__":
    main()
