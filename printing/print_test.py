"""Explicitly invoked image-printing smoke test."""
import argparse
import asyncio
from pathlib import Path
from printing import PrintDocument, ImageBlock, TextBlock, render_document
from printing.mxw01 import MXW01

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--address", default="48:0F:57:1E:0E:EB")
    p.add_argument("--image", type=Path)
    p.add_argument("--text")
    p.add_argument("--preview", type=Path)
    args = p.parse_args()
    if (args.image is None) == (args.text is None):
        p.error("Choose exactly one of --image or --text")
    block = ImageBlock(args.image) if args.image else TextBlock(args.text)
    bitmap = render_document(PrintDocument("3615 IA", (block,)))
    if args.preview:
        bitmap.save(args.preview)
        print(args.preview)
        return
    asyncio.run(MXW01(args.address).print_image(bitmap))
    print("Print completion acknowledged")

if __name__ == "__main__":
    main()
