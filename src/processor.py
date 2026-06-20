"""
Image processor for pixel art conversion.
Handles background removal, colour removal, palette reduction,
pixel snapping (upscale detection), and .aseprite file generation.
"""

from __future__ import annotations

import io
import math
import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image
import numpy as np


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProcessConfig:
    """All knobs exposed to the user via the Discord button panel."""
    remove_bg: bool = False
    bg_tolerance: int = 80          # 0-255 flood-fill tolerance
    transparency_threshold: int = 12
    remove_colours: list[tuple[int, int, int]] = field(default_factory=list)  # list of RGB tuples
    colour_tolerance: int = 30      # euclidean distance for colour removal
    max_colours: int = 256          # palette compression (0 = no limit)
    output_width: int = 0           # 0 = auto-detect
    output_height: int = 0
    trim_edges: bool = False
    crop_padding: int = 1
    pixel_snap: bool = False        # detect and snap to logical pixel grid
    resampling: str = "nearest"     # nearest | bilinear | lanczos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Parse #RRGGBB or RRGGBB into (R, G, B)."""
    h = hex_str.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex colour: {hex_str!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _colour_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((int(x) - int(y)) ** 2 for x, y in zip(a, b)))


def _resample_filter(mode: str):
    mapping = {
        "nearest": Image.NEAREST,
        "bilinear": Image.BILINEAR,
        "lanczos": Image.LANCZOS,
    }
    return mapping.get(mode, Image.NEAREST)


# ---------------------------------------------------------------------------
# Step 1 – Background removal (edge-connected flood fill)
# ---------------------------------------------------------------------------

def remove_background(img: Image.Image, tolerance: int, alpha_threshold: int) -> Image.Image:
    """Remove edge-connected background pixels using flood fill."""
    img = img.convert("RGBA")
    arr = np.array(img, dtype=np.uint8)
    h, w = arr.shape[:2]

    # Seed colour = corner pixel (most common convention)
    seed_rgb = tuple(arr[0, 0, :3])

    visited = np.zeros((h, w), dtype=bool)
    to_visit = []

    # Seed from all four edges
    for y in range(h):
        for x in (0, w - 1):
            if not visited[y, x]:
                visited[y, x] = True
                to_visit.append((y, x))
    for x in range(w):
        for y in (0, h - 1):
            if not visited[y, x]:
                visited[y, x] = True
                to_visit.append((y, x))

    mask = np.zeros((h, w), dtype=bool)

    while to_visit:
        y, x = to_visit.pop()
        pixel = arr[y, x]
        if pixel[3] < alpha_threshold:
            mask[y, x] = True
            continue
        dist = _colour_distance(tuple(pixel[:3]), seed_rgb)
        if dist <= tolerance:
            mask[y, x] = True
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx]:
                    visited[ny, nx] = True
                    to_visit.append((ny, nx))

    arr[mask, 3] = 0
    return Image.fromarray(arr, "RGBA")


# ---------------------------------------------------------------------------
# Step 2 – Remove specific colours
# ---------------------------------------------------------------------------

def remove_colours(img: Image.Image, colours: list[tuple[int, int, int]], tolerance: int) -> Image.Image:
    img = img.convert("RGBA")
    arr = np.array(img, dtype=np.uint8)
    rgb = arr[:, :, :3].astype(np.int32)

    for target in colours:
        t = np.array(target, dtype=np.int32)
        diff = rgb - t
        dist = np.sqrt((diff ** 2).sum(axis=2))
        mask = dist <= tolerance
        arr[mask, 3] = 0

    return Image.fromarray(arr, "RGBA")


# ---------------------------------------------------------------------------
# Step 3 – Detect logical pixel grid size
# ---------------------------------------------------------------------------

