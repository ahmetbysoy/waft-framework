"""Data sources: Excel / JSON / CSV / inline rows.

The loader produces :class:`~waft.models.TargetRow` objects which carry:

* ``target_url``               – where to navigate,
* ``form_data``                – what to type (arbitrary columns become data keys),
* per-row configuration        – selectors, steps, submit behaviour, IMAP settings,
* multi-value columns          – ``email`` values separated by ``|`` produce several rows.

Column names are matched case-insensitively and support Turkish characters, so an Excel
file with ``Hedef URL`` / ``E-posta`` / ``Şifre`` headers works out of the box.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from .config import Config
from .errors import DataSourceError
from .logging_setup import get_logger
from .models import StepAction, TargetRow
from .utils import (
    interpolate_deep,
    looks_like_selector,
    normalize_ws,
    parse_bool,
    parse_int,
    read_json,
    slugify,
    split_csv,
    truncate,
)

__all__ = [
    "DataLoader",
    "LoadReport",
    "MULTI_VALUE_SEPARATOR",
    "URL_COLUMN_CANDIDATES",
    "NON_FIELD_COLUMNS",
    "normalize_header",
]

logger = get_logger("waft.data_source")

#: Multiple values inside one cell split into separate rows (``a@x.com|b@x.com``).
MULTI_VALUE_SEPARATOR = "|"

#: Column names (normalised) treated as the target URL.
URL_COLUMN_CANDIDATES: tuple[str, ...] = (
    "target_url",
    "targeturl",
    "url",
    "hedef_url",
    "hedefurl",
    "hedef",
    "link",
    "adres",
    "site",
    "sayfa",
    "page_url",
    "form_url",
    "endpoint",
)

#: Columns that configure the run instead of being typed into the form.
NON_FIELD_COLUMNS: frozenset[str] = frozenset(
    {
        "target_url",
        "name",
        "test_name",
        "scenario",
        "senaryo",
        "steps",
        "step",
        "workflow",
        "adimlar",
        "selectors",
        "selector_map",
        "submit",
        "submit_form",
        "submit_selector",
        "submit_button_text",
        "submit_text",
        "success_url_regex",
        "success_selector",
        "error_selector",
        "wait_after_submit_ms",
        "wait_after_submit",
        "requires_email_verification",
        "email_verification",
        "expect_error",
        "expect_failure",
        "expecting_error",
        "beklenen_hata",
        "hata_bekleniyor",
        "negative_test",
        "verification_email",
        "verify_email",
        "verification_email_field",
        "verification_link_regex",
        "verification_subject_regex",
        "verification_sender",
        "verification_otp_field",
        "verification_timeout_s",
        "resend_selector",
        "proxy",
        "iterations",
        "tekrar",
        "tags",
        "etiketler",
        "active",
        "aktif",
        "enabled",
        "notes",
        "notlar",
    }
)

_TRUTHY = {"1", "true", "yes", "y", "on", "evet", "aktif", "var", "x", "✓"}
_FALSY = {"0", "false", "no", "n", "off", "hayir", "hayır", "pasif", "yok", ""}

_TR_ASCII = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ğ": "g",
        "Ğ": "g",
        "ş": "s",
        "Ş": "s",
        "ç": "c",
        "Ç": "c",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
    }
)


def normalize_header(name: Any) -> str:
    """Normalise a column header: ``Hedef URL`` → ``hedef_url``, ``E-Posta`` → ``e_posta``."""
    text = str(name or "").strip().translate(_TR_ASCII).lower()
    text = re.sub(r"[\s\-./\\]+", "_", text)
    text = re.sub(r"[^a-z0-9_]+", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "column"


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip() == ""


def _cell_to_text(value: Any) -> str:
    """Render a spreadsheet cell as the string a human would type into the form."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(round(value, 10)).rstrip("0").rstrip(".")
    if hasattr(value, "isoformat"):  # datetime / date / time
        return value.isoformat(sep=" ") if hasattr(value, "hour") else value.isoformat()
    return str(value).strip()


def _truthy(value: Any, default: bool = False) -> bool:
    if _is_blank(value):
        return default
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return parse_bool(text, default=default)


