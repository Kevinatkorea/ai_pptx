"""run_selfcheck.py — 린터 v1 자기검증.
1) 정상 더미 → 'fail' 없어야 함
2) 결함 더미 → overlap·text_overflow·off_slide 모두 잡아야 함
3) (있으면) 실제 생성 슬라이드 → 리포트 출력
종료코드 0 = 통과.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.config import load_config
from engine import linter, geometry

CFG = load_config()
SLIDE = (6840538, 10261600)  # 보건복지부 템플릿 크기


def txt(t, sz=700, wrap="square", autofit=False, fonts=None):
    return {"text": t, "sz": sz, "wrap": wrap, "autofit": autofit, "fonts": fonts or set()}


def shp(name, x, y, cx, cy, texts=None):
    return {"tag": "sp", "name": name, "x": x, "y": y, "cx": cx, "cy": cy, "texts": texts or []}


def good_shapes():
    return [
        shp("회사개요표", 360000, 2600000, 6121399, 1500000),
        shp("연도박스2024", 300000, 4540000, 440000, 200000,
            [txt("2024", sz=1000, wrap="none")]),
        shp("불릿2024", 800000, 4540000, 2230000, 300000,
            [txt("국가고객만족도 초고속인터넷 IPTV 14년 연속 1위 달성", sz=700)]),
        shp("연도박스2023", 300000, 4960000, 440000, 200000,
            [txt("2023", sz=1000, wrap="none")]),
        shp("불릿2023", 800000, 4960000, 2230000, 300000,
            [txt("한국서비스품질지수 초고속인터넷 9년 연속 1위", sz=700)]),
        shp("연도박스2022", 300000, 5380000, 440000, 200000,
            [txt("2022", sz=1000, wrap="none")]),
    ]


def bad_shapes():
    s = good_shapes()
    s += [
        shp("도형A", 1000000, 3000000, 1200000, 900000),
        shp("도형B", 1500000, 3300000, 1200000, 900000),  # 도형A와 겹침
        shp("좁은박스", 300000, 6200000, 1000000, 200000,
            [txt("아주 긴 텍스트가 좁은 박스에 들어가 두 줄로 넘쳐서 겹치는 상황 테스트용 문장입니다", sz=1100)]),
        shp("화면밖", 6900000, 100000, 500000, 500000),  # 슬라이드 우측 경계 초과
    ]
    return s


def codes(findings):
    return {c for _, c, _, _ in findings}


def main():
    ok = True

    g = linter.lint(good_shapes(), *SLIDE, CFG)
    gv, gf, gw, gtext = linter.report(g)
    print(f"[1] 정상 더미 → {gv}")
    print(gtext)
    if gf != 0:
        print("   ✗ 실패: 정상 더미에서 fail 발생"); ok = False
    else:
        print("   ✓ 통과")

    b = linter.lint(bad_shapes(), *SLIDE, CFG)
    bv, bf, bw, btext = linter.report(b)
    print(f"\n[2] 결함 더미 → {bv}")
    print(btext)
    need = {"overlap", "text_overflow", "off_slide"}
    miss = need - codes(b)
    if miss:
        print(f"   ✗ 실패: 미검출 {miss}"); ok = False
    else:
        print("   ✓ 통과 (3종 결함 모두 검출)")

    # 3) 실제 생성 슬라이드(있으면)
    real = os.environ.get("REAL_SLIDE")
    if real and os.path.exists(real):
        xml = open(real, encoding="utf-8").read()
        shapes = geometry.extract_shapes(xml)
        rv, rf, rw, rtext = linter.report(linter.lint(shapes, *SLIDE, CFG))
        print(f"\n[3] 실제 슬라이드({os.path.basename(real)}, 도형 {len(shapes)}개) → {rv}")
        print(rtext)

    print("\n=== SELF-CHECK:", "PASS" if ok else "FAIL", "===")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
