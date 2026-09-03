"""Perceptual hashing for the double-financing guard.

Naive 8x8 average/difference hashes do not work on this problem. Every
invoice from one mill shares a letterhead, so at 8x8 two *different*
invoices look nearly identical while a re-photograph - rotated, blurred,
recompressed - drifts further away than they do. The first implementation
here flagged the wrong pair, which is the whole trap.

The fix is two-part:

  1. Normalise illumination *before* geometry. A harsher re-photograph is
     globally darker, and Otsu on a darkened page thresholds the paper
     itself as ink - mask coverage jumps from 0.10 to 0.51 and the crop
     grabs the whole rotated frame instead of the document. Flat-field
     correction (dividing by a heavily blurred background estimate) removes
     the gradient and the global shift, after which the deskew and crop
     land on the same content in both shots.
  2. Hash at a resolution that can actually see the variable fields. A
     256-bit DCT hash over a 32x32 low-frequency block keeps the amount,
     invoice number and QR block in the signature instead of averaging
     them away into the shared template.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

CANON = 256          # canonical page size before the DCT
DCT_KEEP = 16        # low-frequency block retained -> 16*16-1 = 255 bits
DUP_THRESHOLD = 22   # bits over 255; measured: same doc 14, nearest distinct pair 30


@lru_cache(maxsize=1)
def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n).reshape(-1, 1)
    x = np.arange(n).reshape(1, -1)
    m = np.cos(np.pi * (2 * x + 1) * k / (2 * n))
    m[0] *= np.sqrt(1 / n)
    m[1:] *= np.sqrt(2 / n)
    return m


def _flat_field(grey: np.ndarray) -> np.ndarray:
    """Divide out the illumination so paper reads as paper in every shot."""
    bg = cv2.GaussianBlur(grey.astype(np.float32), (0, 0), 51)
    corrected = (grey.astype(np.float32) / np.maximum(bg, 1e-3)) * 200.0
    return np.clip(corrected, 0, 255).astype(np.uint8)


def _deskew_and_crop(grey: np.ndarray) -> np.ndarray:
    """Straighten the page and crop to its content."""
    _, mask = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    pts = cv2.findNonZero(mask)
    if pts is None or len(pts) < 50:
        return grey

    (_, _), (w, h), angle = cv2.minAreaRect(pts)
    if angle < -45:
        angle += 90
    if abs(angle) > 15:          # a wild angle means the mask found noise
        angle = 0.0
    rot = cv2.getRotationMatrix2D(
        (grey.shape[1] / 2, grey.shape[0] / 2), angle, 1.0)
    straight = cv2.warpAffine(grey, rot, (grey.shape[1], grey.shape[0]),
                              flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)

    smask = cv2.warpAffine(mask, rot, (mask.shape[1], mask.shape[0]),
                           flags=cv2.INTER_NEAREST)
    pts = cv2.findNonZero(smask)
    if pts is None:
        return straight
    x, y, w, h = cv2.boundingRect(pts)
    if w < 40 or h < 40:
        return straight
    return straight[y:y + h, x:x + w]


def normalise(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read image: {path}")
    page = _deskew_and_crop(_flat_field(img))
    page = cv2.resize(page, (CANON, CANON), interpolation=cv2.INTER_AREA)
    # CLAHE removes the lighting gradient a phone camera adds
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(page)


def phash(path: str | Path) -> str:
    px = normalise(path).astype(np.float64)
    m = _dct_matrix(CANON)
    coeffs = m @ px @ m.T
    block = coeffs[:DCT_KEEP, :DCT_KEEP].flatten()[1:]   # drop DC
    bits = block > np.median(block)
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return f"{value:064x}"


# Public names kept stable for callers.
def combined(path: str | Path) -> str:
    return phash(path)


def hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 9999
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def is_duplicate(a: str, b: str, threshold: int = DUP_THRESHOLD) -> tuple[bool, int]:
    d = hamming(a, b)
    return d <= threshold, d
