"""preview.py — out.pptx → preview.png 폴백 렌더러.

soffice 와 pdftoppm 이 모두 설치돼 있을 때만 동작. 없거나 실패하면 False.
PROPOSAL_PREVIEW_DISABLE=1 환경변수로 비활성화 가능(테스트용).
"""
import glob
import os
import shutil
import subprocess
import tempfile


SOFFICE_TIMEOUT = 60
PDFTOPPM_TIMEOUT = 30


def available() -> bool:
    if os.environ.get("PROPOSAL_PREVIEW_DISABLE") == "1":
        return False
    return bool(shutil.which("soffice") and shutil.which("pdftoppm"))


def try_render(pptx_path: str, out_png: str, dpi: int = 96) -> bool:
    if not available():
        return False
    if not os.path.isfile(pptx_path):
        return False
    tmp = tempfile.mkdtemp(prefix="pf_preview_")
    try:
        try:
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf",
                 "--outdir", tmp, pptx_path],
                check=True, capture_output=True, timeout=SOFFICE_TIMEOUT)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
        pdfs = glob.glob(os.path.join(tmp, "*.pdf"))
        if not pdfs:
            return False
        prefix = os.path.join(tmp, "p")
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi),
                 "-f", "1", "-l", "1", pdfs[0], prefix],
                check=True, capture_output=True, timeout=PDFTOPPM_TIMEOUT)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
        pngs = sorted(glob.glob(prefix + "*.png"))
        if not pngs:
            return False
        os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
        shutil.copyfile(pngs[0], out_png)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
