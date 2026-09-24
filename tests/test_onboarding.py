"""Onboarding checks and the setup wizard's profile assembly."""

import sqlite3
from datetime import date

import pytest

from onboarding import (
    check_companies_file,
    check_database,
    check_dependencies,
    format_results,
    run_checks,
    check_jobs_fetched,
    check_lca_loaded,
    check_profile,
    exit_code,
    warn_empty_targeting,
    warn_authorization,
    warn_optional_dependencies,
    warn_notification_env,
    warn_score_threshold,
    warn_stale_jobs,
)


@pytest.fixture
def con():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE employers (
            employer_norm    TEXT PRIMARY KEY,
            employer_display TEXT,
            certified        INTEGER DEFAULT 0
        );
        CREATE TABLE jobs (
            job_key TEXT PRIMARY KEY,
            company TEXT,
            title   TEXT,
            posted  TEXT
        );
    """)
    yield connection
    connection.close()


def test_lca_check_fails_when_no_employers_loaded(con):
    result = check_lca_loaded(con)
    assert result.status == "FAIL"
    assert "load-lca" in result.remedy


def test_lca_check_passes_and_reports_the_employer_count(con):
    con.executemany(
        "INSERT INTO employers (employer_norm, certified) VALUES (?, ?)",
        [("acme", 5), ("globex", 12)])
    result = check_lca_loaded(con)
    assert result.status == "OK"
    assert "2" in result.message


def add_job(con, key, posted):
    con.execute(
        "INSERT INTO jobs (job_key, company, title, posted) VALUES (?, ?, ?, ?)",
        (key, "Acme", "Software Engineer", posted))


def test_jobs_check_fails_when_nothing_has_been_fetched(con):
    result = check_jobs_fetched(con)
    assert result.status == "FAIL"
    assert "fetch-jobs" in result.remedy


def test_jobs_check_passes_and_reports_the_posting_count(con):
    add_job(con, "a", "2026-09-20")
    add_job(con, "b", "2026-09-21")
    result = check_jobs_fetched(con)
    assert result.status == "OK"
    assert "2" in result.message


def test_stale_jobs_warn_when_the_newest_posting_is_over_a_week_old(con):
    add_job(con, "a", "2026-09-01")
    result = warn_stale_jobs(con, today=date(2026, 9, 23))
    assert result.status == "WARN"
    assert "fetch-jobs" in result.remedy


def test_recent_jobs_do_not_warn(con):
    add_job(con, "a", "2026-09-20")
    result = warn_stale_jobs(con, today=date(2026, 9, 23))
    assert result.status == "OK"


# --------------------------------------------------------------- profile checks

def write_profile(tmp_path, data):
    import json
    path = tmp_path / "p.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_profile_check_fails_and_surfaces_the_validation_error(tmp_path):
    path = write_profile(tmp_path, {"profile_id": "x", "minimum_score": -5})
    result = check_profile(path)
    assert result.status == "FAIL"
    assert "minimum_score" in result.message


def test_profile_check_fails_when_the_file_is_absent(tmp_path):
    result = check_profile(tmp_path / "nope.json")
    assert result.status == "FAIL"


def test_profile_check_passes_on_a_valid_profile(tmp_path):
    path = write_profile(tmp_path, {"profile_id": "casey", "work_authorization": "opt"})
    result = check_profile(path)
    assert result.status == "OK"
    assert "casey" in result.message


def test_high_minimum_score_warns(tmp_path):
    assert warn_score_threshold({"minimum_score": 140}).status == "WARN"


def test_default_minimum_score_does_not_warn(tmp_path):
    assert warn_score_threshold({"minimum_score": 95}).status == "OK"


def test_empty_roles_and_skills_warn():
    result = warn_empty_targeting({"target_roles": [], "skills": {}})
    assert result.status == "WARN"


def test_populated_targeting_does_not_warn():
    result = warn_empty_targeting(
        {"target_roles": ["Software Engineer"], "skills": {"Python": 7}})
    assert result.status == "OK"


def test_enabled_email_without_credentials_warns():
    profile = {"notifications": {"email_enabled": True, "google_sheets_enabled": False}}
    result = warn_notification_env(profile, env={})
    assert result.status == "WARN"
    assert "GMAIL_ADDRESS" in result.message


def test_enabled_email_with_credentials_does_not_warn():
    profile = {"notifications": {"email_enabled": True, "google_sheets_enabled": False}}
    env = {"GMAIL_ADDRESS": "a@b.com", "GMAIL_APP_PASSWORD": "x",
           "NOTIFICATION_EMAIL": "c@d.com"}
    assert warn_notification_env(profile, env=env).status == "OK"


def test_disabled_notifications_do_not_warn():
    profile = {"notifications": {"email_enabled": False, "google_sheets_enabled": False}}
    assert warn_notification_env(profile, env={}).status == "OK"


# ------------------------------------------------------------- companies.yaml

def test_companies_check_fails_when_the_file_is_missing(tmp_path):
    result = check_companies_file(tmp_path / "absent.yaml")
    assert result.status == "FAIL"
    assert "discover" in result.remedy


def test_companies_check_counts_boards_across_providers(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text(
        "companies:\n"
        "  greenhouse:\n"
        "    - {slug: stripe, name: Stripe}\n"
        "    - {slug: airbnb, name: Airbnb}\n"
        "  lever:\n"
        "    - {slug: netflix, name: Netflix}\n",
        encoding="utf-8")
    result = check_companies_file(path)
    assert result.status == "OK"
    assert "3" in result.message


def test_companies_check_fails_when_no_boards_are_listed(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("companies: {}\n", encoding="utf-8")
    assert check_companies_file(path).status == "FAIL"


# ------------------------------------------------------------------ exit code

def test_exit_code_is_one_when_any_check_fails():
    from onboarding import CheckResult
    results = [CheckResult("a", "OK", ""), CheckResult("b", "FAIL", "")]
    assert exit_code(results) == 1


def test_warnings_alone_do_not_fail_the_run():
    from onboarding import CheckResult
    results = [CheckResult("a", "OK", ""), CheckResult("b", "WARN", "")]
    assert exit_code(results) == 0


# --------------------------------------------------------- environment checks

def test_dependency_check_fails_and_names_the_missing_module():
    result = check_dependencies(["requests", "a_module_that_does_not_exist"])
    assert result.status == "FAIL"
    assert "a_module_that_does_not_exist" in result.message
    assert "pip install" in result.remedy


def test_dependency_check_passes_when_all_present():
    assert check_dependencies(["json", "sqlite3"]).status == "OK"


def test_database_check_fails_when_the_file_is_absent(tmp_path):
    result = check_database(tmp_path / "missing.db")
    assert result.status == "FAIL"
    assert "load-lca" in result.remedy


def test_database_check_passes_and_reports_the_path(tmp_path):
    db = tmp_path / "sponsorscan.db"
    db.write_bytes(b"x" * 2048)
    result = check_database(db)
    assert result.status == "OK"
    assert "sponsorscan.db" in result.message


def test_contradictory_authorization_warns():
    # An OPT profile that also rejects OPT-excluded postings is consistent;
    # a citizen profile claiming OPT rules is not.
    profile = {
        "work_authorization": "us_citizen",
        "reject_citizenship_required": True,
    }
    assert warn_authorization(profile).status == "WARN"


def test_consistent_authorization_does_not_warn():
    profile = {
        "work_authorization": "opt",
        "reject_citizenship_required": True,
        "reject_permanent_authorization_required": True,
        "reject_opt_excluded": True,
    }
    assert warn_authorization(profile).status == "OK"


# --------------------------------------------------------------------- output

def test_format_shows_status_message_and_remedy():
    from onboarding import CheckResult
    text = format_results([
        CheckResult("LCA data", "OK", "48,201 employers loaded"),
        CheckResult("Live postings", "FAIL", "No postings fetched",
                    "python sponsorscan.py fetch-jobs --replace"),
    ])
    assert "OK" in text
    assert "48,201 employers loaded" in text
    assert "fetch-jobs" in text


def test_format_summarises_counts():
    from onboarding import CheckResult
    text = format_results([
        CheckResult("a", "FAIL", ""),
        CheckResult("b", "WARN", ""),
        CheckResult("c", "OK", ""),
    ])
    assert "1 failed" in text
    assert "1 warning" in text


# --------------------------------------------------------------- the full run

def build_db(tmp_path, employers=0, jobs=0):
    db = tmp_path / "sponsorscan.db"
    connection = sqlite3.connect(db)
    connection.executescript("""
        CREATE TABLE employers (employer_norm TEXT PRIMARY KEY, certified INTEGER);
        CREATE TABLE jobs (job_key TEXT PRIMARY KEY, company TEXT, title TEXT,
                           posted TEXT);
    """)
    for i in range(employers):
        connection.execute("INSERT INTO employers VALUES (?, ?)", (f"e{i}", 5))
    for i in range(jobs):
        connection.execute("INSERT INTO jobs VALUES (?, ?, ?, ?)",
                           (f"j{i}", "Acme", "Engineer", "2026-09-22"))
    connection.commit()
    connection.close()
    return db


def test_run_checks_reports_the_earliest_broken_stage(tmp_path):
    db = build_db(tmp_path, employers=10, jobs=0)
    results = run_checks(db_path=db, companies_path=tmp_path / "none.yaml",
                         profile_path=None, env={}, today=date(2026, 9, 23))
    failed = [r for r in results if r.status == "FAIL"]
    assert any("postings" in r.message.lower() for r in failed)
    assert exit_code(results) == 1


def test_run_checks_passes_on_a_complete_setup(tmp_path):
    db = build_db(tmp_path, employers=10, jobs=5)
    companies = tmp_path / "companies.yaml"
    companies.write_text(
        "companies:\n  greenhouse:\n    - {slug: stripe, name: Stripe}\n",
        encoding="utf-8")
    profile = write_profile(tmp_path, {"profile_id": "casey",
                                       "work_authorization": "opt",
                                       "target_roles": ["Software Engineer"]})
    results = run_checks(db_path=db, companies_path=companies,
                         profile_path=profile, env={}, today=date(2026, 9, 23))
    assert exit_code(results) == 0


def test_run_checks_without_a_profile_skips_profile_checks(tmp_path):
    db = build_db(tmp_path, employers=10, jobs=5)
    companies = tmp_path / "companies.yaml"
    companies.write_text(
        "companies:\n  greenhouse:\n    - {slug: stripe, name: Stripe}\n",
        encoding="utf-8")
    results = run_checks(db_path=db, companies_path=companies,
                         profile_path=None, env={}, today=date(2026, 9, 23))
    assert exit_code(results) == 0
    assert not any(r.name == "Profile" and r.status == "FAIL" for r in results)


def test_missing_optional_module_warns_rather_than_failing():
    # rapidfuzz is guarded by HAVE_RAPIDFUZZ and openpyxl is imported lazily,
    # so their absence degrades behaviour instead of breaking the pipeline.
    result = warn_optional_dependencies({"absent_module": "fuzzy matching"})
    assert result.status == "WARN"
    assert "absent_module" in result.message
    assert "fuzzy matching" in result.message


def test_present_optional_modules_do_not_warn():
    assert warn_optional_dependencies({"json": "whatever"}).status == "OK"


# ------------------------------------------------------------------- CLI wiring

def test_doctor_subcommand_reports_a_missing_database(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo / "sponsorscan.py"), "doctor",
         "--db", str(tmp_path / "absent.db")],
        capture_output=True, text=True, cwd=str(repo))
    assert result.returncode == 1
    assert "load-lca" in result.stdout


def test_doctor_subcommand_succeeds_on_a_healthy_setup(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    db = build_db(tmp_path, employers=10, jobs=5)
    result = subprocess.run(
        [sys.executable, str(repo / "sponsorscan.py"), "doctor", "--db", str(db)],
        capture_output=True, text=True, cwd=str(repo))
    assert result.returncode == 0, result.stdout + result.stderr


def test_format_names_the_check_alongside_its_message():
    from onboarding import CheckResult
    text = format_results([CheckResult("Dependencies", "OK", "All installed")])
    assert "Dependencies" in text
    assert "All installed" in text


def test_freshness_message_uses_singular_for_one_day(con):
    add_job(con, "a", "2026-09-22")
    result = warn_stale_jobs(con, today=date(2026, 9, 23))
    assert "1 day old" in result.message
    assert "1 days" not in result.message
