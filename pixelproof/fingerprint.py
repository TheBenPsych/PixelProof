"""Makes the two fingerprints for an image.

A: perceptual hash (64 on/off values). Catches straight copies, resized or recompressed.
B: CLIP (512 numbers from an AI model). Catches cropped, flipped, filtered or watermarked copies.
"""

from __future__ import annotations

import io
import os
import warnings

import numpy as np
from PIL import Image, ImageFile, ImageOps

from . import config

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 400_000_000  # big camera files are fine, stop absurd ones
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

try:  # iPhone HEIC photos, optional
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover
    pass

WORK_SIZE = 512  # we never need more pixels than this to fingerprint


def open_image(source: str | os.PathLike | bytes, fix_rotation: bool = True) -> tuple[Image.Image, tuple[int, int]]:
    """Open a file path or raw bytes. Returns (small RGB image, original (width, height))."""
    fp = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    img = Image.open(fp)
    original_size = img.size
    try:
        img.draft("RGB", (WORK_SIZE, WORK_SIZE))  # fast JPEG decode at reduced size
    except Exception:
        pass
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    if fix_rotation:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((WORK_SIZE, WORK_SIZE))
    return img, original_size


def phash(img: Image.Image) -> bytes:
    """64-bit perceptual hash, as 8 bytes."""
    import imagehash

    return np.packbits(imagehash.phash(img).hash.flatten()).tobytes()


def phash_pair(img: Image.Image) -> tuple[bytes, bytes]:
    """Hash of the image and of its mirror image (catches flipped copies). Same result as phash(), but the
    picture is shrunk once and the mirror is taken of the tiny 32x32 version (much faster)."""
    import scipy.fftpack

    px = np.asarray(img.convert("L").resize((32, 32), Image.Resampling.LANCZOS))

    def h(p):
        dct = scipy.fftpack.dct(scipy.fftpack.dct(p, axis=0), axis=1)[:8, :8]
        return np.packbits((dct > np.median(dct)).flatten()).tobytes()

    return h(px), h(np.ascontiguousarray(px[:, ::-1]))


def hash_distance(a: bytes, b: bytes) -> int:
    return int(np.unpackbits(np.frombuffer(a, np.uint8) ^ np.frombuffer(b, np.uint8)).sum())


def thumbnail_jpeg(img: Image.Image, size: int = 420) -> bytes:
    t = img.copy()
    t.thumbnail((size, size))
    out = io.BytesIO()
    t.save(out, "JPEG", quality=82)
    return out.getvalue()


class Clip:
    """The CLIP model. Uses the NVIDIA graphics card when there is one."""

    def __init__(self, model_name: str = config.CLIP_MODEL, pretrained: str | None = config.CLIP_PRETRAINED,
                 device: str | None = None, cache_dir: str | os.PathLike | None = None):
        import open_clip
        import torch

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=self.device,
            cache_dir=str(cache_dir) if cache_dir else None,
        )
        model.eval()
        self.half = self.device == "cuda"
        if self.half:
            model = model.half()
        self.model = model
        self.preprocess = preprocess
        self.dim = int(model.visual.output_dim)
        self.name = f"{model_name}/{pretrained}"
        self.fast = self._setup_fast()

    def _setup_fast(self) -> bool:
        """The quick way (pictures prepared by the helpers, see prep.py) is only used when it gives the same
        numbers as the AI's own preparation. Checked once on a test picture."""
        torch = self.torch
        try:
            from .prep import ai_pixels

            norm = next(t for t in self.preprocess.transforms if type(t).__name__ == "Normalize")
            self._mean = torch.tensor(norm.mean, dtype=torch.float32, device=self.device).view(1, 3, 1, 1)
            self._std = torch.tensor(norm.std, dtype=torch.float32, device=self.device).view(1, 3, 1, 1)
            rng = np.random.default_rng(1)
            test = Image.fromarray((rng.random((300, 450, 3)) * 255).astype(np.uint8))
            want = self.preprocess(test).unsqueeze(0).float()
            got = self._pixels_to_batch([ai_pixels(test)]).float().cpu()
            if self.half:   # compare at the precision that is actually used
                want = want.half().float()
            return bool(torch.allclose(got, want, atol=2e-2))
        except Exception:
            return False

    def _pixels_to_batch(self, arrs):
        torch = self.torch
        x = torch.from_numpy(np.stack(arrs)).to(self.device).permute(0, 3, 1, 2).float()
        x = (x / 255.0 - self._mean) / self._std
        return x.half() if self.half else x

    def embed_pixels(self, arrs: list) -> np.ndarray:
        """Like embed(), for pictures the helpers already prepared (224x224 pixels)."""
        torch = self.torch
        batch = self._pixels_to_batch(arrs)
        with torch.inference_mode():
            feats = self.model.encode_image(batch).float()
        feats = feats / feats.norm(dim=-1, keepdim=True).clamp_min(1e-6)
        return feats.cpu().numpy().astype(np.float32)

    def device_label(self) -> str:
        if self.device == "cuda":
            return "graphics card (" + self.torch.cuda.get_device_name(0) + ")"
        return "processor (no NVIDIA graphics card found, this is slower)"

    def embed_tensors(self, tensors: list) -> np.ndarray:
        torch = self.torch
        batch = torch.stack(tensors).to(self.device)
        if self.half:
            batch = batch.half()
        with torch.inference_mode():
            feats = self.model.encode_image(batch).float()
        feats = feats / feats.norm(dim=-1, keepdim=True).clamp_min(1e-6)
        return feats.cpu().numpy().astype(np.float32)

    def embed(self, images: list[Image.Image]) -> np.ndarray:
        return self.embed_tensors([self.preprocess(im) for im in images])


