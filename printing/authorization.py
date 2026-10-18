"""Explicit authorization boundary for physical printing."""
from dataclasses import dataclass, field
from secrets import token_urlsafe

from .document import PrintDocument


@dataclass
class PrintApproval:
    """Single-use pending approval, never granted by model-generated content."""
    document: PrintDocument
    token: str = field(default_factory=lambda: token_urlsafe(16))
    _consumed: bool = field(default=False, init=False, repr=False)

    def confirm(self, *, user_input: str, token: str) -> PrintDocument | None:
        if self._consumed or token != self.token:
            return None
        if user_input.strip().upper() != "OUI":
            return None
        self._consumed = True
        return self.document

    def cancel(self) -> None:
        self._consumed = True
