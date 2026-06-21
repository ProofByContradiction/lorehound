"""Generate a Discord profile banner for Lorehound, matching the icon theme.

Wide art in the established palette (deep navy, gold, ember) tying together the
two chosen icon directions (#5 painterly hero hound, #6 clean navy/gold emblem).
Saves to this folder with the correct extension for whatever the model returns.

    python assets/generate_banner.py
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent
KEY_FILE = ASSETS_DIR.parent.parent / "gemini_API.txt"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MODEL_CHAIN = ["gemini-3-pro-image-preview", "gemini-3-pro-image", "gemini-2.5-flash-image"]
# Discord profile banners are ~5:2; 21:9 is the closest the model offers, with
# 16:9 as a fallback. Reposition/crop on upload.
ASPECTS = ["21:9", "16:9"]

BANNERS = {
    "banner-hero-scene": (
        "A wide cinematic banner for a tabletop-RPG helper bot. On the right "
        "third, a wise hero dog — a bespectacled hound in a worn adventurer's "
        "leather coat with a softly glowing twenty-sided-die amulet — gazing "
        "thoughtfully across the scene. A sweeping painterly backdrop blends "
        "RPG genres left to right: a glowing fantasy sword and arcane runes, "
        "shifting into neon cyberpunk circuitry and distant city glow, then a "
        "field of stars and a faint nebula, with a subtle hint of "
        "post-apocalyptic haze. Unified palette of deep navy, gold and ember "
        "orange; dramatic rim lighting; rich and atmospheric. Keep the "
        "lower-left area calmer and less detailed. Absolutely no text, "
        "letters, words, numbers, or watermark."
    ),
    "banner-emblem": (
        "A wide banner in a bold, clean emblem style matching a navy-and-gold "
        "guild badge. A noble hound's-head emblem with crossed twenty-sided "
        "dice and an open book sits toward the right, with a decorative wide "
        "field of subtle tabletop-RPG iconography — dice, a sword, "
        "constellations, faint circuit lines — spread elegantly across deep "
        "navy with gold and ember accents. Flat graphic vector-like shapes, "
        "balanced and iconic. Keep the lower-left simple. No text, no "
        "letters, no words, no numbers, no watermark."
    ),
}


def load_key() -> str:
    return KEY_FILE.read_text().strip()


def ext_for(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "img"


def request_image(model: str, prompt: str, key: str, aspect: str) -> bytes | None:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect},
        },
    }
    req = urllib.request.Request(
        ENDPOINT.format(model=model, key=key),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"    [{model} {aspect}] HTTP {e.code}: {e.read().decode(errors='replace')[:160]}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"    [{model} {aspect}] error: {e}")
        return None
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


def generate(prompt: str, key: str) -> bytes | None:
    for model in MODEL_CHAIN:
        for aspect in ASPECTS:
            img = request_image(model, prompt, key, aspect)
            if img:
                print(f"    ✓ {model} @ {aspect}")
                return img
    return None


def main() -> None:
    key = load_key()
    for slug, prompt in BANNERS.items():
        print(f"{slug} …")
        img = generate(prompt, key)
        if not img:
            print(f"    ✗ no image for {slug}")
            continue
        out = ASSETS_DIR / f"{slug}.{ext_for(img)}"
        out.write_bytes(img)
        print(f"    saved {out.name} ({len(img) // 1024} KB)")


if __name__ == "__main__":
    main()
