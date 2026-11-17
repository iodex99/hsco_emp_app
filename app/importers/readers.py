"""Read .xlsx / .xls / .csv into {sheet_name: [[cell, ...], ...]} — values only, filters ignored.

AutoFilters and hidden rows are irrelevant when reading cell values (we read every row), which satisfies the
"remove filters" requirement. Trailing empty rows (e.g. a 1,048,550-row used range) are trimmed by stopping after
a run of blank rows.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path

MAX_BLANK_RUN = 200
MAX_ROWS = 200_000


def _trim(rows: list[list]) -> list[list]:
    while rows and all(v in (None, "") for v in rows[-1]):
        rows.pop()
    return rows


def read_workbook(data: bytes, filename: str) -> dict[str, list[list]]:
    name = filename.lower()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return {"csv": _trim([list(r) for r in csv.reader(io.StringIO(text))])}
    if data[:4] == b"PK\x03\x04" or name.endswith((".xlsx", ".xlsm")):
        return _read_xlsx(data)
    if data[:8] == bytes.fromhex("D0CF11E0A1B11AE1") or name.endswith(".xls"):
        return _read_xls(data)
    raise ValueError(f"Unsupported file type: {filename}")


def _read_xlsx(data: bytes) -> dict[str, list[list]]:
    import openpyxl
    import warnings

    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("File is not a valid .xlsx workbook")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = {}
    try:
        for ws in wb.worksheets:
            # greytHR writes wrong <dimension> tags (e.g. DateOfJoiningReport says A1:A1); ignore them
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()
            rows, blank = [], 0
            for row in ws.iter_rows(values_only=True):
                vals = list(row)
                if all(v in (None, "") for v in vals):
                    blank += 1
                    if blank > MAX_BLANK_RUN:
                        break
                else:
                    blank = 0
                rows.append(vals)
                if len(rows) > MAX_ROWS:
                    raise ValueError(f"Sheet '{ws.title}' has more than {MAX_ROWS} rows")
            out[ws.title] = _trim(rows)
    finally:
        wb.close()
    return out


def _read_xls(data: bytes) -> dict[str, list[list]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=data)
    out = {}
    for sh in book.sheets():
        rows = []
        for i in range(min(sh.nrows, MAX_ROWS)):
            vals = []
            for c in sh.row(i):
                if c.ctype == xlrd.XL_CELL_DATE:
                    vals.append(datetime(*xlrd.xldate_as_tuple(c.value, book.datemode)))
                elif c.ctype == xlrd.XL_CELL_EMPTY:
                    vals.append(None)
                else:
                    vals.append(c.value)
            rows.append(vals)
        out[sh.name] = _trim(rows)
    return out


def read_path(path: str | Path) -> dict[str, list[list]]:
    p = Path(path)
    return read_workbook(p.read_bytes(), p.name)
