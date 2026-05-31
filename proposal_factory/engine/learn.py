"""learn.py — 직원 수정 학습 루프의 핵심.
엔진 후보 ↔ 직원이 고친 최종본의 도형 기하를 diff 하여 '교정 기록'을 만든다.
반복되는 교정은 사람 1회 승인 후 design_guide/recipe 로 승격.
"""
THRESH = {"move_emu": 30000, "resize_emu": 30000}


def diff_shapes(cand, final):
    """이름으로 매칭하여 이동/크기/폰트/텍스트 변화를 추출. 반환 교정 목록."""
    fmap = {s["name"]: s for s in final if s["name"]}
    out = []
    for c in cand:
        f = fmap.get(c["name"])
        if not f:
            continue
        if abs(c["x"] - f["x"]) > THRESH["move_emu"] or abs(c["y"] - f["y"]) > THRESH["move_emu"]:
            out.append({"shape": c["name"], "kind": "moved",
                        "dx": f["x"] - c["x"], "dy": f["y"] - c["y"]})
        if abs(c["cx"] - f["cx"]) > THRESH["resize_emu"] or abs(c["cy"] - f["cy"]) > THRESH["resize_emu"]:
            out.append({"shape": c["name"], "kind": "resized",
                        "dcx": f["cx"] - c["cx"], "dcy": f["cy"] - c["cy"]})
        ct = c["texts"][0] if c["texts"] else None
        ft = f["texts"][0] if f["texts"] else None
        if ct and ft:
            if ct["sz"] != ft["sz"]:
                out.append({"shape": c["name"], "kind": "font_size",
                            "from": ct["sz"], "to": ft["sz"]})
            if ct["text"].strip() != ft["text"].strip():
                out.append({"shape": c["name"], "kind": "text_edit",
                            "from": ct["text"], "to": ft["text"]})
    return out


def to_corrections(diffs, job_id, page_type):
    """diff → correction_record(누적 저장용)."""
    return [{"job_id": job_id, "page_type": page_type, **d} for d in diffs]
