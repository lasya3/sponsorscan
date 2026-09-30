"""The Sheets uploader runs unattended, so every configuration mistake has to
come back as the step that fixes it rather than a Google stack trace."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "update_google_sheet", REPO / "scripts" / "update_google_sheet.py")
sheet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sheet)

KEY = {
    "type": "service_account",
    "project_id": "sponsorscan-123",
    "client_email": "bot@sponsorscan-123.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
}
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"


# --------------------------------------------------------------- a fake client

class _Call:
    def __init__(self, log, name, kwargs, result):
        self.log, self.name, self.kwargs, self.result = log, name, kwargs, result

    def execute(self):
        self.log.append((self.name, self.kwargs))
        return self.result


class FakeService:
    """Records calls in the order they execute, like the real client chain
    service.spreadsheets().values().batchClear(...).execute()."""

    def __init__(self, tabs=None, title="Job Hunt"):
        self.log = []
        self.spreadsheet = {
            "properties": {"title": title},
            "sheets": [{"properties": p} for p in (tabs or [])],
        }

    def spreadsheets(self):
        return self

    def values(self):
        return SimpleNamespace(
            batchClear=lambda **kw: _Call(self.log, "batchClear", kw, {}),
            batchUpdate=lambda **kw: _Call(self.log, "values.batchUpdate", kw, {}),
        )

    def get(self, **kw):
        return _Call(self.log, "get", kw, self.spreadsheet)

    def batchUpdate(self, **kw):
        return _Call(self.log, "batchUpdate", kw, {})

    def calls(self, name):
        return [kw for n, kw in self.log if n == name]


def http_error(status, content=b""):
    """Shaped like googleapiclient.errors.HttpError."""
    exc = Exception(f"<HttpError {status}>")
    exc.resp = SimpleNamespace(status=status)
    exc.content = content
    return exc


# ------------------------------------------------------------------ the key

def test_key_is_read_from_json_text():
    assert sheet.load_service_account_info(json.dumps(KEY))["client_email"] \
        == KEY["client_email"]


def test_key_is_read_from_a_file_path(tmp_path):
    path = tmp_path / "key.json"
    path.write_text(json.dumps(KEY), encoding="utf-8")
    assert sheet.load_service_account_info(str(path))["project_id"] == "sponsorscan-123"


def test_oauth_client_file_is_rejected_with_the_right_download():
    oauth = json.dumps({"installed": {"client_id": "x"}})
    with pytest.raises(sheet.SheetsConfigurationError, match="Add key"):
        sheet.load_service_account_info(oauth)


def test_partial_paste_is_reported_as_invalid_json():
    with pytest.raises(sheet.SheetsConfigurationError, match="opening"):
        sheet.load_service_account_info('{"type": "service_account",')


def test_neither_json_nor_a_file_is_reported():
    with pytest.raises(sheet.SheetsConfigurationError, match="neither JSON"):
        sheet.load_service_account_info("C:/no/such/key.json")


# --------------------------------------------------------- the spreadsheet ID

def test_spreadsheet_id_is_taken_from_a_full_url():
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0"
    assert sheet.parse_spreadsheet_id(url) == SHEET_ID


def test_bare_spreadsheet_id_is_accepted():
    assert sheet.parse_spreadsheet_id(f"  {SHEET_ID} ") == SHEET_ID


def test_junk_spreadsheet_id_names_where_to_copy_it():
    with pytest.raises(sheet.SheetsConfigurationError, match="/d/"):
        sheet.parse_spreadsheet_id("My Job Sheet")


# ------------------------------------------------------------------ the rows

def test_numbers_become_numbers_so_scores_sort():
    assert sheet.to_cell("112") == 112
    assert sheet.to_cell("7.5") == 7.5


def test_leading_zeros_and_formulas_stay_text():
    assert sheet.to_cell("02139") == "02139"
    assert sheet.to_cell("=HYPERLINK(1)") == "=HYPERLINK(1)"


def test_csv_header_stays_text_and_data_is_converted(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text("\ufeffcombined_score,company\n112,Stripe\n", encoding="utf-8")
    assert sheet.read_csv_rows(path) == [["combined_score", "company"], [112, "Stripe"]]


def test_missing_csv_says_to_generate_the_report(tmp_path):
    with pytest.raises(FileNotFoundError, match="Generate the report"):
        sheet.read_csv_rows(tmp_path / "absent.csv")


def test_tab_names_are_quoted_for_a1_notation():
    assert sheet.quote_sheet_name("Pranav's Jobs") == "'Pranav''s Jobs'"


# ---------------------------------------------------------- tab structure

def test_new_tab_freezes_the_header_inside_grid_properties():
    [request] = sheet.build_structure_requests({}, [("New Jobs 48h", [["a"]])])
    properties = request["addSheet"]["properties"]
    assert "frozenRowCount" not in properties
    assert properties["gridProperties"]["frozenRowCount"] == 1


def test_existing_tab_grows_to_fit_but_never_shrinks():
    existing = {"All": {"sheetId": 7,
                        "gridProperties": {"rowCount": 1000, "columnCount": 26}}}
    big = [["h"] * 30] + [["x"] * 30] * 1500
    [grow] = sheet.build_structure_requests(existing, [("All", big)])
    update = grow["updateSheetProperties"]
    assert update["properties"]["sheetId"] == 7
    assert update["properties"]["gridProperties"] == {
        "frozenRowCount": 1, "rowCount": 1501, "columnCount": 30}

    [small] = sheet.build_structure_requests(existing, [("All", [["h"]])])
    assert small["updateSheetProperties"]["fields"] == "gridProperties.frozenRowCount"


# ------------------------------------------------------------------ the sync

def test_sync_creates_clears_and_writes_each_tab():
    service = FakeService(tabs=[{"sheetId": 1, "title": "All Matches 48h",
                                 "gridProperties": {"rowCount": 1000, "columnCount": 26}}])
    tabs = [("All Matches 48h", [["company"], ["Stripe"]]),
            ("New Jobs 48h", [["company"]])]

    assert sheet.sync(service, SHEET_ID, tabs) == "Job Hunt"

    [structure] = service.calls("batchUpdate")
    kinds = [next(iter(r)) for r in structure["body"]["requests"]]
    assert kinds == ["updateSheetProperties", "addSheet"]

    [cleared] = service.calls("batchClear")
    assert cleared["body"]["ranges"] == ["'All Matches 48h'", "'New Jobs 48h'"]

    [written] = service.calls("values.batchUpdate")
    assert written["body"]["valueInputOption"] == "RAW"
    assert [d["range"] for d in written["body"]["data"]] == [
        "'All Matches 48h'!A1", "'New Jobs 48h'!A1"]

    order = [name for name, _ in service.log]
    assert order.index("batchClear") < order.index("values.batchUpdate")


def test_check_writes_no_cells(tmp_path, capsys):
    service = FakeService()
    tabs = [("All Matches 48h", tmp_path / "a.csv"), ("New Jobs 48h", tmp_path / "n.csv")]
    assert sheet.run_check(service, SHEET_ID, tabs) == 0
    assert not service.calls("batchClear")
    assert not service.calls("values.batchUpdate")
    # The one write is the no-op title update that proves Editor access.
    [probe] = service.calls("batchUpdate")
    assert probe["body"]["requests"][0]["updateSpreadsheetProperties"]["properties"] \
        == {"title": "Job Hunt"}
    assert "will be created" in capsys.readouterr().out


# ------------------------------------------------------ explaining failures

def test_forbidden_names_the_account_to_share_with():
    message = sheet.explain_error(http_error(403, b"The caller does not have permission"),
                                  KEY["client_email"], "sponsorscan-123")
    assert KEY["client_email"] in message
    assert "Editor" in message


def test_disabled_api_links_to_the_enable_page():
    message = sheet.explain_error(
        http_error(403, b'"reason": "SERVICE_DISABLED"'),
        KEY["client_email"], "sponsorscan-123")
    assert "sheets.googleapis.com?project=sponsorscan-123" in message


def test_not_found_points_at_the_url():
    message = sheet.explain_error(http_error(404), KEY["client_email"], "p")
    assert "/d/" in message


def test_revoked_key_asks_for_a_new_one():
    RefreshError = type("RefreshError", (Exception,), {})
    message = sheet.explain_error(RefreshError("invalid_grant"), KEY["client_email"], "p")
    assert "new JSON key" in message


# ------------------------------------------------------------------ main()

def test_main_names_the_missing_variable(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    assert sheet.main([]) == 1
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in capsys.readouterr().err


def test_main_refuses_one_tab_for_both_reports(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(KEY))
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", SHEET_ID)
    monkeypatch.setenv("ALL_MATCHES_SHEET", "Jobs")
    monkeypatch.setenv("NEW_JOBS_SHEET", "Jobs")
    assert sheet.main([]) == 1
    assert "same tab" in capsys.readouterr().err


def test_main_explains_an_api_failure(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(KEY))
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", SHEET_ID)

    def refuse(_info):
        raise http_error(403)

    monkeypatch.setattr(sheet, "build_service", refuse)
    assert sheet.main(["--check"]) == 1
    assert "add bot@sponsorscan-123" in capsys.readouterr().err


def test_main_syncs_both_reports(monkeypatch, tmp_path, capsys):
    all_csv, new_csv = tmp_path / "all.csv", tmp_path / "new.csv"
    all_csv.write_text("company\nStripe\nPlaid\n", encoding="utf-8")
    new_csv.write_text("company\n", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(KEY))
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", SHEET_ID)
    monkeypatch.setenv("ALL_MATCHES_CSV", str(all_csv))
    monkeypatch.setenv("NEW_JOBS_CSV", str(new_csv))
    monkeypatch.delenv("ALL_MATCHES_SHEET", raising=False)
    monkeypatch.delenv("NEW_JOBS_SHEET", raising=False)

    service = FakeService()
    monkeypatch.setattr(sheet, "build_service", lambda _info: service)

    assert sheet.main([]) == 0
    assert "'All Matches 48h' 2 rows, 'New Jobs 48h' 0 rows" in capsys.readouterr().out
