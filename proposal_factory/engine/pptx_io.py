"""pptx_io.py — PPTX 입출력 래퍼.

기본 동작: 표준 라이브러리 zipfile 만으로 unpack/pack/clean.
하위 호환: 환경변수 PPTX_TOOLS 가 설정돼 있고 해당 경로에 외부 스크립트가
존재하면 그쪽으로 폴백한다(Phase-0 빌드 호환용).

render_pdf/pdf_to_png 는 soffice/pdftoppm 외부 호출 그대로 유지(선택 사용).
"""
import os
import shutil
import subprocess
import zipfile

PPTX_TOOLS = os.environ.get("PPTX_TOOLS", "")


def _use_external(script_rel: str) -> bool:
    return bool(PPTX_TOOLS) and os.path.exists(os.path.join(PPTX_TOOLS, script_rel))


def _py(script, *args):
    subprocess.run(["python3", os.path.join(PPTX_TOOLS, script), *args], check=True)


def unpack(pptx: str, outdir: str) -> None:
    """pptx → outdir 로 전부 추출. 기존 outdir 가 있으면 비우고 다시 채운다."""
    if _use_external("ooxml/scripts/unpack.py"):
        _py("ooxml/scripts/unpack.py", pptx, outdir)
        return
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir, exist_ok=True)
    with zipfile.ZipFile(pptx, "r") as z:
        z.extractall(outdir)


def pack(srcdir: str, out: str, original: str = None) -> None:
    """srcdir 내용을 out(pptx) 으로 압축.

    OOXML 호환을 위해 [Content_Types].xml 을 zip 의 첫 엔트리로 둔다(권장 관례).
    original 인자는 호환 인자로 받기만 하고, zip 의 파일 순서를 원본과 맞추는
    용도로 사용한다(주어진 경우). 일부 옛 PowerPoint/뷰어가 순서에 민감한 사례
    대비.
    """
    if _use_external("ooxml/scripts/pack.py"):
        if original:
            _py("ooxml/scripts/pack.py", srcdir, out, "--original", original)
        else:
            _py("ooxml/scripts/pack.py", srcdir, out)
        return

    files = []
    for root, _dirs, names in os.walk(srcdir):
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, srcdir).replace(os.sep, "/")
            files.append(rel)

    order = []
    if "[Content_Types].xml" in files:
        order.append("[Content_Types].xml")

    if original and os.path.isfile(original):
        seen = set(order)
        try:
            with zipfile.ZipFile(original, "r") as z:
                for info in z.infolist():
                    name = info.filename
                    if name in files and name not in seen:
                        order.append(name)
                        seen.add(name)
        except zipfile.BadZipFile:
            pass

    for f in files:
        if f not in order:
            order.append(f)

    if os.path.dirname(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in order:
            z.write(os.path.join(srcdir, rel), arcname=rel)


def clean(srcdir: str) -> None:
    """언팩된 트리에서 임시·OS 부산물 제거."""
    if _use_external("ooxml/scripts/clean.py"):
        _py("ooxml/scripts/clean.py", srcdir)
        return
    for root, _dirs, names in os.walk(srcdir):
        for n in names:
            if n in (".DS_Store", "Thumbs.db") or n.endswith("~"):
                try:
                    os.remove(os.path.join(root, n))
                except OSError:
                    pass


def render_pdf(pptx: str, outdir: str, soffice: str = "soffice") -> None:
    subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                    "--outdir", outdir, pptx], check=True)


def pdf_to_png(pdf: str, prefix: str, dpi: int = 140) -> None:
    subprocess.run(["pdftoppm", "-jpeg", "-r", str(dpi), pdf, prefix], check=True)
