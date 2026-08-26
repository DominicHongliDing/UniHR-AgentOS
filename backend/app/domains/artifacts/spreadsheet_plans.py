from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


_INVALID_SHEET_CHARACTERS = re.compile(r"[\\/*?:\[\]]")
_CELL_REFERENCE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
_RANGE_REFERENCE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}:[A-Z]{1,3}[1-9][0-9]{0,6}$")
_HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")


def _validate_sheet_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 31 or _INVALID_SHEET_CHARACTERS.search(cleaned):
        raise ValueError("invalid_spreadsheet_sheet_name")
    return cleaned


class CreateSheetOperation(BaseModel):
    """Create a bounded table as the root of a new workbook."""

    type: Literal["create_sheet"]
    sheet: str
    headers: list[str] = Field(min_length=1, max_length=100)
    rows: list[list[str | int | float | bool | None]] = Field(default_factory=list, max_length=500)

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @model_validator(mode="after")
    def validate_table(self) -> "CreateSheetOperation":
        cleaned_headers = [" ".join(str(value).split())[:200] for value in self.headers]
        if any(not value for value in cleaned_headers):
            raise ValueError("spreadsheet_create_sheet_header_required")
        if len(set(cleaned_headers)) != len(cleaned_headers):
            raise ValueError("spreadsheet_create_sheet_headers_must_be_unique")
        for row in self.rows:
            if len(row) != len(cleaned_headers):
                raise ValueError("spreadsheet_create_sheet_row_width_mismatch")
            for value in row:
                if isinstance(value, str):
                    if len(value) > 32_767:
                        raise ValueError("spreadsheet_create_sheet_cell_too_long")
                    if value.lstrip().startswith("="):
                        raise ValueError("spreadsheet_create_sheet_formula_not_allowed")
        self.headers = cleaned_headers
        return self


class CopySheetOperation(BaseModel):
    type: Literal["copy_sheet"]
    source_sheet: str
    target_sheet: str

    _source_sheet = field_validator("source_sheet")(_validate_sheet_name)
    _target_sheet = field_validator("target_sheet")(_validate_sheet_name)


class RenameSheetOperation(BaseModel):
    type: Literal["rename_sheet"]
    source_sheet: str
    target_sheet: str

    _source_sheet = field_validator("source_sheet")(_validate_sheet_name)
    _target_sheet = field_validator("target_sheet")(_validate_sheet_name)


class ReplaceYearTextOperation(BaseModel):
    type: Literal["replace_year_text"]
    sheet: str
    from_year: int = Field(ge=2000, le=2100)
    to_year: int = Field(ge=2000, le=2100)
    include_formulas: bool = False

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @model_validator(mode="after")
    def years_must_differ(self) -> "ReplaceYearTextOperation":
        if self.from_year == self.to_year:
            raise ValueError("spreadsheet_years_must_differ")
        return self


