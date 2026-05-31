"""pdf_adapter.py — PDF 텍스트 추출 어댑터(pypdf lazy import).

pypdf 미설치 시 AdapterUnavailable 로 우아하게 실패한다(엔진/데몬은 stdlib 유지).
설치: `pip install pypdf` (adapters 레이어 의존성).
"""
from . import base


def extract_pdf(path):
    try:
        import pypdf
    except ImportError as e:
        raise base.AdapterUnavailable(
            "PDF 추출에는 pypdf 가 필요합니다: pip install pypdf") from e
    try:
        reader = pypdf.PdfReader(path)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except base.AdapterError:
        raise
    except Exception as e:  # pypdf 내부 파싱 오류 등
        raise base.AdapterError(f"PDF 파싱 실패: {type(e).__name__}: {e}") from e
    if not text.strip():
        raise base.AdapterError("PDF 에서 텍스트를 찾지 못함(스캔 이미지일 수 있음)")
    return base.text_to_result("pdf", text)


base.register("pdf", extract_pdf)
