"""Settings for PixelProof. Everything stays on this PC."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "PixelProof"
VERSION = "0.5.9"

# The app folder (the folder that holds install.bat). Data lives next to it in "data".
APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = APP_DIR / "data"

# Image types we read from your library.
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".tif", ".tiff",
    ".bmp", ".gif", ".heic", ".heif",
}

# AI fingerprint model (CLIP). Downloaded once on first use, then kept in data\models.
CLIP_MODEL = "ViT-B-32-quickgelu"  # the exact layout OpenAI trained this model with
CLIP_PRETRAINED = "openai"

# Matching rules (starting values, we will tune them on your real images).
EXACT_MAX_HASH_DIST = 8      # perceptual hash differs by 8 bits or fewer -> Exact copy
EDITED_MIN_CLIP = 0.92       # CLIP similarity 0.92 or higher -> Edited copy
# "Looks alike" hits are double-checked (fingerprint.same_photo_score): the small details (corners, edges) of
# the two pictures must line up, most matched details must agree, and when one picture is laid on top of the
# other the pixels must agree (0.79-1.0 for crops, recolours, banners, black and white of the same photo;
# -0.15 to 0.2 for the 15 look-alikes of Trump, flags and ICE vests the news feed found on 2026-11-07).
# A picture-hash hit (exact or flipped) with an AI similarity below this is double-checked too: dark, plain
# pictures can share a hash by chance (a flipped portrait matched a different person at 45% on 2026-11-07).
HASH_HIT_MIN_CLIP = 0.80
SAME_PHOTO_MIN_POINTS = 20
POSSIBLE_MIN_CLIP = 0.89     # CLIP similarity 0.89 to 0.92 -> Possible (0.85 let in other photos of the same person)

# Web images smaller than this (either side, in pixels) are skipped: icons, logos, buttons.
MIN_WEB_IMAGE_SIDE = 120
MAX_WEB_IMAGES_PER_PAGE = 300
MAX_WEB_IMAGE_BYTES = 30 * 1024 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 PixelProof/" + VERSION
)


def data_dir(override: str | os.PathLike | None = None) -> Path:
    path = Path(override or os.environ.get("PIXELPROOF_DATA") or DEFAULT_DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    (path / "reports").mkdir(exist_ok=True)
    (path / "models").mkdir(exist_ok=True)
    banned = path / "banned.txt"
    if not banned.exists():
        banned.write_text(
            "# Sites to hide from results, one per line (for example: mysite.com)\n"
            "# Lines starting with # are ignored.\n",
            encoding="utf-8",
        )
    from .sites import make_templates  # approved.txt and sister-sites.txt

    make_templates(path)
    return path
