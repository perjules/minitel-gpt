"""Compare effective raster text resolutions on the MXW01 (one strip per run)."""
import argparse
import asyncio

from PIL import Image, ImageDraw, ImageFont

from printing.mxw01 import MXW01

WIDTHS = (48, 64, 96, 128, 192, 256, 384)
TEXT = (
    "3615 IA — ESSAI TYPOGRAPHIQUE\n"
    "À Lyon, l'été était déjà terminé. Élise prépare un café, "
    "vérifie les températures : 18,5 °C et 1013 hPa.\n\n"
    "Le vieux terminal affiche : « Bonjour ! » — puis une réponse "
    "pleine d'accents, de chiffres (0123456789), et de ponctuation.\n\n"
    "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ\n"
    "La qualité des caractères, des courbes et des espaces "
    "doit rester agréable à lire sur papier thermique.\n\n"
) * 2

def make_strip(width):
    # Render at effective width, then nearest-neighbour scale to printer dots.
    # Physical text size is kept approximately constant across variants.
    scale = WIDTHS[-1] / width
    font_size = max(5, round(30 / scale))
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
    margin = max(2, round(18 / scale))
    line_height = max(7, round(42 / scale))
    draw = ImageDraw.Draw(Image.new("L", (width, 1), 255))
    lines = [f"RESOLUTION {width}/384 ({scale:g}x)"]
    available = width - 2 * margin
    for paragraph in TEXT.splitlines():
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) > available and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    # Ensure one section per BLE print request; short test strips are preferable.
    height = margin * 2 + line_height * len(lines)
    bitmap = Image.new("L", (width, height), 255)
    pen = ImageDraw.Draw(bitmap)
    for index, line in enumerate(lines):
        pen.text((margin, margin + index * line_height), line, font=font, fill=0)
    bitmap = bitmap.resize((384, round(height * scale)), Image.Resampling.NEAREST)
    return bitmap.convert("1", dither=Image.Dither.NONE)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, choices=WIDTHS, required=True)
    parser.add_argument("--address", default="48:0F:57:1E:0E:EB")
    parser.add_argument("--preview", help="Save PNG instead of printing")
    args = parser.parse_args()
    image = make_strip(args.width)
    if args.preview:
        image.save(args.preview)
        print(args.preview)
    else:
        asyncio.run(MXW01(args.address).print_image(image))
        print(f"Completed {args.width}/384")

if __name__ == "__main__":
    main()