def detect_pixel_size(img: Image.Image) -> int:
    """
    Detect the upscale factor of pixel art using run-length analysis.
    Looks for the GCD of run lengths of equal colours across scanlines.
    Returns the most likely integer pixel size (1 if no upscale detected).
    """
    gray = np.array(img.convert("L"), dtype=np.uint8)
    h, w = gray.shape

    run_lengths = []

    # Sample horizontal runs
    for y in range(0, h, max(1, h // 20)):
        row = gray[y]
        if len(row) < 2:
            continue
        current = row[0]
        run = 1
        for x in range(1, len(row)):
            if row[x] == current:
                run += 1
            else:
                run_lengths.append(run)
                current = row[x]
                run = 1
        run_lengths.append(run)

    # Sample vertical runs
    for x in range(0, w, max(1, w // 20)):
        col = gray[:, x]
        current = col[0]
        run = 1
        for y in range(1, len(col)):
            if col[y] == current:
                run += 1
            else:
                run_lengths.append(run)
                current = col[y]
                run = 1
        run_lengths.append(run)

    if not run_lengths:
        return 1

    from math import gcd
    from functools import reduce

    # Find GCD of all run lengths
    common = reduce(gcd, run_lengths)

    # Only trust sizes 2–16 that make sense for pixel art
    if 2 <= common <= 16:
        return common
    # If GCD is 1 or very large, find the most common small factor
    for size in (2, 3, 4, 6, 8, 12, 16):
        aligned = sum(1 for r in run_lengths if r % size == 0)
        if aligned / len(run_lengths) > 0.8:
            return size

    return 1


# ---------------------------------------------------------------------------
# Step 4 – Snap to logical pixel grid
# ---------------------------------------------------------------------------

def snap_to_pixel_grid(img: Image.Image, pixel_size: int) -> Image.Image:
    """Downscale to logical pixels using the detected pixel size."""
    if pixel_size <= 1:
        return img
    w = max(1, img.width // pixel_size)
    h = max(1, img.height // pixel_size)
    return img.resize((w, h), Image.NEAREST)


# ---------------------------------------------------------------------------
# Step 5 – Palette reduction
# ---------------------------------------------------------------------------

def reduce_palette(img: Image.Image, max_colours: int) -> Image.Image:
    if max_colours <= 0 or max_colours >= 256:
        return img
    img_rgba = img.convert("RGBA")
    # FAST_OCTREE (method=2) supports RGBA; MEDIANCUT does not
    try:
        quantized = img_rgba.quantize(colors=max_colours, method=Image.Quantize.FASTOCTREE, dither=0)
        return quantized.convert("RGBA")
    except Exception:
        # Fallback: convert to RGB, quantize, paste alpha back
        rgb = img_rgba.convert("RGB")
        alpha = img_rgba.split()[3]
        quantized = rgb.quantize(colors=max_colours, method=Image.Quantize.MEDIANCUT, dither=0)
        result = quantized.convert("RGBA")
        result.putalpha(alpha)
        return result


# ---------------------------------------------------------------------------
# Step 6 – Trim and pad
# ---------------------------------------------------------------------------

def trim_and_pad(img: Image.Image, padding: int) -> Image.Image:
    bbox = img.getbbox()
    if bbox is None:
        return img
    img = img.crop(bbox)
    if padding > 0:
        new_w = img.width + padding * 2
        new_h = img.height + padding * 2
        padded = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
        padded.paste(img, (padding, padding))
        return padded
    return img


# ---------------------------------------------------------------------------
# Step 7 – Resize to output dimensions
# ---------------------------------------------------------------------------

def resize_output(img: Image.Image, width: int, height: int, resample: str) -> Image.Image:
    if width <= 0 and height <= 0:
        return img
    filt = _resample_filter(resample)
    if width > 0 and height > 0:
        return img.resize((width, height), filt)
    if width > 0:
        ratio = width / img.width
        return img.resize((width, max(1, int(img.height * ratio))), filt)
    ratio = height / img.height
    return img.resize((max(1, int(img.width * ratio)), height), filt)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def process_image(src_bytes: bytes, cfg: ProcessConfig) -> tuple[Image.Image, dict]:
    """
    Run the full processing pipeline.
    Returns (processed_image, info_dict).
    """
    img = Image.open(io.BytesIO(src_bytes)).convert("RGBA")
    original_size = img.size
    info: dict = {"original_size": original_size, "steps": []}

    if cfg.remove_bg:
        img = remove_background(img, cfg.bg_tolerance, cfg.transparency_threshold)
        info["steps"].append("Background removed")

    if cfg.remove_colours:
        img = remove_colours(img, cfg.remove_colours, cfg.colour_tolerance)
        info["steps"].append(f"Removed {len(cfg.remove_colours)} colour(s)")

    pixel_size = 1
    if cfg.pixel_snap:
        pixel_size = detect_pixel_size(img)
        if pixel_size > 1:
            img = snap_to_pixel_grid(img, pixel_size)
            info["steps"].append(f"Snapped to {pixel_size}px logical grid → {img.size}")

    if cfg.max_colours < 256:
        img = reduce_palette(img, cfg.max_colours)
        info["steps"].append(f"Palette reduced to {cfg.max_colours} colours")

    if cfg.trim_edges:
        img = trim_and_pad(img, cfg.crop_padding)
        info["steps"].append("Trimmed transparent edges")

    if cfg.output_width > 0 or cfg.output_height > 0:
        img = resize_output(img, cfg.output_width, cfg.output_height, cfg.resampling)
        info["steps"].append(f"Resized to {img.size}")

    info["final_size"] = img.size
    info["pixel_size"] = pixel_size
    return img, info


# ---------------------------------------------------------------------------
# .aseprite writer (single-layer, RGBA or indexed)
# ---------------------------------------------------------------------------
# Minimal but valid .aseprite v1.3 writer.
# Spec: https://github.com/aseprite/aseprite/blob/main/docs/ase-file-specs.md

_MAGIC = 0xA5E0
_FRAME_MAGIC = 0xF1FA
_CHUNK_LAYER = 0x2004
_CHUNK_CEL = 0x2005
_CHUNK_COLOR_PROFILE = 0x2007
_PIXEL_RGBA = 32
_PIXEL_INDEXED = 8


def _write_u8(v: int) -> bytes:
    return struct.pack("<B", v & 0xFF)

def _write_u16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)

def _write_u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)

def _write_i16(v: int) -> bytes:
    return struct.pack("<h", v)

def _write_string(s: str) -> bytes:
    enc = s.encode("utf-8")
    return _write_u16(len(enc)) + enc


def image_to_aseprite(img: Image.Image) -> bytes:
    """Convert a PIL Image (RGBA) to .aseprite bytes."""
    img = img.convert("RGBA")
    w, h = img.size

    raw_pixels = img.tobytes()  # RGBA row-major
    compressed = zlib.compress(raw_pixels, level=6)

    # --- Build layer chunk ---
    layer_data = (
        _write_u16(0)          # flags (visible)
        + _write_u16(0)        # layer type = normal
        + _write_u16(0)        # layer child level
        + _write_u16(0)        # default layer width (ignored)
        + _write_u16(0)        # default layer height (ignored)
        + _write_u16(0)        # blend mode = normal
        + _write_u8(255)       # opacity
        + bytes(3)             # future (reserved)
        + _write_string("Layer")
    )
    layer_chunk = _write_u32(6 + len(layer_data)) + _write_u16(_CHUNK_LAYER) + layer_data

    # --- Color profile chunk (sRGB) ---
    cp_data = (
        _write_u16(1)          # sRGB
        + _write_u16(0)        # no special fixed gamma
        + _write_u32(0)        # fixed gamma = 0 (unused)
        + bytes(8)             # reserved
    )
    cp_chunk = _write_u32(6 + len(cp_data)) + _write_u16(_CHUNK_COLOR_PROFILE) + cp_data

    # --- Cel chunk (compressed image data) ---
    cel_header = (
        _write_u16(0)          # layer index
        + _write_i16(0)        # x position
        + _write_i16(0)        # y position
        + _write_u8(255)       # opacity
        + _write_u16(2)        # cel type = compressed image
        + _write_i16(0)        # z-index
        + bytes(5)             # reserved
        + _write_u16(w)        # width in pixels
        + _write_u16(h)        # height in pixels
    )
    cel_data = cel_header + compressed
    cel_chunk = _write_u32(6 + len(cel_data)) + _write_u16(_CHUNK_CEL) + cel_data

    # --- Frame ---
    frame_chunks = layer_chunk + cp_chunk + cel_chunk
    num_chunks = 3
    frame_duration_ms = 100

    frame_body = (
        _write_u32(0)          # old num chunks placeholder
        + _write_u16(frame_duration_ms)
        + bytes(2)             # reserved
        + _write_u32(num_chunks)
        + frame_chunks
    )
    frame_size = 16 + len(frame_chunks)
    frame = _write_u32(frame_size) + _write_u16(_FRAME_MAGIC) + frame_body

    # --- Header ---
    num_frames = 1
    color_depth = _PIXEL_RGBA   # 32 = RGBA
    header = (
        _write_u32(0)                  # file size placeholder — patched below
        + _write_u16(_MAGIC)
        + _write_u16(num_frames)
        + _write_u16(w)
        + _write_u16(h)
        + _write_u16(color_depth)
        + _write_u32(1)                # flags (layer opacity valid)
        + _write_u16(frame_duration_ms)
        + _write_u32(0)                # reserved
        + _write_u32(0)                # reserved
        + _write_u8(0)                 # transparent palette entry
        + bytes(3)                     # ignore these
        + _write_u16(0)                # number of colors (0 = 256 for indexed; ignored for RGBA)
        + _write_u8(1)                 # pixel width ratio
        + _write_u8(1)                 # pixel height ratio
        + _write_i16(0)                # grid x
        + _write_i16(0)                # grid y
        + _write_u16(16)               # grid width
        + _write_u16(16)               # grid height
        + bytes(84)                    # reserved
    )
    assert len(header) == 128, f"Header length = {len(header)}, expected 128"

    file_bytes = header + frame
    file_size = len(file_bytes)
    # Patch file size at offset 0
    file_bytes = struct.pack("<I", file_size) + file_bytes[4:]

    return file_bytes


# ---------------------------------------------------------------------------
# Convenience: process + export both PNG and .aseprite
# ---------------------------------------------------------------------------

def process_and_export(src_bytes: bytes, cfg: ProcessConfig) -> tuple[bytes, bytes, dict]:
    """
    Returns (png_bytes, aseprite_bytes, info_dict).
    """
    img, info = process_image(src_bytes, cfg)

    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    ase_bytes = image_to_aseprite(img)

    return png_bytes, ase_bytes, info


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    return _hex_to_rgb(hex_str)