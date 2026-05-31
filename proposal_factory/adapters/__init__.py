"""adapters — 비-PPTX 입력(PDF/HWP/텍스트)을 표준 콘텐츠로 추출하는 별도 레이어.

engine 은 표준 라이브러리 전용 원칙을 유지한다. 외부 파서 의존성(pypdf, olefile)은
이 레이어 안에서만 **lazy import** 되며, 미설치 시 `AdapterUnavailable` 로 우아하게
실패한다(엔진/데몬 크래시 없음). 어댑터는 import 시 자동으로 레지스트리에 등록된다.

공개 API:
    extract(path) -> dict        # {kind, title, body, blocks, text, source_slots}
    is_supported(path) -> bool
    supported_exts() -> set[str]
    AdapterError / AdapterUnavailable
"""
from .base import (AdapterError, AdapterUnavailable, extract, is_supported,
                   register, supported_exts, text_to_result)
from . import text_adapter, pdf_adapter, hwp_adapter, hwpx_adapter  # noqa: F401  (등록 부작용)

__all__ = ["AdapterError", "AdapterUnavailable", "extract", "is_supported",
           "register", "supported_exts", "text_to_result"]
