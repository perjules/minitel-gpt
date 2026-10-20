"""Shared byte/character budgets for local input and knowledge files."""
import os
import stat

MAX_QUESTION_CHARS = 2000
MAX_REQUEST_BYTES = 2 * 1024 * 1024
KNOWLEDGE_FILE_BYTES = 256 * 1024
KNOWLEDGE_READ_BYTES = 64 * 1024


def read_knowledge(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("Regular knowledge file required")
        return stream.read(KNOWLEDGE_READ_BYTES).decode("utf-8", errors="ignore").strip()
