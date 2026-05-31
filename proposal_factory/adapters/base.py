"""base.py — 어댑터 인터페이스 + 확장자 레지스트리 + 공통 텍스트→source_slots 변환.

이 모듈 자체는 의존성이 없다(표준 라이브러리만). 각 어댑터가 `register()` 로
확장자를 등록하고, `extract(path)` 가 확장자에 맞는 어댑터로 디스패치한다.
"""
import os


class AdapterError(Exception):
    """입력을 추출할 수 없음(형식 오류·빈 본문 등)."""


class AdapterUnavailable(AdapterError):
    """필요한 외부 파서가 미설치. (설치 힌트를 메시지에 담는다.)"""


_REGISTRY = {}  # ext(소문자, 점 없음) -> callable(path) -> ExtractResult(dict)


def _ext(path):
    return os.path.splitext(path)[1].lower().lstrip(".")


def register(ext, fn):
    _REGISTRY[ext.lower().lstrip(".")] = fn


def supported_exts():
    return set(_REGISTRY)


def is_supported(path):
    return _ext(path) in _REGISTRY


def extract(path):
    """확장자에 맞는 어댑터로 추출. 미지원 → AdapterError.
    반환: {kind, title, body, blocks, text, source_slots}."""
    fn = _REGISTRY.get(_ext(path))
    if not fn:
        raise AdapterError(f"no adapter for .{_ext(path)}")
    return fn(path)


def text_to_result(kind, text):
    """추출 텍스트 → 표준 결과 dict. 첫 비어있지 않은 줄=title, 나머지=body.

    source_slots 의 키(title/body/blocks/text)는 레시피의 `from` 에서 참조한다.
    문서엔 슬라이드 구조가 없으므로 표/이미지 슬롯은 채우지 않는다(사이드카 필요).
    """
    blocks = [ln.strip() for ln in (text or "").splitlines()]
    blocks = [b for b in blocks if b]
    title = blocks[0] if blocks else ""
    body = "\n".join(blocks[1:])
    return {
        "kind": kind,
        "title": title,
        "body": body,
        "blocks": blocks,
        "text": text or "",
        "source_slots": {"title": title, "body": body,
                         "blocks": blocks, "text": text or ""},
    }
