"""Build Centrelink evidence PDFs from Quick Apply screenshots. Pure code: no LLM, no DB.

Centrelink takes one file per upload (PDF, DOCX, RTF, JPG, PNG or DOC, under 3 MB) and asks
how many applications it covers, so one PDF holds several screenshots, one per page, each
with a caption (date applied, job title, employer, Seek job link) drawn above it.

Size: a Seek "application sent" page is ~90 KB as a JPEG page at quality 80, so ~29 fit.
Pages are packed greedily by their measured size up to ``TARGET_BYTES`` (headroom under the
3 MB limit), and a built PDF that still comes out over ``LIMIT_BYTES`` is split in half, so
no file handed to the user is ever too big to upload.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LIMIT_BYTES = 3_000_000   # Centrelink's "under 3 MB", read as the smaller (decimal) megabyte
TARGET_BYTES = 2_800_000  # what the packer aims for
JPEG_QUALITY = 80
PAGE_WIDTH = 1200         # screenshots are scaled down to this; captions are drawn to fit
_RESOLUTION = 150.0       # dpi written into the PDF: 1200 px = 8 inches wide

_CAPTION_PAD = 16
_LINE_GAP = 6


@dataclass(frozen=True)
class EvidencePage:
    image_path: Path
    caption: tuple[str, ...]  # one line each; the first is drawn bold-ish (larger)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    """``text`` cut with an ellipsis so it fits in ``width`` pixels."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def render_page(page: EvidencePage) -> Image.Image:
    """The screenshot (RGB, at most ``PAGE_WIDTH`` wide) under a white caption band."""
    with Image.open(page.image_path) as src:
        shot = src.convert("RGB")
    if shot.width > PAGE_WIDTH:
        shot = shot.resize(
            (PAGE_WIDTH, max(1, round(shot.height * PAGE_WIDTH / shot.width))), Image.LANCZOS
        )
    width = shot.width
    fonts = [_font(26)] + [_font(20)] * max(0, len(page.caption) - 1)
    heights = [f.getbbox("Ag")[3] for f in fonts]
    band = _CAPTION_PAD * 2 + sum(heights) + _LINE_GAP * max(0, len(heights) - 1) + 2

    out = Image.new("RGB", (width, band + shot.height), "white")
    draw = ImageDraw.Draw(out)
    y = _CAPTION_PAD
    for line, font, h in zip(page.caption, fonts, heights):
        draw.text((_CAPTION_PAD, y), _fit(draw, line, font, width - 2 * _CAPTION_PAD),
                  font=font, fill=(17, 24, 39))
        y += h + _LINE_GAP
    draw.line([(0, band - 2), (width, band - 2)], fill=(156, 163, 175), width=2)
    out.paste(shot, (0, band))
    return out


def _pdf_bytes(images: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    images[0].save(
        buf, "PDF", save_all=True, append_images=images[1:],
        quality=JPEG_QUALITY, resolution=_RESOLUTION,
        title="Job application evidence", producer="Job Application Assistant",
    )
    return buf.getvalue()


def build_bundles(pages: list[EvidencePage]) -> list[tuple[list[EvidencePage], bytes]]:
    """Group ``pages`` (in order) into PDFs each under ``LIMIT_BYTES``.

    Returns ``(pages in this PDF, PDF bytes)`` per file; usually exactly one.
    """
    if not pages:
        return []
    rendered = [render_page(p) for p in pages]
    sizes = [len(_pdf_bytes([img])) for img in rendered]

    groups: list[list[int]] = [[]]
    total = 0
    for i, size in enumerate(sizes):
        if groups[-1] and total + size > TARGET_BYTES:
            groups.append([])
            total = 0
        groups[-1].append(i)
        total += size

    out: list[tuple[list[EvidencePage], bytes]] = []

    def emit(idx: list[int]) -> None:
        data = _pdf_bytes([rendered[i] for i in idx])
        if len(data) > LIMIT_BYTES and len(idx) > 1:
            half = len(idx) // 2
            emit(idx[:half])
            emit(idx[half:])
            return
        out.append(([pages[i] for i in idx], data))

    for group in groups:
        emit(group)
    return out
