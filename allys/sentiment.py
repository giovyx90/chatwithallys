import re

POSITIVE = {"grande", "top", "win", "bene", "forte", "lol", "ahah", "based", "goat", "ottimo"}
NEGATIVE = {"cringe", "fail", "male", "schifo", "perso", "rotto", "boh", "vergogna", "trash"}


def score_text(text: str) -> float:
    words = set(re.findall(r"\w+", text.lower()))
    return float(len(words & POSITIVE) - len(words & NEGATIVE))


def mentioned_symbols(text: str, symbols: list[str]) -> list[str]:
    upper = text.upper()
    return [symbol for symbol in symbols if symbol in upper or f"${symbol}" in upper]
