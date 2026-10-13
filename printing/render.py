"""Render text and images to a printer-independent 1-bit bitmap.

Pillow is imported only when rendering is requested.
"""
from .document import ImageBlock, PrintDocument, TextBlock


def render_document(document: PrintDocument, *, width: int = 384, max_height: int = 6000):
    from PIL import Image, ImageDraw, ImageFont

    if not 64 <= width <= 2048 or max_height < 64:
        raise ValueError("Invalid printing dimensions")
    font = ImageFont.load_default()
    text_scale = 3
    text_width = width // text_scale
    line_height = 16
    margin = 8
    strips = []

    def text_lines(text):
        scratch = Image.new("L", (text_width, 1), 255)
        draw = ImageDraw.Draw(scratch)
        result = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            if not words:
                result.append("")
                continue
            line = ""
            for word in words:
                candidate = f"{line} {word}" if line else word
                if draw.textlength(candidate, font=font) <= text_width - 2 * margin:
                    line = candidate
                else:
                    if line:
                        result.append(line)
                    line = word
                    while draw.textlength(line, font=font) > text_width - 2 * margin:
                        cut = len(line) - 1
                        while cut > 1 and draw.textlength(line[:cut], font=font) > text_width - 2 * margin:
                            cut -= 1
                        result.append(line[:cut])
                        line = line[cut:]
            result.append(line)
        return result

    for block in (TextBlock(document.title), *document.blocks):
        if isinstance(block, TextBlock):
            lines = text_lines(block.text)
            image = Image.new("L", (text_width, max(1, len(lines)) * line_height + margin), 255)
            draw = ImageDraw.Draw(image)
            for i, line in enumerate(lines):
                draw.text((margin, i * line_height), line, font=font, fill=0)
            strips.append(image.resize((width, image.height * text_scale), Image.Resampling.NEAREST))
        elif isinstance(block, ImageBlock):
            with Image.open(block.path) as source:
                source = source.convert("L")
                height = max(1, round(source.height * width / source.width))
                if height > max_height:
                    raise ValueError("Image exceeds maximum print height")
                strips.append(source.resize((width, height), Image.Resampling.LANCZOS))
        else:
            raise TypeError(f"Unsupported block: {type(block).__name__}")

    total_height = sum(strip.height for strip in strips)
    if total_height > max_height:
        raise ValueError("Document exceeds maximum print height")
    page = Image.new("L", (width, total_height), 255)
    y = 0
    for strip in strips:
        page.paste(strip, (0, y))
        y += strip.height
    return page.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