class SetCellValue(BaseModel):
    cell: str
    value: str | int | float | bool | None
    number_format: str = Field(default="", max_length=80)

    @field_validator("cell")
    @classmethod
    def validate_cell(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not _CELL_REFERENCE.fullmatch(cleaned):
            raise ValueError("invalid_spreadsheet_cell_reference")
        return cleaned


class SetCellsOperation(BaseModel):
    type: Literal["set_cells"]
    sheet: str
    cells: list[SetCellValue] = Field(min_length=1, max_length=200)

    _sheet = field_validator("sheet")(_validate_sheet_name)


class MapColumnOperation(BaseModel):
    type: Literal["map_column"]
    source_sheet: str
    target_sheet: str
    source_key_header: str = Field(min_length=1, max_length=100)
    target_key_header: str = Field(min_length=1, max_length=100)
    source_value_header: str = Field(min_length=1, max_length=100)
    target_header: str = Field(min_length=1, max_length=100)
    source_header_row: int = Field(default=1, ge=1, le=200)
    target_header_row: int = Field(default=1, ge=1, le=200)
    normalize_key: bool = True
    missing_behavior: Literal["blank_and_exception", "keep_existing_and_exception"] = "blank_and_exception"

    _source_sheet = field_validator("source_sheet")(_validate_sheet_name)
    _target_sheet = field_validator("target_sheet")(_validate_sheet_name)


class AddFormulaColumnOperation(BaseModel):
    type: Literal["add_formula_column"]
    sheet: str
    header: str = Field(min_length=1, max_length=100)
    formula_template: str = Field(min_length=2, max_length=1_000)
    header_row: int = Field(default=1, ge=1, le=200)
    start_row: int | None = Field(default=None, ge=2, le=1_048_576)
    end_row: int | None = Field(default=None, ge=2, le=1_048_576)
    number_format: str = Field(default="0.00", max_length=80)
    overwrite_existing: bool = False

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @model_validator(mode="after")
    def validate_formula_range(self) -> "AddFormulaColumnOperation":
        formula = self.formula_template.strip()
        formula = re.sub(r"\{row\}\s*([A-Z]{1,3})\b", r"\1{row}", formula, flags=re.IGNORECASE)
        if not formula.startswith("=") or "{row}" not in formula:
            raise ValueError("spreadsheet_formula_template_requires_row_placeholder")
        if self.start_row is not None and self.end_row is not None and self.start_row > self.end_row:
            raise ValueError("spreadsheet_formula_row_range_invalid")
        self.formula_template = formula
        return self


class FormatRangeOperation(BaseModel):
    type: Literal["format_range"]
    sheet: str
    range: str
    font_name: str | None = Field(default=None, max_length=80)
    font_size: float | None = Field(default=None, ge=6, le=72)
    bold: bool | None = None
    italic: bool | None = None
    font_color: str | None = None
    fill_color: str | None = None
    number_format: str | None = Field(default=None, max_length=80)
    horizontal_alignment: Literal["left", "center", "right", "general"] | None = None
    vertical_alignment: Literal["top", "center", "bottom"] | None = None
    wrap_text: bool | None = None

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @field_validator("range")
    @classmethod
    def validate_range(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not (_CELL_REFERENCE.fullmatch(cleaned) or _RANGE_REFERENCE.fullmatch(cleaned)):
            raise ValueError("invalid_spreadsheet_range_reference")
        return cleaned

    @field_validator("font_color", "fill_color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lstrip("#").upper()
        if not _HEX_COLOR.fullmatch(cleaned):
            raise ValueError("invalid_spreadsheet_color")
        return cleaned

    @model_validator(mode="after")
    def require_format_change(self) -> "FormatRangeOperation":
        values = (
            self.font_name, self.font_size, self.bold, self.italic, self.font_color,
            self.fill_color, self.number_format, self.horizontal_alignment,
            self.vertical_alignment, self.wrap_text,
        )
        if all(value is None for value in values):
            raise ValueError("spreadsheet_format_change_required")
        return self


class SetAutoFilterOperation(BaseModel):
    type: Literal["set_auto_filter"]
    sheet: str
    range: str

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @field_validator("range")
    @classmethod
    def validate_range(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not _RANGE_REFERENCE.fullmatch(cleaned):
            raise ValueError("invalid_spreadsheet_range_reference")
        return cleaned


class FilterRowsOperation(BaseModel):
    type: Literal["filter_rows"]
    sheet: str
    header: str = Field(min_length=1, max_length=100)
    allowed_values: list[str | int | float | bool] = Field(min_length=1, max_length=100)
    header_row: int = Field(default=1, ge=1, le=200)
    case_sensitive: bool = False

    _sheet = field_validator("sheet")(_validate_sheet_name)


class FilterDateRangeOperation(BaseModel):
    """Show rows whose date value falls inside a code-validated inclusive range."""

    type: Literal["filter_date_range"]
    sheet: str
    header: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    header_row: int = Field(default=1, ge=1, le=200)
    invalid_date_policy: Literal["exclude_and_report", "keep_and_report"] = "exclude_and_report"

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @model_validator(mode="after")
    def validate_date_range(self) -> "FilterDateRangeOperation":
        if self.start_date > self.end_date:
            raise ValueError("spreadsheet_date_range_invalid")
        if (self.end_date - self.start_date).days > 3_660:
            raise ValueError("spreadsheet_date_range_too_large")
        return self


class SortRowsOperation(BaseModel):
    type: Literal["sort_rows"]
    sheet: str
    header: str = Field(min_length=1, max_length=100)
    direction: Literal["ascending", "descending"] = "ascending"
    header_row: int = Field(default=1, ge=1, le=200)
    start_row: int | None = Field(default=None, ge=2, le=1_048_576)
    end_row: int | None = Field(default=None, ge=2, le=1_048_576)

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @model_validator(mode="after")
    def validate_row_range(self) -> "SortRowsOperation":
        if self.start_row is not None and self.end_row is not None and self.start_row > self.end_row:
            raise ValueError("spreadsheet_sort_row_range_invalid")
        return self


class AddDataValidationOperation(BaseModel):
    type: Literal["add_data_validation"]
    sheet: str
    header: str = Field(min_length=1, max_length=100)
    validation_type: Literal["list", "whole", "decimal", "date"]
    values: list[str] = Field(default_factory=list, max_length=50)
    minimum: float | int | None = None
    maximum: float | int | None = None
    header_row: int = Field(default=1, ge=1, le=200)
    start_row: int | None = Field(default=None, ge=2, le=1_048_576)
    end_row: int | None = Field(default=None, ge=2, le=1_048_576)
    allow_blank: bool = True
    prompt: str = Field(default="", max_length=240)

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @model_validator(mode="after")
    def validate_rule(self) -> "AddDataValidationOperation":
        if self.validation_type == "list" and not self.values:
            raise ValueError("spreadsheet_validation_list_values_required")
        if self.validation_type != "list" and self.minimum is None and self.maximum is None:
            raise ValueError("spreadsheet_validation_bounds_required")
        if self.start_row is not None and self.end_row is not None and self.start_row > self.end_row:
            raise ValueError("spreadsheet_validation_row_range_invalid")
        return self


class CreateSummaryOperation(BaseModel):
    type: Literal["create_summary"]
    source_sheet: str
    target_sheet: str
    group_header: str = Field(min_length=1, max_length=100)
    value_header: str = Field(default="", max_length=100)
    aggregation: Literal["count", "sum", "average", "min", "max"] = "sum"
    source_header_row: int = Field(default=1, ge=1, le=200)
    result_header: str = Field(default="汇总值", min_length=1, max_length=100)
    number_format: str = Field(default="0.00", max_length=80)

    _source_sheet = field_validator("source_sheet")(_validate_sheet_name)
    _target_sheet = field_validator("target_sheet")(_validate_sheet_name)

    @model_validator(mode="after")
    def require_value_header(self) -> "CreateSummaryOperation":
        if self.aggregation != "count" and not self.value_header.strip():
            raise ValueError("spreadsheet_summary_value_header_required")
        return self


class AddChartOperation(BaseModel):
    type: Literal["add_chart"]
    sheet: str
    category_header: str = Field(min_length=1, max_length=100)
    value_headers: list[str] = Field(min_length=1, max_length=6)
    chart_type: Literal["bar", "column", "line", "pie"] = "column"
    title: str = Field(default="数据图表", max_length=120)
    position: str = "H2"
    header_row: int = Field(default=1, ge=1, le=200)

    _sheet = field_validator("sheet")(_validate_sheet_name)

    @field_validator("position")
    @classmethod
    def validate_position(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not _CELL_REFERENCE.fullmatch(cleaned):
            raise ValueError("invalid_spreadsheet_cell_reference")
        return cleaned


SpreadsheetOperation = Annotated[
    CreateSheetOperation
    | CopySheetOperation
    | RenameSheetOperation
    | ReplaceYearTextOperation
    | SetCellsOperation
    | MapColumnOperation
    | AddFormulaColumnOperation
    | FormatRangeOperation
    | SetAutoFilterOperation
    | FilterRowsOperation
    | FilterDateRangeOperation
    | SortRowsOperation
    | AddDataValidationOperation
    | CreateSummaryOperation
    | AddChartOperation,
    Field(discriminator="type"),
]


class SheetHeadersRequirement(BaseModel):
    sheet: str
    headers: list[str] = Field(min_length=1, max_length=100)
    header_row: int = Field(default=1, ge=1, le=200)

    _sheet = field_validator("sheet")(_validate_sheet_name)


class UniqueKeyRequirement(BaseModel):
    sheet: str
    header: str = Field(min_length=1, max_length=100)
    header_row: int = Field(default=1, ge=1, le=200)
    allow_blank: bool = False

    _sheet = field_validator("sheet")(_validate_sheet_name)


class SpreadsheetValidationPlan(BaseModel):
    required_sheets: list[str] = Field(default_factory=list, max_length=100)
    required_headers: list[SheetHeadersRequirement] = Field(default_factory=list, max_length=100)
    unique_keys: list[UniqueKeyRequirement] = Field(default_factory=list, max_length=30)
    reject_formula_errors: bool = True

    @field_validator("required_sheets")
    @classmethod
    def validate_required_sheets(cls, values: list[str]) -> list[str]:
        return [_validate_sheet_name(value) for value in values]


class SpreadsheetPlan(BaseModel):
    """A model-produced plan that is safe to validate before deterministic execution."""

    version: Literal["1.0", "1.1"] = "1.1"
    objective: str = Field(min_length=1, max_length=2_000)
    operations: list[SpreadsheetOperation] = Field(default_factory=list, max_length=50)
    validations: SpreadsheetValidationPlan = Field(default_factory=SpreadsheetValidationPlan)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=30)
    # The planner may recommend review, but the workflow's hard approval gate is
    # decided separately from task risk and the user's explicit approval setting.
    requires_human_review: bool = False

    @field_validator("objective")
    @classmethod
    def clean_objective(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("assumptions", "unresolved_questions")
    @classmethod
    def clean_notes(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = " ".join(str(value).split())[:500]
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned
