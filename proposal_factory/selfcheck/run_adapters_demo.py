"""run_adapters_demo.py — 입력 어댑터 레이어(PDF/HWP/텍스트) 자기검증.

표준 라이브러리 + engine + adapters + web.store 만 사용(외부 파서 미설치 가정).
검증:
  [1] 레지스트리: txt/md/pdf/hwp 등록.
  [2] text 어댑터: 추출 → source_slots(title/body/blocks).
  [3] pdf/hwp: 외부 파서 미설치 → AdapterUnavailable (우아한 실패).
  [4] HWP5 레코드 파서(순수): 합성 섹션(평/압축)에서 PARA_TEXT 추출·컨트롤 처리.
  [5] 미지원 확장자 → AdapterError.
  [6] 데몬 통합: .txt + 사이드카(template+page_type) → 추출 source_slots 로 변환.
  [7] 데몬 통합: 외부 파서 없는 .pdf → needs_human_approval + adapter_error(데몬 안 죽음).
  [8] (실파일) pypdf 설치 시: 직접 만든 PDF → 실제 추출 → 데몬 변환 왕복.
  [9] (실파일) olefile 설치 시: 비-OLE .hwp → 실제 olefile 로 형식 거부(AdapterError).
  [10] (실파일) HWPX(.hwpx): 직접 만든 ZIP+XML → 추출 → 데몬 변환(외부 의존 없음, 항상 실행).

[7]은 파서 미설치 가정 단계라 설치된 환경에서는 의미가 약하지만(그대로 통과), [8]/[9]가
설치된 환경의 실제 경로를 덮는다. [8]/[9]는 파서 미설치 시 자동 스킵된다.
"""
import json
import os
import sys
import tempfile
import zipfile
import zlib

HERE_SELF = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE_SELF))
sys.path.insert(0, HERE_SELF)

import adapters
from adapters import hwp_adapter
from engine import pptx_io
from engine.config import load_config
from daemon import watch_inbox
from web.store import JobStore
from run_transform_demo import build_template

CFG = load_config()

try:
    import pypdf  # noqa: F401
    HAS_PDF = True
except ImportError:
    HAS_PDF = False
try:
    import olefile  # noqa: F401
    HAS_OLE = True
except ImportError:
    HAS_OLE = False


