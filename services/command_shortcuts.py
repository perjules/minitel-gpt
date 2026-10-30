"""Explicit colon shortcuts. Expand once; never interpret their payload as commands."""

import re

PREFIX = re.compile(r"^\s*([A-Za-z]):")


class ShortcutError(ValueError):
    pass


def expand(text, commands):
    match = PREFIX.match(text)
    if match is None:
        return text
    letter = match[1].upper()
    if letter not in commands:
        raise ShortcutError(
            "Raccourci inconnu. Utilisez les commandes indiquées ou GUIDE."
        )
    command, arguments = commands[letter]
    payload = text[match.end() :].strip()
    if payload and not arguments:
        raise ShortcutError(f"{letter}: doit être saisi seul. Aucune action effectuée.")
    return command + (" " + payload if payload else "")
