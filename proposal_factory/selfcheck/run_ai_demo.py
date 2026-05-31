"""run_ai_demo.py — AIGateway 3단 지능 게이트웨이 자기검증.

MockHttp 로 Ollama/Anthropic 응답을 시뮬레이션 (실 네트워크 호출 0).
PROPOSAL_REAL_LLM=1 + 두 백엔드 살아있으면 마지막 단계에서 실 핑.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from engine import classify  # noqa: E402
from engine.ai import AIGateway  # noqa: E402

LABELS = ["body_company_overview", "body_table", "body_image"]


class MockHttp:
    """callable: (method, url, headers, json_body, timeout) -> (status, bytes)."""
    def __init__(self, ollama_text=None, anthropic_payload=None,
                 ollama_status=200, anthropic_status=200):
        self.ollama_text = ollama_text
        self.anthropic_payload = anthropic_payload
        self.ollama_status = ollama_status
        self.anthropic_status = anthropic_status
        self.calls = []

    def __call__(self, method, url, headers=None, json_body=None, timeout=None):
        self.calls.append({"url": url, "body": json_body})
        if ":11434" in url or "/api/generate" in url:
            if self.ollama_text is None:
                raise OSError("mock: ollama unreachable")
            return self.ollama_status, json.dumps({"response": self.ollama_text}).encode("utf-8")
        if "anthropic" in url or "/v1/messages" in url:
            if self.anthropic_payload is None:
                raise OSError("mock: anthropic unreachable")
            return self.anthropic_status, json.dumps(self.anthropic_payload).encode("utf-8")
        raise OSError(f"mock: unknown url {url}")

    def calls_to(self, frag):
        return [c for c in self.calls if frag in c["url"]]


def anth_msg(text, in_tok=100, out_tok=50):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


def make_cfg(mode="tiered", budget=50.0, cloud_enabled=True, local_enabled=True,
             api_key_env="TEST_AI_KEY"):
    return {"ai": {
        "mode": mode,
        "local_llm": {"provider": "ollama", "base_url": "http://localhost:11434",
                      "model": "qwen2.5", "timeout_s": 5, "enabled": local_enabled},
        "cloud": {"provider": "anthropic", "api_key_env": api_key_env,
                  "endpoint": "https://api.anthropic.com/v1/messages",
                  "anthropic_version": "2023-06-01",
                  "classify_model": "claude-haiku-4-5",
                  "recipe_model": "claude-opus-4-7",
                  "classify_max_tokens": 200, "recipe_max_tokens": 2000,
                  "timeout_s": 5, "enabled": cloud_enabled,
                  "pricing": {
                      "claude-haiku-4-5": {"in_per_mtok": 1.0, "out_per_mtok": 5.0},
                      "claude-opus-4-7": {"in_per_mtok": 15.0, "out_per_mtok": 75.0},
                  }},
        "monthly_budget_usd": budget,
        "fallback_when_over_budget": "local_then_human",
    }}


def main():
    errors = []
    def chk(cond, msg):
        print(("   ✓ " if cond else "   ✗ ") + msg)
        if not cond:
            errors.append(msg)

    tmp_log = tempfile.mkdtemp(prefix="pf_ai_log_")
    os.environ["TEST_AI_KEY"] = "sk-mock-test"
    # 잠재적 키 누수 차단
    if "MISSING_KEY_XYZ" in os.environ:
        del os.environ["MISSING_KEY_XYZ"]

    counter = [0]
    def make_gw(cfg, http):
        counter[0] += 1
        spend = os.path.join(tmp_log, f"spend_{counter[0]}.json")
        return AIGateway(cfg, http=http, known_page_types=LABELS, spend_path=spend)

    try:
        # [1] 결정론 매칭 → 게이트웨이 호출 0회
        print("\n[1] 결정론 매칭 우선 (게이트웨이 호출 0회)")
        http = MockHttp()
        gw = make_gw(make_cfg(), http)
        library = [{"type": "body_company_overview",
                    "match": {"n_table_min": 1, "n_year_box_min": 1}}]
        shapes = [
            {"tag": "graphicFrame", "name": "표", "table_h": 1000, "texts": []},
            {"tag": "sp", "name": "연도", "texts": [{"text": "2024"}]},
        ]
        ptype, conf, src = classify.classify(shapes, library, gw)
        chk(ptype == "body_company_overview", f"결정론 매칭 (실제 {ptype})")
        chk(src == "deterministic", f"source=deterministic (실제 {src})")
        chk(len(http.calls) == 0, f"HTTP 호출 0회 (실제 {len(http.calls)})")

        # [2] 결정론 미매칭 → local 폴백
        print("\n[2] 결정론 미매칭 → local_classify")
        http = MockHttp(ollama_text='{"page_type": "body_table"}')
        gw = make_gw(make_cfg(), http)
        shapes2 = [{"tag": "sp", "name": "본문", "texts": [{"text": "x"}]}]
        library2 = [{"type": "body_company_overview",
                     "match": {"n_table_min": 1, "n_year_box_min": 1}}]
        ptype, conf, src = classify.classify(shapes2, library2, gw)
        chk(ptype == "body_table", f"local 결과 (실제 {ptype})")
        chk(src == "local_llm", f"source=local_llm (실제 {src})")
        chk(len(http.calls_to("11434")) == 1, "Ollama 1회")
        chk(len(http.calls_to("anthropic")) == 0, "Anthropic 0회")

        # [3] local 실패 → cloud 폴백 + 예산 차감
        print("\n[3] local 실패 → cloud_classify")
        http = MockHttp(ollama_text=None,
                        anthropic_payload=anth_msg('{"page_type": "body_image"}'))
        gw = make_gw(make_cfg(), http)
        b0 = gw.remaining_budget()
        ptype, conf, src = classify.classify(shapes2, library2, gw)
        chk(ptype == "body_image", f"cloud 결과 (실제 {ptype})")
        chk(src == "cloud", f"source=cloud (실제 {src})")
        b1 = gw.remaining_budget()
        chk(b1 < b0, f"예산 차감 {b0-b1:.6f} USD")

        # [4] secure_offline 모드 → 호출 0회, unknown 반환
        print("\n[4] secure_offline 모드")
        http = MockHttp(ollama_text='{"page_type": "body_table"}',
                        anthropic_payload=anth_msg('{"page_type": "body_image"}'))
        gw = make_gw(make_cfg(mode="secure_offline"), http)
        ptype, conf, src = classify.classify(shapes2, library2, gw)
        chk(ptype == "unknown", f"unknown 반환 (실제 {ptype})")
        chk(len(http.calls) == 0, f"HTTP 호출 0회 (실제 {len(http.calls)})")

        # [5] local_only 모드 → cloud 차단
        print("\n[5] local_only 모드")
        http = MockHttp(ollama_text='{"page_type": "body_table"}',
                        anthropic_payload=anth_msg('{"page_type": "body_image"}'))
        gw = make_gw(make_cfg(mode="local_only"), http)
        ptype, conf, src = classify.classify(shapes2, library2, gw)
        chk(ptype == "body_table", f"local 결과 (실제 {ptype})")
        chk(len(http.calls_to("anthropic")) == 0, "Anthropic 0회")

        # [6] cloud.enabled=False 별칭 → local_only 동작
        print("\n[6] cloud.enabled=False")
        http = MockHttp(ollama_text='{"page_type": "body_table"}',
                        anthropic_payload=anth_msg('{"page_type": "body_image"}'))
        gw = make_gw(make_cfg(cloud_enabled=False), http)
        ptype, conf, src = classify.classify(shapes2, library2, gw)
        chk(ptype == "body_table", f"local 결과 (실제 {ptype})")
        chk(len(http.calls_to("anthropic")) == 0, "Anthropic 0회")

        # [7] 예산 초과 → cloud_classify None
        print("\n[7] 예산 0 → cloud_classify None")
        http = MockHttp(anthropic_payload=anth_msg('{"page_type": "body_image"}'))
        gw = make_gw(make_cfg(budget=0.0), http)
        chk(gw.can_use_cloud() is False, "can_use_cloud=False")
        r = gw.cloud_classify({"x": 1})
        chk(r is None, f"None 반환 (실제 {r})")
        chk(len(http.calls_to("anthropic")) == 0, "API 호출 0회")

        # [8] API 키 미설정 → cloud_classify None
        print("\n[8] API 키 미설정")
        http = MockHttp(anthropic_payload=anth_msg('{"page_type": "body_image"}'))
        gw = make_gw(make_cfg(api_key_env="MISSING_KEY_XYZ"), http)
        chk(gw.can_use_cloud() is False, "can_use_cloud=False")
        r = gw.cloud_classify({"x": 1})
        chk(r is None, f"None 반환 (실제 {r})")

        # [9] author_recipe — Opus mock 응답 → 4-op 검증
        print("\n[9] author_recipe Opus")
        recipe_text = json.dumps({
            "type": "body_team_intro",
            "template_slide": "ppt/slides/slide7.xml",
            "ops": [
                {"op": "text_inject", "slot": "title", "from": "section_path"},
                {"op": "shape_rebuild", "slot": "members", "from": "members"},
            ],
        }, ensure_ascii=False)
        http = MockHttp(anthropic_payload=anth_msg(recipe_text, in_tok=500, out_tok=300))
        gw = make_gw(make_cfg(), http)
        b0 = gw.remaining_budget()
        recipe = gw.author_recipe({"n_text": 5}, [{"tag": "sp", "name": "x"}],
                                  hint="팀 소개")
        chk(isinstance(recipe, dict), f"dict 반환 (실제 {type(recipe).__name__})")
        if recipe:
            chk(recipe.get("type") == "body_team_intro", "type 매치")
            chk(len(recipe.get("ops", [])) == 2, f"ops 2개 (실제 {len(recipe.get('ops', []))})")
        b1 = gw.remaining_budget()
        # Opus pricing: (500*15 + 300*75) / 1M = 0.030
        chk(abs((b0 - b1) - 0.030) < 1e-6,
            f"Opus 비용 ~0.030 USD (실제 {b0-b1:.6f})")
        # opus 모델로 호출됐는지 확인
        opus_calls = [c for c in http.calls_to("anthropic")
                      if (c["body"] or {}).get("model") == "claude-opus-4-7"]
        chk(len(opus_calls) == 1, f"Opus 모델 1회 호출 (실제 {len(opus_calls)})")

        # [9b] map_content — 인덱스 배정만(문구 변경 없음), 유효성 필터
        print("\n[9b] map_content (verbatim 배정)")
        assign_text = json.dumps(
            {"assign": {"title": 0, "summary": 99, "bogus": 1}}, ensure_ascii=False)
        http = MockHttp(anthropic_payload=anth_msg(assign_text, in_tok=200, out_tok=40))
        gw = make_gw(make_cfg(), http)
        blocks = [{"i": 0, "text": "제안 개요"}, {"i": 1, "text": "중간"}, {"i": 2, "text": "요약문"}]
        slots = [{"key": "title", "op": "text_inject"}, {"key": "summary", "op": "text_inject"}]
        res = gw.map_content(blocks, slots, hint="회사개요")
        a = (res or {}).get("assign", {})
        chk(isinstance(res, dict) and "assign" in res, "assign dict 반환")
        chk(a.get("title") == 0, f"유효 배정 보존 title→0 (실제 {a})")
        chk("summary" not in a, "유효하지 않은 블록 인덱스(99) 제외")
        chk("bogus" not in a, "유효하지 않은 슬롯 키 제외")
        chk(all(isinstance(v, int) for v in a.values()), "값은 인덱스(정수)만 — 응답에 문구 없음")
        map_calls = [c for c in http.calls_to("anthropic")
                     if (c["body"] or {}).get("model") == "claude-haiku-4-5"]
        chk(len(map_calls) == 1, f"map_model(haiku) 1회 (실제 {len(map_calls)})")

        # [10] Anthropic 5xx → None
        print("\n[10] Anthropic 5xx")
        http = MockHttp(anthropic_payload=anth_msg("err"), anthropic_status=500)
        gw = make_gw(make_cfg(), http)
        r = gw.cloud_classify({"x": 1})
        chk(r is None, f"5xx → None (실제 {r})")

        # [11] 비-JSON 응답 → None
        print("\n[11] 비-JSON 응답")
        http = MockHttp(ollama_text="not json at all")
        gw = make_gw(make_cfg(), http)
        r = gw.local_classify({"x": 1})
        chk(r is None, f"비-JSON → None")
        # 코드 펜스 래핑된 JSON 은 파싱 성공해야 함
        http = MockHttp(ollama_text='```json\n{"page_type":"body_table"}\n```')
        gw = make_gw(make_cfg(), http)
        r = gw.local_classify({"x": 1})
        chk(r == "body_table", f"코드 펜스 JSON 파싱 (실제 {r})")

        # [12] 알 수 없는 라벨 → None
        print("\n[12] 라벨 검증")
        http = MockHttp(ollama_text='{"page_type":"not_in_library"}')
        gw = make_gw(make_cfg(), http)
        r = gw.local_classify({"x": 1})
        chk(r is None, f"알 수 없는 라벨 → None (실제 {r})")

        # [13] 실 호출 옵트인
        print("\n[13] 실 호출 옵트인 (PROPOSAL_REAL_LLM=1)")
        if os.environ.get("PROPOSAL_REAL_LLM") == "1":
            import urllib.error
            import urllib.request as _ureq
            try:
                with _ureq.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
                    ollama_up = r.status == 200
            except (urllib.error.URLError, OSError, TimeoutError):
                ollama_up = False
            anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
            print(f"   Ollama up: {ollama_up}, Anthropic key set: {bool(anth_key)}")
            if ollama_up or anth_key:
                cfg = json.load(open(os.path.join(HERE, "config.json")))
                real = AIGateway(cfg, known_page_types=LABELS,
                                 spend_path=os.path.join(tmp_log, "real_spend.json"))
                if ollama_up:
                    r = real.local_classify({"n_table": 1, "n_year_box": 1})
                    print(f"   local_classify → {r}")
                if anth_key:
                    r = real.cloud_classify({"n_table": 1, "n_year_box": 1})
                    used = cfg["ai"]["monthly_budget_usd"] - real.remaining_budget()
                    print(f"   cloud_classify → {r} (spend=${used:.6f})")
                chk(True, "실 호출 1회 실행 (결과 무관)")
            else:
                chk(True, "스킵 — 백엔드 둘 다 미가용")
        else:
            chk(True, "스킵 — PROPOSAL_REAL_LLM != 1")

        print("\n=== AI SELF-CHECK:",
              "PASS" if not errors else f"FAIL ({len(errors)})", "===")
        sys.exit(0 if not errors else 1)
    finally:
        shutil.rmtree(tmp_log, ignore_errors=True)
        if "TEST_AI_KEY" in os.environ:
            del os.environ["TEST_AI_KEY"]


if __name__ == "__main__":
    main()
