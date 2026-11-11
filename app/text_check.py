"""Проверка и нормализация текста уведомления (русский + кыргызский).

Типичные проблемы с кыргызским текстом:
  * вместо ң ө ү набраны похожие символы из других алфавитов
    (ѳ - старославянская фита, ҥ - марийская, ұ - казахская, θ - греческая);
  * латинские буквы внутри кириллического слова (o, e, c, p, ö, ü, ñ ...);
  * текст уже "испорчен" кодировкой cp1251 - буквы ң ө ү превратились в "?".
"""
import re
import unicodedata
from urllib.parse import quote

from .config import settings

KYRGYZ_LETTERS = "ҢңӨөҮү"

ZERO_WIDTH_RE = re.compile("[​‌‍⁠﻿­]")

# Похожие символы -> правильные кыргызские буквы (заменяются везде)
LOOKALIKES = {
    "ѳ": "ө", "Ѳ": "Ө",   # фита
    "θ": "ө", "Θ": "Ө",   # греческая тета
    "ҥ": "ң", "Ҥ": "Ң",   # лигатура НГ
    "ӊ": "ң", "Ӊ": "Ң",
    "ӈ": "ң", "Ӈ": "Ң",
    "ұ": "ү", "Ұ": "Ү",   # казахская
}

# Латиница внутри кириллического слова -> кириллица.
# y/Y сознательно не трогаем: непонятно, имелось в виду "у" или "ү".
LATIN_TO_CYR = {
    "a": "а", "A": "А", "B": "В", "c": "с", "C": "С", "e": "е", "E": "Е",
    "H": "Н", "K": "К", "k": "к", "M": "М", "o": "о", "O": "О", "p": "р",
    "P": "Р", "T": "Т", "x": "х", "X": "Х",
    "ö": "ө", "Ö": "Ө", "ü": "ү", "Ü": "Ү", "ñ": "ң", "Ñ": "Ң",
}

WORD_RE = re.compile(r"\w+", re.UNICODE)


def _is_cyr(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def _is_latin(ch: str) -> bool:
    return ("a" <= ch.lower() <= "z") or ch in "öüñÖÜÑ"


def normalize(text: str) -> str:
    """Безопасная нормализация, применяется всегда перед отправкой."""
    text = unicodedata.normalize("NFC", text or "")
    text = ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _mixed_words(text: str) -> list[str]:
    found = []
    for w in WORD_RE.findall(text):
        if any(_is_cyr(c) for c in w) and any(_is_latin(c) for c in w):
            if w not in found:
                found.append(w)
    return found


def fix(text: str) -> tuple[str, list[str]]:
    """Автоисправление похожих символов. Возвращает (текст, список замен)."""
    text = normalize(text)
    changes: list[str] = []

    for bad, good in LOOKALIKES.items():
        if bad in text:
            changes.append(f"«{bad}» (U+{ord(bad):04X}) → «{good}»")
            text = text.replace(bad, good)

    def repl(m: re.Match) -> str:
        w = m.group(0)
        if not (any(_is_cyr(c) for c in w) and any(_is_latin(c) for c in w)):
            return w
        fixed = "".join(LATIN_TO_CYR.get(c, c) for c in w)
        if fixed != w:
            changes.append(f"{w} → {fixed}")
        return fixed

    text = WORD_RE.sub(repl, text)
    return text, changes


def analyze(raw: str) -> dict:
    text = normalize(raw)
    warnings: list[str] = []
    errors: list[str] = []

    if not text:
        errors.append("Текст пустой.")

    lookalikes = sorted({c for c in text if c in LOOKALIKES})
    if lookalikes:
        warnings.append(
            "Похожие, но неправильные символы вместо кыргызских букв: "
            + ", ".join(f"«{c}» (U+{ord(c):04X})" for c in lookalikes)
        )

    mixed = _mixed_words(text)
    if mixed:
        warnings.append(
            "Слова, где смешаны латиница и кириллица: " + ", ".join(mixed[:15])
            + (" …" if len(mixed) > 15 else "")
        )

    if "�" in text or re.search(r"\w\?\w", text):
        warnings.append(
            "Похоже, текст испорчен кодировкой (знаки «?» или «�» внутри слов). "
            "Скопируйте текст заново из исходного документа."
        )

    encoded_len = len(quote(text, safe=""))
    url_len = len(settings.PUSH_BASE_URL) + len("/push/") + 15 + 1 + encoded_len
    if settings.PUSH_MODE == "path" and url_len > 7000:
        warnings.append(
            f"Текст очень длинный для GET-запроса (URL ≈ {url_len} символов). "
            "Сервер может отклонить запрос - сократите текст или используйте PUSH_MODE=json."
        )

    return {
        "normalized": text,
        "length": len(text),
        "bytes_utf8": len(text.encode("utf-8")),
        "url_length": url_len,
        "has_kyrgyz": any(c in KYRGYZ_LETTERS for c in text),
        "warnings": warnings,
        "errors": errors,
        "fixable": bool(lookalikes or any(
            any(c in LATIN_TO_CYR for c in w) for w in mixed
        )),
    }
