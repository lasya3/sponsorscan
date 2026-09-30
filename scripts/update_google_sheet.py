#!/usr/bin/env python3
"""
Copy the SponsorScan report CSV files into a Google spreadsheet.

Each run replaces the contents of two worksheet tabs, one per CSV, creating the
tabs when they do not exist and freezing their header rows. Anything typed into
those two tabs by hand is overwritten on the next run, so keep notes in a tab of
your own.

Run with --check first. It confirms the key, the spreadsheet ID, and the sharing
without writing any cells, and says what to fix when something is wrong.

Required environment variables
------------------------------
GOOGLE_SERVICE_ACCOUNT_JSON
    The full contents of the service account's JSON key, or a path to the key
    file. A path is convenient locally; GitHub Actions secrets hold the contents.

GOOGLE_SPREADSHEET_ID
    The spreadsheet ID, or the full URL of the spreadsheet.

Optional environment variables
------------------------------
ALL_MATCHES_CSV
    Default: matches_48h.csv

NEW_JOBS_CSV
    Default: new_jobs_48h.csv

ALL_MATCHES_SHEET
    Tab that receives ALL_MATCHES_CSV. Default: All Matches 48h

NEW_JOBS_SHEET
    Tab that receives NEW_JOBS_CSV. Default: New Jobs 48h

The Google client libraries are optional dependencies:

    pip install -r requirements-sheets.txt
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path


DEFAULT_ALL_CSV = "matches_48h.csv"
DEFAULT_NEW_CSV = "new_jobs_48h.csv"
DEFAULT_ALL_SHEET = "All Matches 48h"
DEFAULT_NEW_SHEET = "New Jobs 48h"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# A tab created by the API starts at 1000 rows and 26 columns. Writing past the
# grid fails, so a tab is grown to fit the data. It is never shrunk.
NEW_TAB_ROWS = 1000
NEW_TAB_COLUMNS = 26

SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
SPREADSHEET_ID_RE = re.compile(r"[A-Za-z0-9_-]{20,}")

# Numbers are sent as numbers so the score columns sort numerically. A leading
# zero stays text, because it is an identifier rather than a quantity.
INTEGER_RE = re.compile(r"-?(0|[1-9]\d*)")
DECIMAL_RE = re.compile(r"-?(0|[1-9]\d*)\.\d+")


class SheetsConfigurationError(ValueError):
    """Raised when the Sheets configuration is missing or invalid."""


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SheetsConfigurationError(
            f"Required environment variable '{name}' is missing. "
            "See docs/GOOGLE_SHEETS_SETUP.md."
        )
    return value


def load_service_account_info(raw: str) -> dict:
    """The key as a dictionary, from its JSON text or from a path to the file.

    The usual mistakes each get their own message: a partial paste, a path that
    does not exist, and an OAuth client file downloaded in place of a
    service-account key.
    """
    raw = raw.strip()
    if not raw.startswith("{"):
        path = Path(raw).expanduser()
        if not path.is_file():
            raise SheetsConfigurationError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is neither JSON nor the path of an "
                "existing file. Set it to the whole contents of the downloaded "
                "key file, or to that file's path."
            )
        raw = path.read_text(encoding="utf-8-sig")

    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        raise SheetsConfigurationError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. It must hold the "
            "whole key file, from the opening { to the closing }."
        ) from None

    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise SheetsConfigurationError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not a service-account key. Create "
            "one under IAM & Admin > Service Accounts > (your account) > Keys > "
            "Add key > Create new key > JSON. An OAuth client ID file does not "
            "work here."
        )

    missing = [k for k in ("client_email", "private_key") if not info.get(k)]
    if missing:
        raise SheetsConfigurationError(
            "The service-account key is incomplete (no "
            + ", ".join(missing)
            + "). Download a fresh JSON key and use the whole file."
        )
    return info


def parse_spreadsheet_id(raw: str) -> str:
    """The ID from a bare ID or from a full spreadsheet URL."""
    raw = raw.strip()
    match = SPREADSHEET_URL_RE.search(raw)
    if match:
        return match.group(1)
    if SPREADSHEET_ID_RE.fullmatch(raw):
        return raw
    raise SheetsConfigurationError(
        "GOOGLE_SPREADSHEET_ID does not look like a spreadsheet ID. Use the "
        "part of the spreadsheet URL between /d/ and /edit, or the whole URL."
    )


def to_cell(value: str):
    """A CSV value as the cell value to send.

    Cells are written with valueInputOption RAW, so a job title beginning with
    "=" stays text instead of running as a formula.
    """
    if INTEGER_RE.fullmatch(value):
        return int(value)
    if DECIMAL_RE.fullmatch(value):
        return float(value)
    return value


def read_csv_rows(path: Path) -> list[list]:
    """Header and data rows, ready to write. An empty file gives no rows."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Report CSV not found: {path}. Generate the report first, and "
            "check that ALL_MATCHES_CSV and NEW_JOBS_CSV name the files it wrote."
        )
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return []
    header, data = rows[0], rows[1:]
    return [header] + [[to_cell(value) for value in row] for row in data]