@dataclass
class LoadReport:
    """Bookkeeping about what was read from the data source."""

    source: str = ""
    format: str = ""
    sheet: Optional[str] = None
    columns: list[str] = field(default_factory=list)
    field_columns: list[str] = field(default_factory=list)
    url_column: Optional[str] = None
    raw_rows: int = 0
    expanded_rows: int = 0
    skipped_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    multi_value_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "format": self.format,
            "sheet": self.sheet,
            "columns": self.columns,
            "field_columns": self.field_columns,
            "url_column": self.url_column,
            "raw_rows": self.raw_rows,
            "expanded_rows": self.expanded_rows,
            "skipped_rows": self.skipped_rows,
            "multi_value_columns": self.multi_value_columns,
            "warnings": self.warnings,
        }


class DataLoader:
    """Load target rows from Excel, JSON or CSV and normalise them into ``TargetRow``."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.report = LoadReport()
        self._used_slug_names: dict[str, int] = {}
        self._row_counter = 0

    # ------------------------------------------------------------------ public API
    def load(self, path: Optional[Union[str, Path]] = None) -> list[TargetRow]:
        """Load and validate all target rows from *path* (defaults to ``config.data_file``)."""
        source = Path(path).expanduser() if path else self.config.data_file
        if source is None or not Path(source).exists():
            raise DataSourceError(
                "No data source available: pass --data <file.xlsx|file.json|file.csv> or --urls url1,url2"
            )
        source = Path(source)
        self.report.source = str(source)

        suffix = source.suffix.lower()
        if suffix in {".xlsx", ".xlsm", ".xls"}:
            records, sheet = self._load_excel(source)
            self.report.format = "excel"
            self.report.sheet = sheet
        elif suffix == ".csv":
            records = self._load_csv(source)
            self.report.format = "csv"
        elif suffix == ".json":
            records = self._load_json(source)
            self.report.format = "json"
        elif suffix in {".md", ".txt"}:
            records = self._load_plaintext(source)
            self.report.format = "text"
        else:
            raise DataSourceError(f"Unsupported data source format: {suffix or source.name}")

        if not records:
            raise DataSourceError(f"Data source {source} contained no usable rows")

        rows = self._normalise(records)
        if not rows:
            raise DataSourceError(
                f"No valid rows found in {source}. Every row needs a target URL column "
                f"(one of: {', '.join(URL_COLUMN_CANDIDATES[:5])}) and (optionally) form fields."
            )

        if self.config.shuffle_targets:
            rng = random.Random(self.config.seed) if self.config.seed is not None else random.Random()
            rng.shuffle(rows)

        logger.info(
            "Loaded %d target(s) from %s%s (%d field column(s): %s)",
            len(rows),
            source.name,
            f" [sheet: {self.report.sheet}]" if self.report.sheet else "",
            len(self.report.field_columns),
            ", ".join(self.report.field_columns[:8]) + ("…" if len(self.report.field_columns) > 8 else ""),
        )
        for warning in self.report.warnings:
            logger.warning("%s", warning)
        return rows

    # ------------------------------------------------------------------ readers
    def _load_excel(self, path: Path) -> tuple[list[dict[str, Any]], Optional[str]]:
        """Read an Excel workbook using pandas (+openpyxl/xlrd engines)."""
        import pandas as pd  # imported lazily: pandas is heavy but only needed for Excel

        sheet = self.config.sheet_name
        read_kwargs: dict[str, Any] = {"sheet_name": sheet if sheet else 0, "dtype": object}
        try:
            frame = pd.read_excel(path, **read_kwargs)
            resolved_sheet = sheet or self._first_sheet_name(path)
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise DataSourceError(
                "Reading Excel requires pandas and an engine. Install with: "
                "pip install pandas openpyxl xlrd"
            ) from exc
        except ValueError as exc:
            if "Worksheet" in str(exc) and sheet:
                available = self._sheet_names(path)
                raise DataSourceError(
                    f"Sheet '{sheet}' not found in {path.name}. Available sheets: {', '.join(available)}"
                ) from exc
            raise DataSourceError(f"Could not read Excel file {path}: {exc}") from exc

        frame = frame.dropna(how="all")
        records = frame.to_dict(orient="records")
        self.report.raw_rows = len(records)
        return records, resolved_sheet

    @staticmethod
    def _sheet_names(path: Path) -> list[str]:
        try:
            import pandas as pd

            return list(pd.ExcelFile(path).sheet_names)
        except Exception:  # noqa: BLE001 - best effort
            return []

    def _first_sheet_name(self, path: Path) -> Optional[str]:
        names = self._sheet_names(path)
        return names[0] if names else None

    def _load_csv(self, path: Path) -> list[dict[str, Any]]:
        """Read CSV with automatic delimiter sniffing (`,` `;` tab)."""
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        sample = raw[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
        reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
        records = [dict(record) for record in reader]
        self.report.raw_rows = len(records)
        return records

    def _load_json(self, path: Path) -> list[dict[str, Any]]:
        """Read JSON: list of objects, ``{"rows": [...]}`` or ``{"targets": [...]}``."""
        payload = read_json(path, default=None)
        if payload is None:
            raise DataSourceError(f"JSON data source is empty or unreadable: {path}")
        records: list[dict[str, Any]]
        if isinstance(payload, list):
            if all(isinstance(item, str) for item in payload):
                records = [{"target_url": item} for item in payload]
            elif all(isinstance(item, dict) for item in payload):
                records = [dict(item) for item in payload]
            else:
                raise DataSourceError(
                    f"JSON data source {path} must be a list of objects (or a list of URL strings)"
                )
        elif isinstance(payload, dict):
            for key in ("rows", "targets", "data", "items", "records"):
                if isinstance(payload.get(key), list):
                    records = [dict(item) if isinstance(item, dict) else {"target_url": item} for item in payload[key]]
                    break
            else:
                # A single object describing one target.
                records = [dict(payload)]
        else:
            raise DataSourceError(f"Unsupported JSON structure in {path}")
        self.report.raw_rows = len(records)
        return records

    def _load_plaintext(self, path: Path) -> list[dict[str, Any]]:
        """Read a plain list of URLs (one per line, ``#`` comments allowed)."""
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            records.append({"target_url": entry})
        self.report.raw_rows = len(records)
        return records

    # ------------------------------------------------------------------ normalisation
    def _normalise(self, records: Sequence[dict[str, Any]]) -> list[TargetRow]:
        """Convert raw records into :class:`TargetRow` objects (expanding multi-values)."""
        if not records:
            return []

        # 1) normalise headers, remembering the original spelling for error messages.
        normalised_records: list[dict[str, Any]] = []
        header_map: dict[str, str] = {}
        for record in records:
            clean: dict[str, Any] = {}
            for raw_key, value in record.items():
                if raw_key is None:
                    continue
                key = normalize_header(raw_key)
                header_map.setdefault(key, str(raw_key))
                clean[key] = value
            normalised_records.append(clean)

        columns = list(header_map.keys())
        self.report.columns = list(header_map.values())

        # 2) find the URL column.
        url_column = self._pick_url_column(columns)
        if url_column is None:
            raise DataSourceError(
                "No target URL column found. Expected one of: "
                + ", ".join(URL_COLUMN_CANDIDATES[:8])
                + f" (found: {', '.join(header_map.values())})"
            )
        if self.config.url_column and normalize_header(self.config.url_column) != url_column:
            logger.warning(
                "--url-column %r did not match any column header; using %r instead",
                self.config.url_column,
                header_map.get(url_column, url_column),
            )
        self.report.url_column = header_map.get(url_column, url_column)

        non_field = set(NON_FIELD_COLUMNS)
        field_columns = [c for c in columns if c != url_column and c not in non_field and not c.startswith("_")]
        self.report.field_columns = [header_map.get(c, c) for c in field_columns]

        # 3) build rows, expanding multi-value cells.
        rows: list[TargetRow] = []
        skipped = 0
        for record in normalised_records:
            url = _cell_to_text(record.get(url_column))
            if not url:
                skipped += 1
                continue
            if not re.match(r"^https?://", url, re.I):
                url = "https://" + url.lstrip("/")
            if not _truthy(record.get("active", record.get("aktif", record.get("enabled"))), default=True):
                skipped += 1
                continue

            multi_values: dict[str, list[str]] = {}
            for column in field_columns:
                raw = record.get(column)
                text = _cell_to_text(raw)
                if MULTI_VALUE_SEPARATOR in text:
                    values = [v.strip() for v in text.split(MULTI_VALUE_SEPARATOR) if v.strip()]
                    if values:
                        multi_values[column] = values
                        if header_map.get(column, column) not in self.report.multi_value_columns:
                            self.report.multi_value_columns.append(header_map.get(column, column))

            fanout = max((len(v) for v in multi_values.values()), default=1)
            for variant in range(fanout):
                form_data: dict[str, str] = {}
                for column in field_columns:
                    values = multi_values.get(column)
                    if values:
                        form_data[column] = values[variant % len(values)]
                    else:
                        text = _cell_to_text(record.get(column))
                        if text:
                            form_data[column] = text
                row = self._build_row(record, url, form_data, url_column)
                if len(multi_values) and variant > 0:
                    row.name = f"{row.name or 'row'} #v{variant + 1}" if row.name else None
                    row.metadata["variant"] = variant + 1
                rows.append(row)

        self.report.expanded_rows = len(rows)
        self.report.skipped_rows = skipped
        if skipped:
            self.report.warnings.append(f"{skipped} row(s) skipped (missing URL or marked inactive)")
        if not rows:
            return []

        # 4) global overrides from config (scenario/selectors/IMAP presence).
        for row in rows:
            self._apply_global_defaults(row)
        return rows

    def _pick_url_column(self, columns: Sequence[str]) -> Optional[str]:
        explicit = normalize_header(self.config.url_column) if self.config.url_column else None
        if explicit and explicit in columns:
            return explicit
        for candidate in URL_COLUMN_CANDIDATES:
            if candidate in columns:
                return candidate
        # Fall back to the first column whose values look like URLs.
        return None

    def _build_row(
        self,
        record: dict[str, Any],
        url: str,
        form_data: dict[str, str],
        url_column: str,
    ) -> TargetRow:
        name = _cell_to_text(record.get("name") or record.get("test_name")) or None
        self._row_counter += 1
        index = self._row_counter
        slug_source = name or url
        base = slugify(slug_source, max_length=48)
        count = self._used_slug_names.get(base, 0)
        self._used_slug_names[base] = count + 1
        slug = base if count == 0 else f"{base}-{count + 1}"

        scenario = normalize_header(_cell_to_text(record.get("scenario") or record.get("senaryo")) or "")
        scenario = scenario.replace("_", "-") if scenario else ""

        row = TargetRow(
            index=index,
            target_url=url,
            form_data=form_data,
            raw={k: _cell_to_text(v) for k, v in record.items()},
            name=name or slug,
            scenario=scenario or "auto",
            selectors=self._parse_mapping(record.get("selectors") or record.get("selector_map")),
            steps=self._parse_steps(record.get("steps") or record.get("step") or record.get("workflow") or record.get("adimlar")),
            submit_selector=_cell_to_text(record.get("submit_selector")) or None,
            success_url_regex=_cell_to_text(record.get("success_url_regex")) or None,
            success_selector=_cell_to_text(record.get("success_selector")) or None,
            error_selector=_cell_to_text(record.get("error_selector")) or None,
            submit_button_text=_cell_to_text(
                record.get("submit_button_text") or record.get("submit_text")
            )
            or None,
            wait_after_submit_ms=parse_int(
                record.get("wait_after_submit_ms") or record.get("wait_after_submit"), default=0
            )
            or 0,
            requires_email_verification=_truthy(
                record.get("requires_email_verification") or record.get("email_verification"), default=False
            ),
            verification_email=_cell_to_text(record.get("verification_email") or record.get("verify_email")) or None,
            verification_email_field=_cell_to_text(record.get("verification_email_field")) or None,
            verification_link_regex=_cell_to_text(record.get("verification_link_regex")) or None,
            verification_subject_regex=_cell_to_text(record.get("verification_subject_regex")) or None,
            verification_sender=_cell_to_text(record.get("verification_sender")) or None,
            verification_otp_field=_cell_to_text(record.get("verification_otp_field")) or None,
            verification_timeout_s=parse_int(record.get("verification_timeout_s"), default=None) or None,
            resend_selector=_cell_to_text(record.get("resend_selector")) or None,
            proxy=_cell_to_text(record.get("proxy")) or None,
            iterations=parse_int(record.get("iterations") or record.get("tekrar"), default=1) or 1,
            tags=split_csv(record.get("tags") or record.get("etiketler")),
            metadata={
                "raw_row_keys": list(record.keys()),
                "source_url_column": url_column,
            },
        )

        # Non-destructive per-row overrides coming from the spreadsheet.
        for key, target in (
            ("submit_selector", "submit_selector"),
            ("success_url_regex", "success_url_regex"),
            ("success_selector", "success_selector"),
            ("error_selector", "error_selector"),
        ):
            if row.raw.get(key):
                setattr(row, target, row.raw[key])

        # ``submit`` pseudo-column: "false"/"hayir" → fill the form but never submit.
        submit_flag = _cell_to_text(record.get("submit"))
        if submit_flag:
            row.metadata["submit"] = _truthy(submit_flag, default=True)

        # ``expect_error`` pseudo-column: negative tests are expected to be *rejected*.
        expect_error = _cell_to_text(
            record.get("expect_error")
            or record.get("expect_failure")
            or record.get("expecting_error")
            or record.get("beklenen_hata")
            or record.get("negative_test")
        )
        if expect_error:
            row.metadata["expect_error"] = _truthy(expect_error, default=False)

        return row

    @staticmethod
    def _parse_mapping(raw: Any) -> dict[str, str]:
        """Parse ``"email=#email, password=input[name=pass]"``, a JSON object or a dict."""
        if _is_blank(raw):
            return {}
        if isinstance(raw, Mapping):
            return {str(key): str(value) for key, value in raw.items()}
        text = str(raw).strip()
        if text.startswith("{"):
            try:
                payload = json.loads(text)
                return {str(k): str(v) for k, v in payload.items()}
            except json.JSONDecodeError:
                logger.warning("Could not parse selector mapping as JSON, falling back to key=value: %s", truncate(text))
        mapping: dict[str, str] = {}
        for token in re.split(r"[,;\n]+", text):
            token = token.strip()
            if not token or "=" not in token:
                continue
            key, _, value = token.partition("=")
            mapping[key.strip()] = value.strip()
        return mapping

    def _parse_steps(self, raw: Any) -> list[StepAction]:
        """Parse a per-row declarative step workflow.

        Accepts a native ``list``/``dict`` (JSON data sources), a JSON string, or the compact
        ``"fill #email: {email} | click text=Kaydol"`` shorthand used inside Excel cells.
        """
        if _is_blank(raw):
            return []
        steps: list[StepAction] = []
        payload: Any = raw
        if isinstance(raw, Mapping) and "steps" in raw:
            payload = raw.get("steps")
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith(("[", "{")):
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("Row steps JSON is invalid: %s", truncate(text))
                    return []
            else:
                payload = None  # fall through to the shorthand parser below
        if isinstance(payload, Mapping):
            payload = payload.get("steps", [])
        if isinstance(payload, list):
            if payload and isinstance(payload[0], StepAction):
                return list(payload)
            if payload and isinstance(payload[0], str):
                return DataLoader._parse_steps_shorthand(" | ".join(str(item) for item in payload))
            if payload:
                for entry in payload:
                    if isinstance(entry, dict):
                        action = str(entry.get("action", "")).strip().lower()
                        if not action:
                            continue
                        steps.append(
                            StepAction(
                                action=action,
                                target=entry.get("target") or entry.get("selector") or entry.get("url"),
                                value=entry.get("value"),
                                timeout_ms=parse_int(entry.get("timeout_ms"), default=None),
                                optional=bool(entry.get("optional", False)),
                                description=str(entry.get("description", "")),
                                options={k: v for k, v in entry.items() if k not in {"action", "target", "value", "timeout_ms", "optional", "description"}},
                            )
                        )
            return steps
        if isinstance(raw, str):
            return DataLoader._parse_steps_shorthand(raw)
        return steps

    @staticmethod
    def _parse_steps_shorthand(text: str) -> list[StepAction]:
        """Parse ``"fill #email: {email} | click text=Kaydol | expect_url /welcome"``."""
        steps: list[StepAction] = []
        for token in re.split(r"[|;]+", text):
            token = token.strip()
            if not token:
                continue
            match = re.match(
                r"^(?P<action>[a-z_]+)\s+(?:(?P<target>\S+)\s*)?(?::\s*(?P<value>.+))?$",
                token,
                re.I,
            )
            if not match:
                continue
            action = match.group("action").lower()
            steps.append(
                StepAction(
                    action=action,
                    target=match.group("target"),
                    value=(match.group("value") or "").strip() or None,
                )
            )
        return steps

    def _apply_global_defaults(self, row: TargetRow) -> None:
        """Merge config-level selectors/scenario/IMAP defaults into the row."""
        if self.config.selectors:
            merged = dict(self.config.selectors)
            merged.update(row.selectors)
            row.selectors = merged
        if not row.steps and self.config.default_steps:
            row.steps = [
                StepAction(
                    action=str(entry.get("action", "")).lower(),
                    target=entry.get("target") or entry.get("selector") or entry.get("url"),
                    value=entry.get("value"),
                    timeout_ms=parse_int(entry.get("timeout_ms"), default=None),
                    optional=bool(entry.get("optional", False)),
                    description=str(entry.get("description", "")),
                    options={
                        k: v
                        for k, v in entry.items()
                        if k not in {"action", "target", "value", "timeout_ms", "optional", "description"}
                    },
                )
                for entry in self.config.default_steps
                if entry.get("action")
            ]
        if (row.verification_email or row.verification_email_field) and not row.requires_email_verification:
            row.requires_email_verification = True
        if row.requires_email_verification and not self.config.imap_enabled and not self.config.imap_host:
            self.report.warnings.append(
                f"Row '{row.name}' requires email verification but IMAP is not configured "
                "(set --imap-host/--imap-user/--imap-password or disable the column)"
            )
        # Record extra metadata for summary/report.
        if row.proxy:
            row.metadata.setdefault("requested_proxy", row.proxy)
        row.metadata.setdefault("form_field_keys", sorted(row.form_data.keys()))
        row.metadata.setdefault("has_selector_overrides", bool(row.selectors))
        row.metadata.setdefault("custom_steps", len(row.steps))

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def field_columns_for_templates(rows: Iterable[TargetRow]) -> list[str]:
        """Union of all form-data keys across rows (used for documentation/summary)."""
        keys: list[str] = []
        for row in rows:
            for key in row.form_data:
                if key not in keys:
                    keys.append(key)
        return keys

    @staticmethod
    def describe_rows(rows: Sequence[TargetRow], limit: int = 10) -> str:
        """One-line-per-row preview used by ``--print-config`` and dry runs."""
        lines: list[str] = []
        for row in rows[:limit]:
            fields = ", ".join(f"{k}={truncate(v, 18)}" for k, v in list(row.form_data.items())[:4])
            lines.append(f"    • [{row.index:>3}] {truncate(row.target_url, 70)}{(' | ' + fields) if fields else ''}")
        if len(rows) > limit:
            lines.append(f"    • … {len(rows) - limit} more row(s)")
        return "\n".join(lines)


class InlineRows:
    """Helper for programmatic usage / tests: build rows without touching the disk."""

    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self.payload = payload

    def load(self, config: Config) -> list[TargetRow]:
        loader = DataLoader(config)
        loader.report.format = "inline"
        loader.report.raw_rows = len(self.payload)
        rows = loader._normalise(self.payload)  # noqa: SLF001 - internal reuse is intentional
        for row in rows:
            loader._apply_global_defaults(row)  # noqa: SLF001
        return rows


def interpolate_row(row: TargetRow, mapping: dict[str, Any]) -> TargetRow:
    """Return a copy of *row* with ``{placeholders}`` resolved (run id, row index, uuid…)."""
    resolved_url = interpolate_deep(row.target_url, mapping)
    resolved_form = interpolate_deep(row.form_data, mapping)
    # Rebuild a lightweight copy preserving typed attributes.
    clone = TargetRow(
        index=row.index,
        target_url=str(resolved_url),
        form_data=dict(resolved_form),
        raw=row.raw,
        name=row.name,
        scenario=row.scenario,
        selectors=dict(row.selectors),
        steps=row.steps,
        submit_selector=row.submit_selector,
        success_url_regex=row.success_url_regex,
        success_selector=row.success_selector,
        error_selector=row.error_selector,
        submit_button_text=row.submit_button_text,
        wait_after_submit_ms=row.wait_after_submit_ms,
        requires_email_verification=row.requires_email_verification,
        verification_email=interpolate_deep(row.verification_email or "", mapping) or None,
        verification_email_field=row.verification_email_field,
        verification_link_regex=row.verification_link_regex,
        verification_subject_regex=row.verification_subject_regex,
        verification_sender=row.verification_sender,
        verification_otp_field=row.verification_otp_field,
        verification_timeout_s=row.verification_timeout_s,
        resend_selector=row.resend_selector,
        proxy=row.proxy,
        iterations=row.iterations,
        tags=list(row.tags),
        metadata={**row.metadata, "placeholders_resolved": True},
    )
    return clone


# Public re-exports used by tests/documentation.
__all__ += ["InlineRows", "interpolate_row", "normalize_ws", "looks_like_selector"]
