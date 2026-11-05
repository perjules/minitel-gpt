"""Long continuous-roll endurance test; explicit operator invocation only."""
import argparse
import asyncio
import logging
import time

from PIL import Image

from printing.document import PrintDocument, TextBlock
from printing.mxw01 import MXW01
from printing.render import render_document_pages

log = logging.getLogger(__name__)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pages", type=int, choices=(2, 5, 10, 20), default=2)
    p.add_argument("--lines-per-page", type=int, default=35)
    p.add_argument("--chunk-height", type=int, default=600)
    p.add_argument("--address", default="48:0F:57:1E:0E:EB")
    p.add_argument("--preview", help="Save a full-length PNG instead of sending BLE")
    args = p.parse_args()
    if not 90 <= args.chunk_height <= 600:
        p.error("--chunk-height must be between 90 and 600")
    if not 10 <= args.lines_per_page <= 100:
        p.error("--lines-per-page must be between 10 and 100")
    lines = []
    for page in range(1, args.pages + 1):
        for n in range(1, args.lines_per_page + 1):
            idx = (page - 1) * args.lines_per_page + n
            lines.append(f"{idx:04d} | REPERE {page:02d}/{args.pages:02d} | "
                         "Texte de controle: ecriture, chiffres 0123456789.")
    document = PrintDocument("3615 IA - TEST ENDURANCE", (TextBlock("\n".join(lines)),))
    chunks = render_document_pages(document, max_height=args.chunk_height)
    print(f"Prepared {len(lines)} numbered lines, {len(chunks)} chunks, "
          f"{sum(c.height for c in chunks)} raster rows")
    if args.preview:
        height = sum(c.height for c in chunks)
        preview = Image.new("1", (384, height), 1)
        offset = 0
        for chunk in chunks:
            preview.paste(chunk, (0, offset))
            offset += chunk.height
        preview.save(args.preview)
        print(f"Saved {args.preview}")
        return
    print("Check paper supply; no automatic retries on ambiguous completion.", flush=True)
    started = time.monotonic()
    try:
        asyncio.run(MXW01(args.address).print_images(chunks))
    except Exception:
        log.exception("Endurance job failed after %.1f seconds", time.monotonic() - started)
        raise
    print(f"Print acknowledged after {time.monotonic()-started:.1f} seconds")

if __name__ == "__main__":
    main()