def same_photo_score(a: Image.Image, b: Image.Image, mirrored: bool = False) -> int | None:
    """Double-checks a "looks alike" hit: counts small details (corners, edges) that line up between the two
    pictures in the same layout. A crop, filter, logo or resize of the SAME photo keeps dozens of them; a
    different photo of the same person or scene has almost none. Returns None if the checker isn't installed."""
    try:
        import cv2
    except Exception:
        return None
    import numpy as np

    def gray(im):
        im = im.convert("L")
        im.thumbnail((WORK_SIZE, WORK_SIZE))
        return np.asarray(im)

    if mirrored:
        b = ImageOps.mirror(b)
    ga, gb = gray(a), gray(b)
    orb = cv2.ORB_create(nfeatures=2000, fastThreshold=10)
    ka, da = orb.detectAndCompute(ga, None)
    kb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return 0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [m for m, *n in pairs if n and m.distance < 0.8 * n[0].distance]
    if len(good) < 8:
        return 0
    src = np.float32([ka[m.queryIdx].pt for m in good])
    dst = np.float32([kb[m.trainIdx].pt for m in good])
    # a crop / resize / small rotation of the same photo: one "similarity" move explains all the details
    move, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=6.0)
    if move is None or inl is None:
        return 0
    keep = inl.ravel() == 1
    n = int(keep.sum())
    scale = float(np.hypot(move[0, 0], move[1, 0]))
    # Look-alike photos (same person, flags, stripes) can line up a few repeating details by chance, but:
    #  - only a small share of the matched details agree (same photo: 66-100%, look-alikes: 9-31%), so at least 50%
    #  - the agreeing details sit in one tiny spot of their picture, or the "move" squashes it to nothing
    if n / len(good) < 0.5 or not 0.1 <= scale <= 10:
        return 0

    def spread(pts, shape):
        return cv2.contourArea(cv2.convexHull(pts)) / float(shape[0] * shape[1]) if len(pts) >= 3 else 0.0

    if spread(src[keep], ga.shape) < 0.02:      # their picture: the same photo fills most of it
        return 0
    if overlap_likeness(ga, gb, move) < 0.5:     # laid on top of each other, the pictures must agree
        return 0
    return n


def overlap_likeness(ga, gb, move) -> float:
    """Lays our picture on top of theirs (using the move found above) and measures how alike the overlapping
    pixels are, from -1 to 1. The same photo, even recoloured or in black and white, scores high; a different
    photo that happens to share a few details scores near 0."""
    import cv2
    import numpy as np

    h, w = ga.shape
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    ours = cv2.warpAffine(gb, move, (w, h), flags=flags).astype(np.float32)
    inside = cv2.warpAffine(np.full(gb.shape, 255, np.uint8), move, (w, h), flags=flags) > 250
    if inside.sum() < 0.02 * w * h:
        return 0.0
    a = cv2.GaussianBlur(ga.astype(np.float32), (0, 0), 1.5)[inside]
    b = cv2.GaussianBlur(ours, (0, 0), 1.5)[inside]
    a, b = a - a.mean(), b - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den else 0.0