def quote_sheet_name(title: str) -> str:
    """A tab name as an A1-notation prefix, safe for spaces and apostrophes."""
    return "'" + title.replace("'", "''") + "'"


def build_structure_requests(existing: dict[str, dict], tabs) -> list[dict]:
    """batchUpdate requests that create missing tabs, size them, and freeze row 1.

    `existing` maps a tab title to its sheet properties as the API returns them;
    `tabs` is a list of (title, rows) pairs.
    """
    requests = []
    for title, rows in tabs:
        needed_rows = max(len(rows), 2)  # the grid must be taller than the frozen row
        needed_columns = max((len(row) for row in rows), default=1)

        if title not in existing:
            # frozenRowCount belongs inside gridProperties. Directly under
            # properties, the API rejects the request.
            requests.append({
                "addSheet": {
                    "properties": {
                        "title": title,
                        "gridProperties": {
                            "rowCount": max(needed_rows, NEW_TAB_ROWS),
                            "columnCount": max(needed_columns, NEW_TAB_COLUMNS),
                            "frozenRowCount": 1,
                        },
                    }
                }
            })
            continue

        properties = existing[title]
        grid = properties.get("gridProperties", {})
        update = {"frozenRowCount": 1}
        if grid.get("rowCount", 0) < needed_rows:
            update["rowCount"] = needed_rows
        if grid.get("columnCount", 0) < needed_columns:
            update["columnCount"] = needed_columns

        requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": properties["sheetId"],
                    "gridProperties": update,
                },
                "fields": ",".join(f"gridProperties.{k}" for k in update),
            }
        })
    return requests


def fetch_spreadsheet(service, spreadsheet_id: str) -> dict:
    return service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="properties.title,sheets.properties(sheetId,title,gridProperties)",
    ).execute()


def existing_tabs(spreadsheet: dict) -> dict[str, dict]:
    return {
        sheet["properties"]["title"]: sheet["properties"]
        for sheet in spreadsheet.get("sheets", [])
    }


def confirm_write_access(service, spreadsheet_id: str, title: str) -> None:
    """Fail unless the service account can edit, without changing anything.

    Reading works with Viewer access, so a read alone would pass a sheet that
    the real run then cannot write. Setting the title to its current value is a
    write that leaves the spreadsheet as it was.
    """
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "updateSpreadsheetProperties": {
                "properties": {"title": title},
                "fields": "title",
            }
        }]},
    ).execute()


def sync(service, spreadsheet_id: str, tabs) -> str:
    """Replace each tab's contents with its rows. Returns the spreadsheet title."""
    spreadsheet = fetch_spreadsheet(service, spreadsheet_id)
    requests = build_structure_requests(existing_tabs(spreadsheet), tabs)

    spreadsheets = service.spreadsheets()
    spreadsheets.batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()

    spreadsheets.values().batchClear(
        spreadsheetId=spreadsheet_id,
        body={"ranges": [quote_sheet_name(title) for title, _ in tabs]},
    ).execute()

    data = [
        {"range": f"{quote_sheet_name(title)}!A1", "values": rows}
        for title, rows in tabs
        if rows
    ]
    if data:
        spreadsheets.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()

    return spreadsheet.get("properties", {}).get("title", spreadsheet_id)


