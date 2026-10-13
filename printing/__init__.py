"""Optional printing primitives; no Bluetooth imports at module load."""
from .document import PrintDocument, TextBlock, ImageBlock
from .authorization import PrintApproval
from .render import render_document

__all__ = ["PrintDocument", "TextBlock", "ImageBlock", "PrintApproval", "render_document"]