def _make_pdf(path, text):
    """stdlib 만으로 텍스트 1줄짜리 최소 유효 PDF 생성(pypdf 가 추출 가능)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n0000000000 65535 f \n"
    for o in offs:
        out += ("%010d 00000 n \n" % o).encode()
    out += (b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode() + b"\n%%EOF")
    with open(path, "wb") as fh:
        fh.write(out)


def _make_hwpx(path, title, body):
    """stdlib 만으로 최소 유효 .hwpx(ZIP+OWPML) 생성 — 외부 의존성 없음."""
    section = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section">'
        f'<hp:p><hp:run><hp:t>{title}</hp:t></hp:run></hp:p>'
        f'<hp:p><hp:run><hp:t>{body}</hp:t></hp:run></hp:p>'
        '</hs:sec>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("version.xml", '<?xml version="1.0"?><hv:HCFVersion '
                   'xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
                   'tagetApplication="WORDPROCESSOR"/>')
        z.writestr("Contents/section0.xml", section)


def _hwp_record(text_wchars):
    """PARA_TEXT 레코드 1개를 합성. text_wchars: str(컨트롤 포함)."""
    payload = text_wchars.encode("utf-16-le")
    header = (hwp_adapter.HWPTAG_PARA_TEXT & 0x3FF) | (len(payload) << 20)
    return header.to_bytes(4, "little") + payload


def main():
    errors = []
    def chk(cond, msg):
        print(("   ✓ " if cond else "   ✗ ") + msg)
        if not cond:
            errors.append(msg)

    tmp = tempfile.mkdtemp(prefix="pf_adapters_")
    try:
        print("[1] 레지스트리 등록")
        exts = adapters.supported_exts()
        chk({"txt", "md", "pdf", "hwp", "hwpx"} <= exts,
            f"txt/md/pdf/hwp/hwpx 등록 (실제 {sorted(exts)})")

        print("\n[2] text 어댑터 추출")
        txt = os.path.join(tmp, "doc.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("문서 제목\n첫 문단\n둘째 문단\n")
        r = adapters.extract(txt)
        chk(r["kind"] == "txt", "kind=txt")
        chk(r["title"] == "문서 제목", f"title 추출 (실제 {r['title']!r})")
        chk(r["source_slots"]["body"] == "첫 문단\n둘째 문단", "body 결합")
        chk(r["source_slots"]["blocks"] == ["문서 제목", "첫 문단", "둘째 문단"], "blocks 분리")

        print("\n[3] pdf/hwp 우아한 실패 (미설치→Unavailable / 설치→추출 실패)")
        for name, has in (("a.pdf", HAS_PDF), ("b.hwp", HAS_OLE)):
            fp = os.path.join(tmp, name)
            with open(fp, "wb") as fh:
                fh.write(b"%dummy")
            try:
                adapters.extract(fp)
                chk(False, f"{name}: 예외 기대했으나 성공")
            except adapters.AdapterUnavailable as e:
                chk(not has, f"{name}: 미설치 → AdapterUnavailable ({str(e).split(':')[0]})")
            except adapters.AdapterError as e:
                chk(has, f"{name}: 설치됨 → 추출 실패 AdapterError ({str(e)[:24]})")

        print("\n[4] HWP5 레코드 파서(순수, 합성 데이터)")
        # "안녕"+개행(13)+"세계"+개체컨트롤(2)+더미7 wchar → "안녕\n세계"
        wchars = "안녕" + "\r" + "세계" + "\x02" + ("\x00" * 7)
        rec = _hwp_record(wchars)
        plain = hwp_adapter.extract_sections([rec], compressed=False)
        chk(plain == "안녕\n세계", f"평문 섹션 파싱 (실제 {plain!r})")
        co = zlib.compressobj(9, zlib.DEFLATED, -15)   # raw deflate(HWP5 압축 방식)
        comp = co.compress(rec) + co.flush()
        plain2 = hwp_adapter.extract_sections([comp], compressed=True)
        chk(plain2 == "안녕\n세계", f"압축 섹션 inflate+파싱 (실제 {plain2!r})")

        print("\n[5] 미지원 확장자 → AdapterError")
        try:
            adapters.extract(os.path.join(tmp, "x.zip"))
            chk(False, "예외 기대")
        except adapters.AdapterError:
            chk(True, ".zip → AdapterError")

        # ---- 데몬 통합 ----
        store = JobStore(os.path.join(tmp, "store"))
        std_tree = os.path.join(tmp, "std")
        os.makedirs(std_tree)
        build_template(std_tree)
        std_pptx = os.path.join(tmp, "standard.pptx")
        pptx_io.pack(std_tree, std_pptx)
        assets = {
            "page_types": [{"type": "doc", "match": {"n_text_min": 1}}],
            "recipes": {"doc": {"type": "doc", "template_slide": "ppt/slides/slide1.xml",
                                "ops": [{"op": "text_inject", "slot": "breadcrumb",
                                         "from": "title"}]}},
            "base_dir": tmp,
        }

        print("\n[6] 데몬 통합: .txt + 사이드카(template+page_type) → 변환")
        doc2 = os.path.join(tmp, "고객문서.txt")
        with open(doc2, "w", encoding="utf-8") as fh:
            fh.write("제안 개요 문서\n내용 본문\n")
        with open(os.path.splitext(doc2)[0] + ".job.json", "w", encoding="utf-8") as fh:
            json.dump({"template_pptx": std_pptx, "page_type": "doc"}, fh, ensure_ascii=False)
        man = watch_inbox.ingest(doc2, CFG, store, assets, gateway=None)
        tx = man.get("transform") or {}
        chk(man.get("page_type") == "doc" and man.get("classify", {}).get("source") == "forced",
            f"page_type 강제=doc/forced (실제 {man.get('page_type')}/{man.get('classify', {}).get('source')})")
        chk(man.get("extracted", {}).get("kind") == "txt", "extracted.kind=txt 보존")
        out = tx.get("out_pptx")
        content_ok = False
        if "error" not in tx and out and os.path.exists(out):
            with zipfile.ZipFile(out) as z:
                content_ok = "제안 개요 문서" in z.read("ppt/slides/slide1.xml").decode("utf-8")
        chk(content_ok, "추출 title 이 표준 템플릿 slot:breadcrumb 에 반영")

        print("\n[7] 데몬 통합: 추출 불가 .pdf → 검수 큐(데몬 안 죽음)")
        pdf = os.path.join(tmp, "스캔.pdf")
        with open(pdf, "wb") as fh:
            fh.write(b"%PDF-1.4 dummy")   # 본문 없는/깨진 PDF
        man2 = watch_inbox.ingest(pdf, CFG, store, assets, gateway=None)
        # 미설치면 AdapterUnavailable(pypdf 힌트), 설치면 파싱 실패/빈 본문 — 어느 쪽이든 검수 큐.
        chk(man2.get("status") == "needs_human_approval", f"상태 needs_human_approval (실제 {man2.get('status')})")
        chk(bool(man2.get("adapter_error")), f"adapter_error 기록 ({man2.get('adapter_error')})")
        chk(store.read_manifest(man2["id"]) is not None, "실패 건도 store 에 기록")

        print("\n[8] (실파일) PDF 왕복 — pypdf 실제 추출 → 데몬 변환")
        if not HAS_PDF:
            print("   · 스킵 — pypdf 미설치 (pip install -r requirements-adapters.txt)")
        else:
            token = "RegressionPDFcontent2026"
            real_pdf = os.path.join(tmp, "제안서.pdf")
            _make_pdf(real_pdf, token)
            rp = adapters.extract(real_pdf)
            chk(rp["kind"] == "pdf" and token in rp["text"],
                f"실제 PDF 추출에 토큰 포함 (실제 {rp['text']!r})")
            with open(os.path.splitext(real_pdf)[0] + ".job.json", "w", encoding="utf-8") as fh:
                json.dump({"template_pptx": std_pptx, "page_type": "doc"}, fh, ensure_ascii=False)
            man3 = watch_inbox.ingest(real_pdf, CFG, store, assets, gateway=None)
            tx3 = man3.get("transform") or {}
            out3 = tx3.get("out_pptx")
            pdf_in_slide = False
            if "error" not in tx3 and out3 and os.path.exists(out3):
                with zipfile.ZipFile(out3) as z:
                    pdf_in_slide = token in z.read("ppt/slides/slide1.xml").decode("utf-8")
            chk(man3.get("extracted", {}).get("kind") == "pdf", "manifest.extracted.kind=pdf")
            chk(pdf_in_slide, "PDF 추출 텍스트가 표준 템플릿 슬라이드에 반영")

        print("\n[9] (실파일) HWP 경계 — olefile 실제 형식 판별")
        if not HAS_OLE:
            print("   · 스킵 — olefile 미설치")
        else:
            fake_hwp = os.path.join(tmp, "잘못된.hwp")
            with open(fake_hwp, "wb") as fh:
                fh.write(b"this is not an OLE compound file")
            try:
                adapters.extract(fake_hwp)
                chk(False, "비-OLE 인데 예외 없음")
            except adapters.AdapterUnavailable:
                chk(False, "olefile 설치됐는데 Unavailable 반환")
            except adapters.AdapterError as e:
                chk("OLE" in str(e) or "HWP" in str(e),
                    f"실제 olefile 로 비-OLE 거부 → AdapterError ({e})")

        print("\n[10] (실파일) HWPX 왕복 — stdlib zip/xml, 외부 의존 없음")
        hwpx = os.path.join(tmp, "제안서.hwpx")
        _make_hwpx(hwpx, "HWPX 제안 개요", "HWPX 본문 내용 라인")
        rh = adapters.extract(hwpx)
        chk(rh["kind"] == "hwpx" and rh["title"] == "HWPX 제안 개요",
            f"HWPX 추출 title (실제 {rh.get('title')!r})")
        chk(rh["source_slots"]["body"] == "HWPX 본문 내용 라인", "HWPX body 추출")
        with open(os.path.splitext(hwpx)[0] + ".job.json", "w", encoding="utf-8") as fh:
            json.dump({"template_pptx": std_pptx, "page_type": "doc"}, fh, ensure_ascii=False)
        man4 = watch_inbox.ingest(hwpx, CFG, store, assets, gateway=None)
        tx4 = man4.get("transform") or {}
        out4 = tx4.get("out_pptx")
        hwpx_in_slide = False
        if "error" not in tx4 and out4 and os.path.exists(out4):
            with zipfile.ZipFile(out4) as z:
                hwpx_in_slide = "HWPX 제안 개요" in z.read("ppt/slides/slide1.xml").decode("utf-8")
        chk(man4.get("extracted", {}).get("kind") == "hwpx", "manifest.extracted.kind=hwpx")
        chk(hwpx_in_slide, "HWPX 추출 텍스트가 표준 템플릿 슬라이드에 반영")

        print("\n=== ADAPTERS SELF-CHECK:",
              "PASS" if not errors else f"FAIL ({len(errors)})", "===")
        sys.exit(0 if not errors else 1)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
