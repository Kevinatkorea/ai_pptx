"""classify.py — 페이지 유형 분류.
1) 결정론(구조 시그니처 매칭, 무료)  2) 로컬 LLM  3) Claude(신규/모호).
"""


def signature(shapes):
    """페이지 구조 특징(결정론 분류·레시피 매칭용)."""
    sig = {"n_table": 0, "n_image": 0, "n_text": 0, "n_year_box": 0, "has_title": False}
    for s in shapes:
        if s["tag"] == "graphicFrame" and s.get("table_h"):
            sig["n_table"] += 1
        elif s["tag"] == "pic":
            sig["n_image"] += 1
        elif s["tag"] == "sp" and s["texts"]:
            sig["n_text"] += 1
            nm = s["name"]
            if "연도" in nm or "year" in nm.lower():
                sig["n_year_box"] += 1
            if "title" in nm.lower() or "제목" in nm:
                sig["has_title"] = True
    return sig


def content_profile(shapes, max_texts=14, snip=80):
    """내용 기반 분류용 프로필 — 시그니처보다 풍부(텍스트 스니펫 + 레이아웃 + 도형 구성).

    LLM 이 grouped 텍스트 타입(asis_tobe / body_head / list ...)을 구분할 수 있도록
    실제 텍스트 일부와 위치/크기를 담는다. 토큰 절약 위해 개수·길이 제한.
    (geometry 는 최상위 도형만 추출 → 그룹 내부 텍스트는 일부 누락될 수 있음.)
    """
    prof = {"signature": signature(shapes), "n_shapes": len(shapes), "tags": {}, "texts": []}
    for s in shapes:
        prof["tags"][s.get("tag")] = prof["tags"].get(s.get("tag"), 0) + 1
    for s in shapes:
        if len(prof["texts"]) >= max_texts:
            break
        if s.get("texts"):
            parts = []
            for tx in s["texts"]:
                parts.extend(tx.get("paras") or ([tx["text"]] if tx.get("text") else []))
            t = " ".join(p for p in parts if p).strip()
            if t:
                prof["texts"].append({"t": t[:snip], "x": s.get("x"), "y": s.get("y"),
                                      "cx": s.get("cx"), "cy": s.get("cy")})
    return prof


def classify(shapes, library, gateway=None):
    """library: [{type, match:{...predicates...}}]. 반환 (type, confidence, source)."""
    sig = signature(shapes)
    for entry in library:
        if _match(sig, entry.get("match", {})):
            return entry["type"], 1.0, "deterministic"
    if gateway is not None:
        try:
            res = gateway.local_classify(sig)
            if res:
                return res, 0.7, "local_llm"
        except NotImplementedError:
            pass
        res = gateway.cloud_classify(sig)
        if res:
            return res, 0.6, "cloud"
    return "unknown", 0.0, "none"  # /review 로 신규 유형 큐잉


def _match(sig, m):
    for k, v in m.items():
        if k.endswith("_min"):
            if sig.get(k[:-4], 0) < v:
                return False
        elif k.endswith("_max"):
            if sig.get(k[:-4], 0) > v:
                return False
        elif sig.get(k) != v:
            return False
    return bool(m)
