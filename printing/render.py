"""Render text and images to a printer-independent 1-bit bitmap.

Pillow is imported only when rendering is requested.
"""
from .document import ImageBlock, PrintDocument, TextBlock


def render_document_pages(document: PrintDocument, *, width: int = 384, max_height: int = 1800):
    """Render successive short paper sections without losing text."""
    from PIL import Image, ImageDraw, ImageFont

    if not 64 <= width <= 2048 or not 64 <= max_height <= 6000:
        raise ValueError("Invalid printing dimensions")
    scale = 3
    text_width = width // scale
    margin = 8
    line_height = 16
    font = ImageFont.load_default()
    scratch = ImageDraw.Draw(Image.new("L", (text_width, 1), 255))
    available = text_width - 2 * margin
    if available <= 0:
        raise ValueError("Insufficient text width")

    def wrap_text(text):
        result = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            if not words:
                result.append("")
                continue
            line = ""
            for word in words:
                candidate = f"{line} {word}" if line else word
                if scratch.textlength(candidate, font=font) <= available:
                    line = candidate
                    continue
                if line:
                    result.append(line)
                line = word
                while scratch.textlength(line, font=font) > available:
                    cut = len(line) - 1
                    while cut > 1 and scratch.textlength(line[:cut], font=font) > available:
                        cut -= 1
                    result.append(line[:cut])
                    line = line[cut:]
            result.append(line)
        return result

    pages = []
    strips = []
    height = 0

    def flush():
        nonlocal strips, height
        if not strips:
            return
        page = Image.new("L", (width, height), 255)
        y = 0
        for strip in strips:
            page.paste(strip, (0, y))
            y += strip.height
        pages.append(page.convert("1", dither=Image.Dither.FLOYDSTEINBERG))
        strips, height = [], 0

    def append(strip):
        nonlocal height
        if strip.height > max_height:
            raise ValueError("Image exceeds maximum print section height")
        if height + strip.height > max_height:
            flush()
        strips.append(strip)
        height += strip.height

    # Reserve margins on each section, not an entire 6000px-long receipt.
    lines_per_section = max(1, (max_height // scale - margin) // line_height)
    for block in (TextBlock(document.title), *document.blocks):
        if isinstance(block, TextBlock):
            lines = wrap_text(block.text)
            for offset in range(0, len(lines), lines_per_section):
                part = lines[offset:offset + lines_per_section]
                image = Image.new("L", (text_width, len(part) * line_height + margin), 255)
                draw = ImageDraw.Draw(image)
                for index, line in enumerate(part):
                    draw.text((margin, index * line_height), line, font=font, fill=0)
                append(image.resize((width, image.height * scale), Image.Resampling.NEAREST))
        elif isinstance(block, ImageBlock):
            with Image.open(block.path) as source:
                source = source.convert("L")
                image_height = max(1, round(source.height * width / source.width))
                if image_height > max_height:
                    raise ValueError("Image exceeds maximum print section height")
                append(source.resize((width, image_height), Image.Resampling.LANCZOS))
        else:
            raise TypeError(f"Unsupported block: {type(block).__name__}")
    flush()
    return pages


def render_document(document: PrintDocument, *, width: int = 384, max_height: int = 6000):
    """Single-bitmap compatibility API for the standalone smoke test."""
    from PIL import Image

    pages = render_document_pages(document, width=width, max_height=max_height)
    if len(pages) != 1:
        raise ValueError("Document requires multiple print sections; use render_document_pages")
    return pages[0]
