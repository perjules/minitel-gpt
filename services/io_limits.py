"""Shared byte/character budgets for local input and knowledge files."""
MAX_QUESTION_CHARS = 2000
MAX_REQUEST_BYTES = 2 * 1024 * 1024
KNOWLEDGE_FILE_BYTES = 256 * 1024
KNOWLEDGE_READ_BYTES = 64 * 1024


def read_knowledge(path):
    with path.open("rb") as stream:
        return stream.read(KNOWLEDGE_READ_BYTES).decode("utf-8", errors="ignore").strip()
