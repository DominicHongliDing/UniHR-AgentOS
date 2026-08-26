from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook


@dataclass(frozen=True)
class WorkbookSnapshot:
    sheets: dict[str, dict[str, dict[str, Any]]]
    scanned_cells: int
    truncated: bool


class WorkbookDiffEngine:
    """Compare workbook state at the artifact seam without depending on the planner."""

    def __init__(self, *, max_cells: int = 200_000, max_samples: int = 120) -> None:
        self.max_cells = max_cells
        self.max_samples = max_samples

    def snapshot(self, workbook: Workbook) -> WorkbookSnapshot:
        sheets: dict[str, dict[str, dict[str, Any]]] = {}
        scanned = 0
        truncated = False
        for sheet in workbook.worksheets:
            if sheet.title.startswith("UniHR"):
                continue
            cells: dict[str, dict[str, Any]] = {}
            for row in sheet.iter_rows():
                for cell in row:
                    if scanned >= self.max_cells:
                        truncated = True
                        break
                    scanned += 1
                    if cell.value in (None, "") and not cell.has_style:
                        continue
                    formula = cell.value if isinstance(cell.value, str) and cell.value.startswith("=") else None
                    cells[cell.coordinate] = {
                        "value_key": self._value_key(cell.value),
                        "display": self._display(cell.value),
                        "formula": formula,
                        "style": self._style_signature(cell),
                        "styled": bool(cell.has_style),
                    }
                if truncated:
                    break
            sheets[sheet.title] = cells
            if truncated:
                break
        return WorkbookSnapshot(sheets=sheets, scanned_cells=scanned, truncated=truncated)

    def compare(self, before: WorkbookSnapshot, after: WorkbookSnapshot) -> dict[str, Any]:
        added_sheets = sorted(set(after.sheets) - set(before.sheets))
        removed_sheets = sorted(set(before.sheets) - set(after.sheets))
        formula_changes = 0
        data_changes = 0
        style_changes = 0
        changed_cells: set[tuple[str, str]] = set()
        samples: list[dict[str, Any]] = []
        sheet_summaries: list[dict[str, Any]] = []
        for sheet_name in sorted(set(before.sheets) | set(after.sheets)):
            before_cells = before.sheets.get(sheet_name, {})
            after_cells = after.sheets.get(sheet_name, {})
            sheet_formula = 0
            sheet_data = 0
            sheet_style = 0
            sheet_changed: set[str] = set()
            for coordinate in sorted(set(before_cells) | set(after_cells), key=self._cell_sort_key):
                old = before_cells.get(coordinate)
                new = after_cells.get(coordinate)
                change_types: list[str] = []
                old_formula = old.get("formula") if old else None
                new_formula = new.get("formula") if new else None
                if old_formula != new_formula and (old_formula is not None or new_formula is not None):
                    formula_changes += 1
                    sheet_formula += 1
                    change_types.append("formula")
                elif (old.get("value_key") if old else None) != (new.get("value_key") if new else None):
                    data_changes += 1
                    sheet_data += 1
                    change_types.append("data")
                if (old.get("style") if old else None) != (new.get("style") if new else None):
                    style_changes += 1
                    sheet_style += 1
                    change_types.append("style")
                if not change_types:
                    continue
                changed_cells.add((sheet_name, coordinate))
                sheet_changed.add(coordinate)
                if len(samples) < self.max_samples:
                    samples.append({
                        "sheet": sheet_name,
                        "cell": coordinate,
                        "change_types": change_types,
                        "before": old.get("display") if old else None,
                        "after": new.get("display") if new else None,
                    })
            if sheet_changed or sheet_name in added_sheets or sheet_name in removed_sheets:
                sheet_summaries.append({
                    "sheet": sheet_name,
                    "status": "added" if sheet_name in added_sheets else "removed" if sheet_name in removed_sheets else "changed",
                    "changed_cells": len(sheet_changed),
                    "formula_changes": sheet_formula,
                    "data_changes": sheet_data,
                    "style_changes": sheet_style,
                })
        return {
            "changed_cells": len(changed_cells),
            "formula_changes": formula_changes,
            "data_changes": data_changes,
            "style_changes": style_changes,
            "added_sheets": added_sheets,
            "removed_sheets": removed_sheets,
            "sheets": sheet_summaries,
            "samples": samples,
            "sample_limit": self.max_samples,
            "truncated": before.truncated or after.truncated,
            "scanned_cells": before.scanned_cells + after.scanned_cells,
        }

    @staticmethod
    def _value_key(value: Any) -> str:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return format(value, "f")
        return repr(value)

    @classmethod
    def _display(cls, value: Any) -> str | int | float | bool | None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value if not isinstance(value, str) else value[:300]
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)[:300]

    @staticmethod
    def _style_signature(cell: Any) -> tuple[Any, ...]:
        font_color = getattr(getattr(cell.font, "color", None), "rgb", None)
        fill_color = getattr(getattr(cell.fill, "fgColor", None), "rgb", None)
        side_style = lambda name: getattr(getattr(cell.border, name, None), "style", None)
        return (
            cell.font.name,
            cell.font.sz,
            cell.font.bold,
            cell.font.italic,
            str(font_color) if font_color is not None else None,
            cell.fill.fill_type,
            str(fill_color) if fill_color is not None else None,
            cell.number_format,
            cell.alignment.horizontal,
            cell.alignment.vertical,
            cell.alignment.wrap_text,
            side_style("left"),
            side_style("right"),
            side_style("top"),
            side_style("bottom"),
        )

    @staticmethod
    def _cell_sort_key(coordinate: str) -> tuple[int, str]:
        digits = "".join(character for character in coordinate if character.isdigit())
        letters = "".join(character for character in coordinate if character.isalpha())
        return int(digits or 0), letters
