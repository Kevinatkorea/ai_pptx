"""hwpx_adapter.py — HWPX(.hwpx) 본문 텍스트 추출 어댑터.

HWPX 는 OWPML 패키지로, ZIP 컨테이너 안에 XML 이 들어있다(.pptx 와 동일 계열).
본문은 `Contents/section0.xml`, `section1.xml` ... 에 `<hp:p>`(문단) / `<hp:t>`(텍스트 런)
형태로 저장된다. **ZIP·XML 은 표준 라이브러리로 처리되므로 외부 의존성이 없다**
(구버전 바이너리 .hwp 와 달리 olefile 불필요).
"""
import html
import re
import zipfile

from . import base

_SECTION_RE = re.compile(r"Contents/section\d+\.xml$")
_PARA_RE = re.compile(r"<hp:p\b.*?</hp:p>", re.S)
_RUN_T_RE = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _para_text(para_block: str) -> str:
    """한 문단(<hp:p>) 블록 → 평문. 런(<hp:t>)들을 잇고 인라인 태그/엔티티 처리."""
    runs = _RUN_T_RE.findall(para_block)
    text = "".join(runs)
    text = _TAG_RE.sub("", text)        # <hp:lineBreak/> 등 인라인 태그 제거
    return html.unescape(text).strip()


def extract_text_from_sections(section_xmls) -> str:
    """섹션 XML 문자열들 → 본문 텍스트(문단=줄). 순수 함수(테스트 용이)."""
    paras = []
    for xml in section_xmls:
        for pblock in _PARA_RE.findall(xml):
            t = _para_text(pblock)
            if t:
                paras.append(t)
    return "\n".join(paras)


def extract_hwpx(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if _SECTION_RE.search(n))
            if not names:
                raise base.AdapterError("HWPX section XML 이 없습니다(올바른 .hwpx 아님)")
            sections = [z.read(n).decode("utf-8", "replace") for n in names]
    except zipfile.BadZipFile as e:
        raise base.AdapterError("HWPX(ZIP) 형식이 아닙니다") from e
    text = extract_text_from_sections(sections)
    if not text.strip():
        raise base.AdapterError("본문 텍스트를 찾지 못했습니다")
    return base.text_to_result("hwpx", text)


base.register("hwpx", extract_hwpx)
