"""Шаблон текста с подстановкой значений из колонок файла.

В тексте пишется {имя_колонки}, например:
    ваш лицевой счет изменен с {cspot} на {cspotnew}
Регистр имени не важен, префикс "row." допускается ({row.cspot}) -
чтобы можно было вставить текст из старого f-string без правок.
"""
import re

PLACEHOLDER_RE = re.compile(r"\{\s*(?:row\.)?([^{}\s]+)\s*\}")


def placeholders(text: str) -> list[str]:
    """Имена подстановок в порядке появления, без повторов (в нижнем регистре)."""
    seen: list[str] = []
    for m in PLACEHOLDER_RE.finditer(text or ""):
        name = m.group(1).lower()
        if name not in seen:
            seen.append(name)
    return seen


def unknown(text: str, fields: list[str]) -> list[str]:
    """Подстановки, для которых в файле нет колонки."""
    have = {f.lower() for f in fields}
    return [p for p in placeholders(text) if p not in have]


def row_values(row: dict, names: list[str]) -> dict[str, str]:
    """Оставляет из строки файла только нужные колонки, ключи в нижнем регистре."""
    low = {k.lower(): v for k, v in row.items()}
    return {n: str(low.get(n, "")).strip() for n in names}


def render(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Подставляет значения. Возвращает (текст, имена пустых подстановок)."""
    empty: list[str] = []

    def repl(m: re.Match) -> str:
        name = m.group(1).lower()
        val = values.get(name, "")
        if not val and name not in empty:
            empty.append(name)
        return val

    return PLACEHOLDER_RE.sub(repl, text), empty
