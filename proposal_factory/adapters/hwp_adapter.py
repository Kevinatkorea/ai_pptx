"""hwp_adapter.py — HWP 5.0 본문 텍스트 추출 어댑터.

HWP5 = OLE 복합 파일. 본문은 `BodyText/Section0..N` 스트림에 레코드로 저장되며,
`FileHeader` 의 플래그 비트0 이 켜져 있으면 각 섹션은 raw-deflate(zlib wbits=-15) 압축이다.

레코드 파싱(`parse_records`/`_para_text`/`extract_sections`)은 **순수 함수**라 외부 의존성
없이 검증된다. OLE 컨테이너 읽기만 `olefile` 에 위임하고 lazy import 한다(미설치 →
AdapterUnavailable). 설치: `pip install olefile`.
"""
import zlib

from . import base

HWPTAG_BEGIN = 0x010
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51  # 0x43 = 67

# PARA_TEXT 의 인라인 컨트롤 문자(UTF-16LE WCHAR 단위).
#  - 1 WCHAR 점유(문자 컨트롤): 줄바꿈/단락 등.
#  - 8 WCHAR(16바이트) 점유(인라인/확장 컨트롤): 표·그림·각주 등 개체 참조.
_CTRL_1WCHAR = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}
_CTRL_8WCHAR = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _para_text(payload: bytes) -> str:
    """PARA_TEXT 페이로드(UTF-16LE + 인라인 컨트롤) → 평문. 개체 컨트롤은 건너뛴다."""
    out = []
    i, n = 0, len(payload)
    while i + 1 < n:
        code = payload[i] | (payload[i + 1] << 8)
        if code in _CTRL_8WCHAR:
            i += 16          # 8 WCHAR 점유
            continue
        if code in _CTRL_1WCHAR:
            if code in (10, 13):
                out.append("\n")
            i += 2
            continue
        out.append(chr(code))
        i += 2
    return "".join(out)


def parse_records(data: bytes):
    """레코드 스트림에서 PARA_TEXT 레코드의 텍스트만 추출 → 문단 리스트.

    레코드 헤더(4바이트 LE): tag=하위10bit, level=다음10bit, size=상위12bit.
    size==0xFFF 이면 다음 4바이트가 실제 크기.
    """
    paras, i, n = [], 0, len(data)
    while i + 4 <= n:
        header = int.from_bytes(data[i:i + 4], "little")
        i += 4
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if i + 4 > n:
                break
            size = int.from_bytes(data[i:i + 4], "little")
            i += 4
        payload = data[i:i + size]
        i += size
        if tag == HWPTAG_PARA_TEXT:
            paras.append(_para_text(payload))
    return paras


def extract_sections(section_datas, compressed: bool) -> str:
    """섹션 바이트들(압축 여부 동일) → 본문 텍스트. 순수 함수(테스트 용이)."""
    paras = []
    for raw in section_datas:
        data = zlib.decompress(raw, -15) if compressed else raw
        paras.extend(parse_records(data))
    return "\n".join(p for p in paras if p)


def _read_sections(path):
    """olefile 로 FileHeader 압축 플래그 + BodyText 섹션들을 읽는다(lazy 의존)."""
    try:
        import olefile
    except ImportError as e:
        raise base.AdapterUnavailable(
            "HWP 추출에는 olefile 이 필요합니다: pip install olefile") from e
    if not olefile.isOleFile(path):
        raise base.AdapterError("HWP 5.0(OLE) 형식이 아닙니다(구버전 HWP/HWPX 미지원)")
    ole = olefile.OleFileIO(path)
    try:
        header = ole.openstream("FileHeader").read()
        compressed = bool(header[36] & 0x01) if len(header) > 36 else False
        sections, idx = [], 0
        while ole.exists(f"BodyText/Section{idx}"):
            sections.append(ole.openstream(f"BodyText/Section{idx}").read())
            idx += 1
    finally:
        ole.close()
    return sections, compressed


def extract_hwp(path):
    sections, compressed = _read_sections(path)
    if not sections:
        raise base.AdapterError("BodyText 섹션이 없습니다")
    text = extract_sections(sections, compressed)
    if not text.strip():
        raise base.AdapterError("본문 텍스트를 찾지 못했습니다")
    return base.text_to_result("hwp", text)


base.register("hwp", extract_hwp)
