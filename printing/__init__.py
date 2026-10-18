"""Optional printing primitives; no Bluetooth imports at module load."""
from .authorization import PrintApproval
from .document import ImageBlock, PrintDocument, TextBlock
from .render import render_document

__all__ = ["ImageBlock", "PrintApproval", "PrintDocument", "TextBlock", "render_document"]
