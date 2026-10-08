"""Image providers for figures that are not logos or diagrams.

OpenAI, Gemini, Stability, Flux, and local ComfyUI share one call. Each stays
off until a key or a ComfyUI address is set. The same prompt, seed, and preset
are not sent twice: the second call returns the saved file. A brand preset
keeps the picture flat, on white, in navy and purple.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "workspace" / "images"
PRESETS = {
    "spark": "Flat illustration on a white background. Use only navy #1A1A2E and purple #6B4EFF. No gradients, no text, no letters, no logos.",
}
_REFUSE = re.compile(r"\b(logo|wordmark|flowchart|flow chart|org chart|architecture diagram|sequence diagram)\b", re.I)
_KEYS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "stability": "STABILITY_API_KEY",
    "flux": "FAL_KEY",
}


class ImageError(RuntimeError):
    pass


class Asset(BaseModel):
    path: str
    prompt: str
    styled_prompt: str
    seed: int
    provider: str
    preset: str
    cost_usd: float = 0.0
    license: str = "provider terms"
    cached: bool = False


def style_prompt(prompt: str, preset: str = "spark") -> str:
    if preset not in PRESETS:
        raise ImageError(f"unknown preset {preset}")
    return f"{prompt.strip()}\n\n{PRESETS[preset]}"


def openai_payload(prompt: str, preset: str = "spark") -> dict:
    return {"model": "gpt-image-1", "prompt": style_prompt(prompt, preset), "n": 1, "size": "1024x1024"}


def gemini_payload(prompt: str, preset: str = "spark") -> dict:
    return {"instances": [{"prompt": style_prompt(prompt, preset)}], "parameters": {"sampleCount": 1}}


def stability_fields(prompt: str, preset: str = "spark") -> dict:
    return {"prompt": style_prompt(prompt, preset), "output_format": "png", "aspect_ratio": "1:1"}


def flux_payload(prompt: str, seed: int, preset: str = "spark") -> dict:
    return {"prompt": style_prompt(prompt, preset), "seed": seed, "image_size": "square", "num_images": 1}


def comfy_payload(prompt: str, seed: int, preset: str = "spark") -> dict:
    return {"prompt": style_prompt(prompt, preset), "seed": seed}


def generate_image(
    prompt: str,
    *,
    provider: str = "openai",
    key: str = "",
    url: str = "",
    seed: int = 1,
    preset: str = "spark",
    cache_dir: Path | None = None,
    backend=None,
) -> Asset:
    text = " ".join(prompt.split())
    if not text:
        raise ImageError("the image needs a prompt")
    if _REFUSE.search(text):
        raise ImageError("Logos and diagrams are drawn by the document tools, not by an image model.")
    provider = provider.lower()
    styled = style_prompt(text, preset)
    folder = cache_dir or CACHE
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{provider}|{preset}|{seed}|{styled}".encode()).hexdigest()[:20]
    image_path = folder / f"{digest}.png"
    meta_path = folder / f"{digest}.json"
    if image_path.is_file() and meta_path.is_file():
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        saved["cached"] = True
        saved["path"] = str(image_path)
        return Asset(**saved)
    caller = backend or _backend(provider, key=key, url=url)
    png, cost = caller(text, seed, preset)
    if not png:
        raise ImageError("the provider returned an empty image")
    image_path.write_bytes(png)
    asset = Asset(
        path=str(image_path),
        prompt=text,
        styled_prompt=styled,
        seed=seed,
        provider=provider,
        preset=preset,
        cost_usd=float(cost or 0),
        license="provider terms",
        cached=False,
    )
    meta_path.write_text(asset.model_dump_json(indent=2), encoding="utf-8")
    return asset


def provider_status() -> list[dict]:
    rows = []
    for name, env in _KEYS.items():
        rows.append({"name": name, "configured": bool(os.environ.get(env))})
    rows.append({"name": "comfy", "configured": bool(os.environ.get("COMFY_URL"))})
    return rows


def _backend(provider: str, *, key: str, url: str):
    if provider == "comfy":
        address = url or os.environ.get("COMFY_URL", "")
        if not address:
            raise ImageError("ComfyUI is off until COMFY_URL is set")
        return lambda prompt, seed, preset: _comfy(address, prompt, seed, preset)
    if provider not in _KEYS:
        raise ImageError(f"unknown image provider {provider}")
    token = key or os.environ.get(_KEYS[provider], "")
    if not token:
        raise ImageError(f"{provider} image generation is off until a key is set")
    return lambda prompt, seed, preset: _remote(provider, token, prompt, seed, preset)


def _remote(provider: str, key: str, prompt: str, seed: int, preset: str) -> tuple[bytes, float]:
    if provider == "openai":
        data = _post_json("https://api.openai.com/v1/images/generations", openai_payload(prompt, preset), {"Authorization": f"Bearer {key}"})
        encoded = ((data.get("data") or [{}])[0]).get("b64_json") or ""
        return base64.b64decode(encoded), 0.0
    if provider == "gemini":
        data = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={key}",
            gemini_payload(prompt, preset),
            {},
        )
        encoded = ((data.get("predictions") or [{}])[0]).get("bytesBase64Encoded") or ""
        return base64.b64decode(encoded), 0.0
    if provider == "stability":
        raw = _post_json("https://api.stability.ai/v2beta/stable-image/generate/core", stability_fields(prompt, preset), {"Authorization": f"Bearer {key}", "Accept": "application/json"})
        encoded = raw.get("image") or ""
        return base64.b64decode(encoded), 0.0
    data = _post_json("https://fal.run/fal-ai/flux/dev", flux_payload(prompt, seed, preset), {"Authorization": f"Key {key}"})
    images = data.get("images") or []
    if images and images[0].get("url"):
        return _read_url(images[0]["url"]), 0.0
    raise ImageError("the provider returned no image")


def _comfy(url: str, prompt: str, seed: int, preset: str) -> tuple[bytes, float]:
    data = _post_json(url.rstrip("/") + "/prompt", comfy_payload(prompt, seed, preset), {})
    if data.get("image"):
        return base64.b64decode(data["image"]), 0.0
    raise ImageError("ComfyUI did not return an image. Check the workflow at that address.")


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    body = json.dumps(payload).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "SparkDocAgent", **headers})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _read_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "SparkDocAgent"})
    with urlopen(request, timeout=60) as response:
        return response.read()
