"""Human-readable transfer codes: N-word-word-word."""

from __future__ import annotations

import re
import secrets
from functools import cache
from importlib.resources import files

CODE_RE = re.compile(r"^\d{1,3}(-[a-z0-9]+)+$")


@cache
def load_wordlist() -> tuple[list[str], list[str]]:
    text = files("eivlhole.mailbox").joinpath("wordlist.txt").read_text(encoding="utf-8")
    even: list[str] = []
    odd: list[str] = []
    target = even
    for line in text.splitlines():
        word = line.strip()
        if not word:
            continue
        if word == "---":
            target = odd
            continue
        target.append(word)
    return even, odd


def generate_code(words: int = 3) -> str:
    if words < 1:
        raise ValueError("a code needs at least one word")
    even, odd = load_wordlist()
    parts = [str(secrets.randbelow(999) + 1)]
    for i in range(words):
        parts.append(secrets.choice(even if i % 2 == 0 else odd))
    return "-".join(parts)


def normalize_code(text: str) -> str:
    cleaned = re.sub(r"[\s\-]+", "-", text.strip().lower()).strip("-")
    if not CODE_RE.match(cleaned):
        raise ValueError(f"not a valid code: {text!r} (expected like 7-tunnel-stairway-glucose)")
    return cleaned
