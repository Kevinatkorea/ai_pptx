"""ai.py — 3단 지능 게이트웨이 + 월 예산 상한.

deterministic(무료) → local LLM(Ollama, 무료) → Claude API(소액, 상한 내).

모드:
  - tiered           : 결정론 → 로컬 → 클라우드 (평상시)
  - local_only       : 결정론 → 로컬 (cloud 차단). cloud.enabled=False 와 동일.
  - secure_offline   : 결정론만 (로컬/클라우드 둘 다 차단)

HTTP 는 stdlib urllib.request 만 사용. 테스트용으로 callable http 주입 가능.
"""
import json
import os
import time
import urllib.error
import urllib.request


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SPEND_PATH = os.path.join(_ROOT, "logs", "ai_spend.json")

_VALID_OPS = {"text_inject", "group_fill", "table_rebuild", "image_reuse", "shape_rebuild"}


def _stdlib_http(method, url, headers=None, json_body=None, timeout=30):
    """urllib 기반 HTTP. 반환 (status, body_bytes). 네트워크 에러는 raise."""
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class AIGateway:
    def __init__(self, cfg, http=None, known_page_types=None, spend_path=None):
        self.cfg = cfg["ai"]
        self.mode = self.cfg.get("mode", "tiered")
        self.local = self.cfg.get("local_llm", {}) or {}
        self.cloud = self.cfg.get("cloud", {}) or {}
        self.budget = float(self.cfg.get("monthly_budget_usd", 0))
        self.known_page_types = list(known_page_types or [])
        self._http = http or _stdlib_http
        self.spend_path = spend_path or _DEFAULT_SPEND_PATH

    # ---- 정책 ----

    def _is_secure_offline(self):
        return self.mode == "secure_offline"

    def _is_local_only(self):
        return self.mode == "local_only" or not self.cloud.get("enabled", True)

    def can_use_local(self):
        if self._is_secure_offline():
            return False
        return bool(self.local.get("enabled", True))

    def can_use_cloud(self):
        if self._is_secure_offline() or self._is_local_only():
            return False
        if not self.cloud.get("enabled", True):
            return False
        key_env = self.cloud.get("api_key_env", "ANTHROPIC_API_KEY")
        if not os.environ.get(key_env):
            return False
        return self.remaining_budget() > 0

    # ---- 예산 ----

    def _spend(self):
        try:
            with open(self.spend_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}

    def remaining_budget(self):
        m = time.strftime("%Y-%m")
        return self.budget - self._spend().get(m, 0.0)

    def _record(self, usd):
        if usd <= 0:
            return
        m = time.strftime("%Y-%m")
        s = self._spend()
        s[m] = round(s.get(m, 0.0) + usd, 6)
        try:
            os.makedirs(os.path.dirname(self.spend_path), exist_ok=True)
            with open(self.spend_path, "w", encoding="utf-8") as fh:
                json.dump(s, fh)
        except OSError:
            pass

    # ---- Ollama ----

    def _call_ollama(self, prompt, json_format=True):
        if not self.can_use_local():
            return None
        url = self.local.get("base_url", "http://localhost:11434").rstrip("/") + "/api/generate"
        body = {
            "model": self.local.get("model", "qwen2.5"),
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        }
        if json_format:
            body["format"] = "json"
        try:
            status, raw = self._http(
                "POST", url, headers={}, json_body=body,
                timeout=self.local.get("timeout_s", 30))
        except (urllib.error.URLError, OSError, TimeoutError):
            return None
        if status != 200:
            return None
        try:
            data = json.loads(raw)
            return data.get("response", "")
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    # ---- Anthropic ----

    def _call_anthropic(self, model, prompt, system, max_tokens):
        if not self.can_use_cloud():
            return None
        url = self.cloud.get("endpoint", "https://api.anthropic.com/v1/messages")
        key_env = self.cloud.get("api_key_env", "ANTHROPIC_API_KEY")
        api_key = os.environ.get(key_env, "")
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self.cloud.get("anthropic_version", "2023-06-01"),
        }
        body = {
            "model": model,
            "max_tokens": int(max_tokens),
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            status, raw = self._http(
                "POST", url, headers=headers, json_body=body,
                timeout=self.cloud.get("timeout_s", 60))
        except (urllib.error.URLError, OSError, TimeoutError):
            return None
        if status != 200:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        # 비용 기록
        usage = data.get("usage") or {}
        pricing = (self.cloud.get("pricing") or {}).get(model) or {}
        usd = (usage.get("input_tokens", 0) * pricing.get("in_per_mtok", 0)
               + usage.get("output_tokens", 0) * pricing.get("out_per_mtok", 0)) / 1_000_000
        self._record(usd)
        # content 추출
        for p in data.get("content") or []:
            if isinstance(p, dict) and p.get("type") == "text":
                return p.get("text", "")
        return None

    # ---- 프롬프트 ----

    _CLASSIFY_SYSTEM = (
        "당신은 슬라이드 구조 분류기다. "
        "출력은 단일 JSON 객체 {\"page_type\": \"<라벨>\"} 만 허용. "
        "라벨은 입력 목록의 값 중 하나여야 한다."
    )

    _RECIPE_SYSTEM = (
        "당신은 결정론적 PPTX 변환 레시피 작성기다. "
        "출력은 단일 JSON 객체 {type, template_slide, ops:[...]}. "
        "op 는 [text_inject, table_rebuild, image_reuse, shape_rebuild] 중에서만 사용."
    )

    _MAP_SYSTEM = (
        "당신은 슬롯 배정기다. **텍스트를 절대 바꾸거나 다시 쓰지 마라.** "
        "각 표준 슬롯에 가장 잘 맞는 초안 블록의 인덱스(번호)만 고른다. "
        "출력은 단일 JSON 객체 {\"assign\": {\"<slot_key>\": <block_index>}} 만 허용. "
        "맞는 블록이 없는 슬롯은 생략한다. 텍스트 내용은 응답에 절대 포함하지 마라."
    )

    def _classify_prompt(self, sig):
        labels = self.known_page_types + ["unknown"]
        return (
            "다음 시그니처에 가장 잘 맞는 페이지 유형을 라벨 목록에서 골라라.\n"
            "잘 맞는 게 없으면 'unknown'. 응답은 {\"page_type\":\"<라벨>\"} JSON.\n\n"
            f"시그니처: {json.dumps(sig, ensure_ascii=False)}\n"
            f"라벨: {json.dumps(labels, ensure_ascii=False)}"
        )

    def _recipe_prompt(self, sig, sample_shapes, hint):
        sample_json = json.dumps(sample_shapes, ensure_ascii=False, default=str)
        if len(sample_json) > 4000:
            sample_json = sample_json[:4000]
        return (
            "신규 페이지 유형 후보. 시그니처/샘플 도형들을 보고 변환 레시피(JSON) 작성.\n"
            "사용 가능 op: text_inject / table_rebuild / image_reuse / shape_rebuild.\n"
            "각 op = {op, slot, from, ...}. slot 은 도형 name='slot:<key>' 의 키.\n\n"
            f"hint: {hint}\n"
            f"signature: {json.dumps(sig, ensure_ascii=False)}\n"
            f"shapes: {sample_json}"
        )

    def _map_prompt(self, blocks, slots, hint):
        b = json.dumps([{"i": x["i"], "text": x["text"]} for x in blocks],
                       ensure_ascii=False)
        s = json.dumps([{"key": x["key"], "op": x.get("op")} for x in slots],
                       ensure_ascii=False)
        if len(b) > 6000:
            b = b[:6000]
        return (
            "초안 텍스트 블록을 표준 템플릿의 슬롯에 배정하라. **문구는 절대 바꾸지 마라** — "
            "슬롯마다 가장 잘 맞는 블록의 인덱스만 고른다.\n"
            "응답: {\"assign\": {\"<slot_key>\": <block_index>}} JSON.\n\n"
            f"hint: {hint}\n"
            f"slots: {s}\n"
            f"blocks: {b}"
        )

    @staticmethod
    def _try_parse_json(text):
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        # 코드 펜스 제거 시도
        s = (text or "").strip()
        if s.startswith("```"):
            s = s.strip("`")
            if s.startswith("json"):
                s = s[4:]
            try:
                return json.loads(s.strip("`").strip())
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return None

    # ---- 공개 메서드 ----

    def local_classify(self, sig):
        if not self.can_use_local():
            return None
        raw = self._call_ollama(self._classify_prompt(sig), json_format=True)
        if not raw:
            return None
        data = self._try_parse_json(raw)
        if not isinstance(data, dict):
            return None
        ptype = data.get("page_type")
        return ptype if ptype in self.known_page_types else None

    def cloud_classify(self, sig):
        if not self.can_use_cloud():
            return None
        model = self.cloud.get("classify_model", "claude-haiku-4-5")
        max_t = self.cloud.get("classify_max_tokens", 200)
        raw = self._call_anthropic(model, self._classify_prompt(sig),
                                   self._CLASSIFY_SYSTEM, max_t)
        if not raw:
            return None
        data = self._try_parse_json(raw)
        if not isinstance(data, dict):
            return None
        ptype = data.get("page_type")
        return ptype if ptype in self.known_page_types else None

    def author_recipe(self, sig, sample_shapes, hint=""):
        if not self.can_use_cloud():
            return None
        model = self.cloud.get("recipe_model", "claude-opus-4-7")
        max_t = self.cloud.get("recipe_max_tokens", 2000)
        raw = self._call_anthropic(model,
                                   self._recipe_prompt(sig, sample_shapes, hint),
                                   self._RECIPE_SYSTEM, max_t)
        if not raw:
            return None
        data = self._try_parse_json(raw)
        if not isinstance(data, dict):
            return None
        ops = data.get("ops")
        if not isinstance(ops, list) or not ops:
            return None
        for op in ops:
            if not isinstance(op, dict) or op.get("op") not in _VALID_OPS:
                return None
        return data

    def map_content(self, blocks, slots, hint=""):
        """초안 텍스트 블록 → 표준 슬롯 '배정'(문구 변경 없음).

        LLM 은 **인덱스만** 반환하고 실제 텍스트 값은 호출자가 blocks 에서 그대로(verbatim)
        가져온다 → 모델이 문구를 바꿀 여지가 없다.
        blocks: [{"i": int, "text": str}], slots: [{"key": str, "op": str}].
        반환: {"assign": {slot_key: block_index}} (유효 인덱스/키만) 또는 None.
        """
        if not self.can_use_cloud():
            return None
        if not blocks or not slots:
            return {"assign": {}}
        model = self.cloud.get("map_model") or self.cloud.get("classify_model", "claude-haiku-4-5")
        max_t = self.cloud.get("map_max_tokens", 800)
        raw = self._call_anthropic(model, self._map_prompt(blocks, slots, hint),
                                   self._MAP_SYSTEM, max_t)
        if not raw:
            return None
        data = self._try_parse_json(raw)
        if not isinstance(data, dict):
            return None
        assign = data.get("assign")
        if not isinstance(assign, dict):
            return None
        valid_idx = {b["i"] for b in blocks}
        valid_keys = {s["key"] for s in slots}
        out = {}
        for k, v in assign.items():
            if k in valid_keys and isinstance(v, int) and v in valid_idx:
                out[k] = v  # 값이 아니라 인덱스만 — 텍스트는 호출자가 verbatim 사용
        return {"assign": out}
