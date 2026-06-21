"""Build a Discord banner around the chosen Lorehound icon.

Feeds `lorehound_icon.jpg` to the model as a reference image so the emblem on
the banner is the SAME hound/crest, then fills the rest in the matching
navy/gold emblem style — no second dog invented. Saves two takes here.

    python assets/generate_banner_from_icon.py
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent
KEY_FILE = ASSETS_DIR.parent.parent / "gemini_API.txt"
REF_IMAGE = ASSETS_DIR / "lorehound_icon.jpg"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MODEL_CHAIN = ["gemini-3-pro-image-preview", "gemini-3-pro-image", "gemini-2.5-flash-image"]
ASPECTS = ["21:9", "16:9"]

BASE_PROMPT = (
    "Use the attached circular crest emblem EXACTLY as provided (a noble "
    "hound's head over crossed twenty-sided dice and an open book, in deep "
    "navy and gold) as the single focal logo. Design a wide horizontal banner "
    "for a tabletop-RPG helper bot: place that emblem, unchanged, on the right "
    "third of the banner. Fill the rest with a decorative field of subtle, "
    "tasteful tabletop-RPG iconography — small twenty-sided dice, swords, "
    "compass roses, constellations, gears and faint circuit traces — in muted "
    "gold and ember line-work on the same deep-navy background, with a thin "
    "gold border frame around the whole banner. Match the emblem's flat, bold, "
    "graphic vector style and its navy/gold/ember palette so it all looks like "
    "one cohesive design. Keep the lower-left area calm and mostly empty (an "
    "avatar will sit there). IMPORTANT: do NOT add, draw or invent any other "
    "dog, animal, character or face — the ONLY canine anywhere is the one "
    "inside the attached emblem. No text, no letters, no words, no numbers, "
    "no watermark, no signature."
)

# Two distinct takes so there's a choice.
VARIANTS = {
    "banner-icon-1": "Balanced composition with the decorative iconography evenly spread.",
    "banner-icon-2": "Fewer, larger motifs with more open negative space and a cleaner, more minimal field.",
}


def load_key() -> str:
    return KEY_FILE.read_text().strip()


def ext_for(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "img"


def request_image(model: str, prompt: str, ref_b64: str, key: str, aspect: str) -> bytes | None:
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inlineData": {"mimeType": "image/jpeg", "data": ref_b64}},
                    {"text": prompt},
                ],
            }
        ],
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


def generate(prompt: str, ref_b64: str, key: str) -> bytes | None:
    for model in MODEL_CHAIN:
        for aspect in ASPECTS:
            img = request_image(model, prompt, ref_b64, key, aspect)
            if img:
                print(f"    ✓ {model} @ {aspect}")
                return img
    return None


def main() -> None:
    key = load_key()
    ref_b64 = base64.b64encode(REF_IMAGE.read_bytes()).decode()
    for slug, extra in VARIANTS.items():
        print(f"{slug} …")
        img = generate(f"{BASE_PROMPT}\n\n{extra}", ref_b64, key)
        if not img:
            print(f"    ✗ no image for {slug}")
            continue
        out = ASSETS_DIR / f"{slug}.{ext_for(img)}"
        out.write_bytes(img)
        print(f"    saved {out.name} ({len(img) // 1024} KB)")


if __name__ == "__main__":
    main()
