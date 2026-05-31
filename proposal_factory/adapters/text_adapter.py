"""text_adapter.py — 일반 텍스트(.txt/.md) 입력 어댑터(외부 의존성 없음).

어댑터 레이어의 레퍼런스 구현. 의존성이 없어 전체 파이프라인을 검증하는 기준이 된다.
"""
from . import base


def extract_txt(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return base.text_to_result("txt", fh.read())


base.register("txt", extract_txt)
base.register("md", extract_txt)
