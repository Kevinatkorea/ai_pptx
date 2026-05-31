"""capture.py — '검증 직전 캡처': 후보 PPTX → PNG + 도형 기하 스냅샷.
린터가 측정할 입력(shapes, slide_size)과 사람이 볼 PNG를 동시에 생성.
"""
import glob
import os

from . import pptx_io, geometry


def capture(pptx_path, workdir, cfg):
    os.makedirs(workdir, exist_ok=True)
    # 1) 사람이 볼 PNG
    pptx_io.render_pdf(pptx_path, workdir, cfg["render"]["soffice"])
    pdf = os.path.join(workdir, os.path.splitext(os.path.basename(pptx_path))[0] + ".pdf")
    pptx_io.pdf_to_png(pdf, os.path.join(workdir, "page"), cfg["render"]["dpi"])
    pngs = sorted(glob.glob(os.path.join(workdir, "page-*.jpg")))
    # 2) 린터용 기하 스냅샷
    udir = os.path.join(workdir, "unpacked")
    pptx_io.unpack(pptx_path, udir)
    pres = open(os.path.join(udir, "ppt/presentation.xml"), encoding="utf-8").read()
    size = geometry.slide_size(pres)
    slides = []
    for sx in sorted(glob.glob(os.path.join(udir, "ppt/slides/slide*.xml"))):
        xml = open(sx, encoding="utf-8").read()
        slides.append({"file": os.path.basename(sx),
                       "shapes": geometry.extract_shapes(xml), "size": size})
    return {"pngs": pngs, "slides": slides}
