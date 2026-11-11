"""Чтение списка логинов из .xlsx / .csv / .txt.

Кодировка CSV: сначала UTF-8 (с BOM или без), если не получилось - cp1251
(так сохраняет Excel "CSV (разделители - запятые)" в русской Windows).
"""
import csv
import io
import re
from dataclasses import dataclass, field

import openpyxl

from .config import settings

HEADER_NAMES = {
    "login", "логин", "phone", "телефон", "номер", "number", "msisdn",
    "номер телефона", "tel", "mobile",
}
NON_DIGIT_RE = re.compile(r"\D")


class ParseError(Exception):
    pass


@dataclass
class ParseResult:
    logins: list[str] = field(default_factory=list)
    rows: int = 0
    duplicates: int = 0
    invalid: int = 0
    invalid_samples: list[dict] = field(default_factory=list)
    column: str = ""
    encoding: str = ""
    sheets: int = 0
    # Колонки из строки заголовка и значения по каждому логину - для подстановок в текст
    fields: list[str] = field(default_factory=list)
    values: dict[str, dict[str, str]] = field(default_factory=dict)


def _cell_to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value).strip()


def _find_column(first_row: list) -> tuple[int, bool, str]:
    """Возвращает (индекс колонки, есть ли заголовок, название колонки)."""
    for i, v in enumerate(first_row):
        name = _cell_to_str(v).lower()
        if name in HEADER_NAMES:
            return i, True, _cell_to_str(v)
    first = _cell_to_str(first_row[0]) if first_row else ""
    digits = NON_DIGIT_RE.sub("", first)
    has_header = bool(first) and len(digits) < settings.MIN_LOGIN_DIGITS
    return 0, has_header, (first if has_header else "1-я колонка")


class _Collector:
    def __init__(self) -> None:
        self.res = ParseResult()
        self._seen: set[str] = set()

    def add_fields(self, headers: list[str]) -> None:
        for h in headers:
            if h and h not in self.res.fields:
                self.res.fields.append(h)

    def add(self, raw, row_no: int, sheet: str = "", headers: list[str] | None = None,
            row=None) -> None:
        s = _cell_to_str(raw)
        if not s:
            return
        self.res.rows += 1
        digits = NON_DIGIT_RE.sub("", s)
        if not (settings.MIN_LOGIN_DIGITS <= len(digits) <= settings.MAX_LOGIN_DIGITS):
            self.res.invalid += 1
            if len(self.res.invalid_samples) < 20:
                self.res.invalid_samples.append(
                    {"row": row_no, "sheet": sheet, "value": s[:50]}
                )
            return
        if digits in self._seen:
            self.res.duplicates += 1
            return
        self._seen.add(digits)
        self.res.logins.append(digits)
        if headers and row is not None:
            self.res.values[digits] = {
                h: _cell_to_str(row[i]) if i < len(row) else ""
                for i, h in enumerate(headers) if h
            }


def _headers(first_row, has_header: bool) -> list[str]:
    return [_cell_to_str(v) for v in first_row] if has_header else []


def _parse_xlsx(data: bytes) -> ParseResult:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ParseError(f"Не удалось открыть Excel-файл: {e}") from e
    col = _Collector()
    try:
        for ws in wb.worksheets:
            rows = ws.iter_rows(values_only=True)
            first = next(rows, None)
            if first is None:
                continue
            col.res.sheets += 1
            idx, has_header, name = _find_column(list(first))
            if not col.res.column:
                col.res.column = name
            headers = _headers(first, has_header)
            col.add_fields(headers)
            if not has_header:
                col.add(first[idx] if idx < len(first) else None, 1, ws.title)
            for n, row in enumerate(rows, start=2):
                if row and idx < len(row):
                    col.add(row[idx], n, ws.title, headers, row)
    finally:
        wb.close()
    col.res.encoding = "xlsx (Unicode)"
    return col.res


def _decode_text(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ParseError("Не удалось определить кодировку файла. Сохраните его как CSV UTF-8.")


def _parse_csv(data: bytes) -> ParseResult:
    text, enc = _decode_text(data)
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    col = _Collector()
    first = next(reader, None)
    if first is None:
        raise ParseError("Файл пустой.")
    idx, has_header, name = _find_column(first)
    col.res.column = name
    col.res.sheets = 1
    headers = _headers(first, has_header)
    col.add_fields(headers)
    if not has_header:
        col.add(first[idx] if idx < len(first) else None, 1)
    for n, row in enumerate(reader, start=2):
        if row and idx < len(row):
            col.add(row[idx], n, "", headers, row)
    col.res.encoding = enc
    return col.res


def parse_file(filename: str, data: bytes) -> ParseResult:
    name = (filename or "").lower()
    if name.endswith(".xls"):
        raise ParseError(
            "Старый формат .xls не поддерживается. Откройте файл в Excel и "
            "сохраните как «Книга Excel (*.xlsx)»."
        )
    if name.endswith((".xlsx", ".xlsm")):
        res = _parse_xlsx(data)
    elif name.endswith((".csv", ".txt")):
        res = _parse_csv(data)
    else:
        raise ParseError("Поддерживаются файлы .xlsx, .csv, .txt")
    if not res.logins:
        raise ParseError("В файле не найдено ни одного корректного логина.")
    return res