def explain_error(exc: Exception, client_email: str, project_id: str) -> str:
    """Turn a Google API failure into the step that fixes it.

    Duck-typed on the error's attributes so the explanations can be tested
    without the Google client libraries installed.
    """
    if type(exc).__name__ == "RefreshError":
        return (
            "Google rejected the service-account key. It may have been deleted "
            "or disabled in Google Cloud. Create a new JSON key for "
            f"{client_email} and update GOOGLE_SERVICE_ACCOUNT_JSON."
        )

    status = getattr(getattr(exc, "resp", None), "status", None)
    content = getattr(exc, "content", b"")
    detail = content.decode("utf-8", "replace") if isinstance(content, bytes) \
        else str(content)
    detail = f"{detail} {exc}"

    if status == 403 and ("SERVICE_DISABLED" in detail
                          or "has not been used" in detail
                          or "is disabled" in detail):
        return (
            "The Google Sheets API is not enabled in the Google Cloud project "
            f"'{project_id}'. Enable it at "
            "https://console.cloud.google.com/apis/library/sheets.googleapis.com"
            f"?project={project_id} then wait a minute and try again."
        )
    if status == 403:
        return (
            "The service account cannot edit this spreadsheet. Open the "
            f"spreadsheet, click Share, and add {client_email} as an Editor. "
            "Viewer or Commenter access is not enough."
        )
    if status == 404:
        return (
            "No spreadsheet has this ID. Copy it again from the spreadsheet's "
            "URL: the part between /d/ and /edit."
        )
    if status is not None:
        return f"Google Sheets returned HTTP {status}: {exc}"
    return f"{type(exc).__name__}: {exc}"


def build_service(info: dict):
    """An authorized Sheets API client. Imported lazily: the libraries are optional."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise SheetsConfigurationError(
            "The Google client libraries are not installed. Run: "
            "pip install -r requirements-sheets.txt"
        ) from None

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def data_rows(rows) -> int:
    return max(len(rows) - 1, 0)


def run_check(service, spreadsheet_id, tabs_config) -> int:
    spreadsheet = fetch_spreadsheet(service, spreadsheet_id)
    title = spreadsheet.get("properties", {}).get("title", spreadsheet_id)
    confirm_write_access(service, spreadsheet_id, title)
    print(f"Spreadsheet: {title} (editable)")

    present = existing_tabs(spreadsheet)
    for tab, csv_path in tabs_config:
        state = "exists" if tab in present else "will be created on the first run"
        source = "found" if csv_path.is_file() else "not generated yet"
        print(f"  Tab '{tab}': {state}. Source {csv_path}: {source}.")

    print("Check passed. Nothing was written.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy the SponsorScan report CSV files into a Google spreadsheet.")
    parser.add_argument(
        "--check", action="store_true",
        help="Confirm the key, spreadsheet ID and sharing without writing cells")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    client_email = project_id = ""

    try:
        info = load_service_account_info(require_env("GOOGLE_SERVICE_ACCOUNT_JSON"))
        spreadsheet_id = parse_spreadsheet_id(require_env("GOOGLE_SPREADSHEET_ID"))
        client_email = info["client_email"]
        project_id = info.get("project_id", "")

        tabs_config = [
            (os.getenv("ALL_MATCHES_SHEET", "").strip() or DEFAULT_ALL_SHEET,
             Path(os.getenv("ALL_MATCHES_CSV", "").strip() or DEFAULT_ALL_CSV)),
            (os.getenv("NEW_JOBS_SHEET", "").strip() or DEFAULT_NEW_SHEET,
             Path(os.getenv("NEW_JOBS_CSV", "").strip() or DEFAULT_NEW_CSV)),
        ]
        if tabs_config[0][0] == tabs_config[1][0]:
            raise SheetsConfigurationError(
                "ALL_MATCHES_SHEET and NEW_JOBS_SHEET name the same tab, so one "
                "report would overwrite the other."
            )

        # Read before authenticating, so a missing report fails fast.
        tabs = None if args.check else [
            (tab, read_csv_rows(path)) for tab, path in tabs_config]

        print(f"Service account: {client_email}")
        service = build_service(info)

        if args.check:
            return run_check(service, spreadsheet_id, tabs_config)

        title = sync(service, spreadsheet_id, tabs)
        counts = ", ".join(f"'{tab}' {data_rows(rows):,} rows" for tab, rows in tabs)
        print(f"Updated {title}: {counts}.")
        return 0

    except (SheetsConfigurationError, FileNotFoundError) as exc:
        print(f"Google Sheets update failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # the Google libraries raise several unrelated types
        print("Google Sheets update failed: "
              + explain_error(exc, client_email, project_id), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
