"""store.py — 파일 기반 job 저장소.

레이아웃:
  <store_dir>/<job_id>/manifest.json
  <store_dir>/<job_id>/out.pptx          (선택 — manifest.transform.out_pptx 폴백)
  <store_dir>/<job_id>/preview.png       (선택 — 캐시된 미리보기)
  <store_dir>/<job_id>/edited.pptx       (선택 — 직원 업로드 수정본)
  <store_dir>/<job_id>/diffs.json        (선택 — 수정본 diff 결과)
"""
import json
import os


def _default(o):
    if isinstance(o, set):
        return sorted(o)
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(f"unserializable type: {type(o).__name__}")


class JobStore:
    def __init__(self, store_dir: str):
        self.store_dir = os.path.abspath(store_dir)
        os.makedirs(self.store_dir, exist_ok=True)

    def job_dir(self, job_id: str) -> str:
        return os.path.join(self.store_dir, job_id)

    def manifest_path(self, job_id: str) -> str:
        return os.path.join(self.job_dir(job_id), "manifest.json")

    def exists(self, job_id: str) -> bool:
        return os.path.isfile(self.manifest_path(job_id))

    def read_manifest(self, job_id: str):
        p = self.manifest_path(job_id)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def save_manifest(self, job_id: str, manifest: dict) -> None:
        d = self.job_dir(job_id)
        os.makedirs(d, exist_ok=True)
        with open(self.manifest_path(job_id), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2, default=_default)

    def list_manifests(self):
        out = []
        if not os.path.isdir(self.store_dir):
            return out
        for name in sorted(os.listdir(self.store_dir)):
            p = os.path.join(self.store_dir, name, "manifest.json")
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        out.append(json.load(fh))
                except (OSError, json.JSONDecodeError):
                    continue
        return out

    def out_pptx_path(self, job_id: str):
        """manifest.transform.out_pptx 가 가리키면 그 경로, 아니면 job_dir/out.pptx."""
        man = self.read_manifest(job_id)
        if man:
            p = (man.get("transform") or {}).get("out_pptx")
            if p and os.path.exists(p):
                return p
        local = os.path.join(self.job_dir(job_id), "out.pptx")
        return local if os.path.exists(local) else None

    def preview_png_path(self, job_id: str):
        p = os.path.join(self.job_dir(job_id), "preview.png")
        return p if os.path.exists(p) else None

    def save_out_pptx_local(self, job_id: str, content: bytes) -> str:
        d = self.job_dir(job_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "out.pptx")
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def save_edited(self, job_id: str, content: bytes) -> str:
        d = self.job_dir(job_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "edited.pptx")
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def edited_pptx_path(self, job_id: str):
        p = os.path.join(self.job_dir(job_id), "edited.pptx")
        return p if os.path.exists(p) else None

    def save_promoted_recipe(self, job_id: str, recipe: dict) -> str:
        """승격된 AI 레시피 초안을 job 디렉터리에 보존(감사용 사본). 경로 반환."""
        d = self.job_dir(job_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "promoted_recipe.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(recipe, fh, ensure_ascii=False, indent=2, default=_default)
        return p

    def save_diffs(self, job_id: str, diffs) -> None:
        d = self.job_dir(job_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "diffs.json"), "w", encoding="utf-8") as fh:
            json.dump(diffs, fh, ensure_ascii=False, indent=2, default=_default)

    def read_diffs(self, job_id: str):
        p = os.path.join(self.job_dir(job_id), "diffs.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
