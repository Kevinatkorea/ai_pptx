"""config.py — 기본값 + config.json 병합 로더(표준 라이브러리만)."""
import json
import os

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_HERE, "config.json")


def load_config(path: str = None) -> dict:
    path = path or DEFAULT_PATH
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
