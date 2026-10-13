"""Printer-independent print job representation."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ImageBlock:
    path: Path


@dataclass(frozen=True)
class PrintDocument:
    title: str
    blocks: tuple[TextBlock | ImageBlock, ...]
