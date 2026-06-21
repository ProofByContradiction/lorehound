"""Generate Lorehound bot-icon concepts with Google's Gemini image models.

Uses the Gemini Developer API key in ../../gemini_API.txt (the same key vextty
uses for sprite art). Each concept is saved as a PNG in this assets/ folder.
Re-run to regenerate; tweak CONCEPTS to add/adjust ideas.

    python assets/generate_icons.py            # all concepts
    python assets/generate_icons.py fantasy     # only slugs containing "fantasy"
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.request
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent
KEY_FILE = ASSETS_DIR.parent.parent / "gemini_API.txt"  # .../Coding Work/gemini_API.txt
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

# Best quality first; fall back if a model is unavailable to the key.
MODEL_CHAIN = [
    "gemini-3-pro-image-preview",   # nano-banana-pro
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",       # nano-banana
]

# Shared framing so every concept reads as a clean Discord avatar/server icon.
STYLE = (
    "Square 1:1 app icon / profile avatar. A single dog character, centered, "
    "with a bold clear silhouette that stays readable when shrunk small and "
    "cropped to a circle. Expressive, characterful eyes; polished digital "
    "illustration; rich color and strong rim lighting. Absolutely no text, "
    "no letters, no words, no watermark, no signature."
)

CONCEPTS = {
    "fantasy-loremaster": (
        "A wise, scholarly dog as a fantasy loremaster: a noble grey-muzzled "
        "hound wearing small round brass spectacles and a deep-blue wizard's "
        "robe with arcane gold trim. He holds a glowing open tome; a "
        "translucent purple twenty-sided die floats above his paw shedding "
        "magical light. Warm candlelit ancient-library background with soft "
        "bokeh. Dungeons & Dragons high-fantasy mood."
    ),
    "cyberpunk-netrunner": (
        "A sharp, clever dog as a cyberpunk netrunner: a sleek shepherd with "
        "one glowing cybernetic eye and a holographic AR visor, wearing a "
        "high-collar techwear jacket. Neon magenta and cyan rim light, "
        "floating holographic dice and data glyphs, a rainy neon night-city "
        "skyline behind. Shadowrun / Cyberpunk aesthetic."
    ),
    "twilight2000-survivalist": (
        "A rugged, intelligent dog as a post-apocalyptic military "
        "survivalist: a battle-worn shepherd in faded olive-drab field "
        "fatigues and a battered helmet, dog tags at the neck, a folded "
        "tactical map and worn ten-sided dice beside him. Muted desaturated "
        "palette, overcast war-torn backdrop, grit and realism. Twilight "
        "2000 wartime-survival mood."
    ),
    "traveller-spacer": (
        "A curious, brainy dog as a retro sci-fi star-traveller: a beagle in "
        "a sleek explorer flight suit with a clear bubble helmet, studying a "
        "glowing holographic star chart, a chrome twelve-sided die orbiting "
        "like a tiny moon. Deep-space nebula and starfield background, warm "
        "console glow on the face. Classic Traveller science-fiction RPG vibe."
    ),
    "brand-mashup-hero": (
        "A wise hero dog that blends tabletop RPG genres into one iconic "
        "mascot: a confident hound with small round glasses and an "
        "adventurer's leather coat, a glowing twenty-sided-die amulet at the "
        "chest. Subtle genre motifs woven into a background halo: a fantasy "
        "sword, a glowing circuit pattern, a star map, and a faint gas-mask "
        "silhouette, all balanced and tasteful. Heroic, friendly, painterly, "
        "centered emblem composition."
    ),
    "crest-emblem": (
        "A bold emblem-style logo of a noble hound's head facing forward, "
        "framed in a circular crest, over two crossed twenty-sided dice and "
        "an open book. Flat graphic vector-like design with strong shapes and "
        "a limited rich palette of deep navy, gold and ember orange. Minimal, "
        "iconic, instantly readable at tiny sizes like a guild badge."
    ),
}


def load_key() -> str:
    return KEY_FILE.read_text().strip()


def request_image(model: str, prompt: str, key: str) -> bytes | None:
    """POST one prompt; return PNG bytes, or None if this model gave no image."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "1:1"},
        },
    }
    url = ENDPOINT.format(model=model, key=key)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        print(f"    [{model}] HTTP {e.code}: {detail}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"    [{model}] error: {e}")
        return None

    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    # No image — surface any text the model returned (often a refusal reason).
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if part.get("text"):
                print(f"    [{model}] text-only reply: {part['text'][:160]}")
    return None


def generate(prompt: str, key: str) -> bytes | None:
    full = f"{STYLE}\n\n{prompt}"
    for model in MODEL_CHAIN:
        png = request_image(model, full, key)
        if png:
            print(f"    ✓ {model}")
            return png
    return None


def main() -> None:
    key = load_key()
    name_filter = sys.argv[1] if len(sys.argv) > 1 else ""
    saved = []
    for i, (slug, prompt) in enumerate(CONCEPTS.items(), start=1):
        if name_filter and name_filter not in slug:
            continue
        print(f"[{i}] {slug} …")
        png = generate(prompt, key)
        if not png:
            print(f"    ✗ no image for {slug}")
            continue
        out = ASSETS_DIR / f"icon_{i}_{slug}.png"
        out.write_bytes(png)
        saved.append(out.name)
        print(f"    saved {out.name} ({len(png) // 1024} KB)")

    print("\nDone. Saved:")
    for name in saved:
        print(f"  • {name}")


if __name__ == "__main__":
    main()
