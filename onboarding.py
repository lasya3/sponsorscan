#!/usr/bin/env python3
"""
Onboarding support for SponsorScan: the `doctor` checks and the `setup` wizard.

The pipeline's usual failure is silence. Every stage is long-running and cached,
so a misconfiguration surfaces as an empty CSV rather than an error, and a user
cannot tell which stage broke. `doctor` inspects each stage and names the one
that needs attention.

Checks are pure functions. They take an open connection, a profile dictionary,
or a path, and return a `CheckResult`. None of them opens a database, reads an
environment variable, or prints, so they can be tested against an in-memory
database with no network and no filesystem.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from profile_loader import ProfileError, load_profile

# Postings older than this suggest the jobs table predates the current run.
STALE_AFTER_DAYS = 7

# Above this, a profile filters out nearly everything. Most matches land
# between 95 and 120 against the default scoring.
HIGH_SCORE_THRESHOLD = 130

# Hard imports in sponsorscan.py: without these nothing runs.
REQUIRED_MODULES = ("requests", "yaml")

# Degraded-but-working without them. rapidfuzz is guarded by HAVE_RAPIDFUZZ,
# and openpyxl is imported lazily and only for .xlsx input.
OPTIONAL_MODULES = {
    "openpyxl": "reading .xlsx LCA files; .csv still works",
    "rapidfuzz": "fuzzy employer matching; falls back to exact matches",
}

EMAIL_ENV = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NOTIFICATION_EMAIL")
SHEETS_ENV = ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SPREADSHEET_ID")


@dataclass
class CheckResult:
    """One line of `doctor` output.

    `status` is "OK", "WARN", or "FAIL". Only "FAIL" affects the exit code.
    `remedy` is the command that fixes the problem, or None when there is
    nothing to run.
    """

    name: str
    status: str
    message: str
    remedy: str | None = None


def _table_count(con, table: str) -> int | None:
    """Row count for `table`, or None when the table does not exist."""
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        return None


def check_lca_loaded(con) -> CheckResult:
    """The employers table is populated by `load-lca`."""
    count = _table_count(con, "employers")
    if not count:
        return CheckResult(
            "LCA data", "FAIL", "No employer records loaded",
            "python sponsorscan.py load-lca <file.xlsx> --replace")
    return CheckResult("LCA data", "OK", f"{count:,} employers loaded")


def check_jobs_fetched(con) -> CheckResult:
    """The jobs table is populated by `fetch-jobs`."""
    count = _table_count(con, "jobs")
    if not count:
        return CheckResult(
            "Live postings", "FAIL", "No postings fetched",
            "python sponsorscan.py fetch-jobs --replace")
    return CheckResult("Live postings", "OK", f"{count:,} postings stored")


def warn_stale_jobs(con, today: date) -> CheckResult:
    """Warn when the newest posting predates the staleness window.

    `today` is passed in rather than read from the clock so the result is
    reproducible.
    """
    try:
        newest = con.execute(
            "SELECT MAX(posted) FROM jobs WHERE posted IS NOT NULL "
            "AND posted <> ''").fetchone()[0]
    except Exception:
        newest = None

    if not newest:
        return CheckResult("Posting freshness", "OK", "No dated postings to judge")

    try:
        newest_date = datetime.strptime(newest[:10], "%Y-%m-%d").date()
    except ValueError:
        return CheckResult("Posting freshness", "OK", "No usable posting dates")

    age = (today - newest_date).days
    described = f"Newest posting is {age} day{'' if age == 1 else 's'} old"
    if age > STALE_AFTER_DAYS:
        return CheckResult(
            "Posting freshness", "WARN", described,
            "python sponsorscan.py fetch-jobs --replace")
    return CheckResult("Posting freshness", "OK", described)


def check_profile(path) -> CheckResult:
    """The profile parses, merges over the defaults, and validates."""
    try:
        profile = load_profile(path)
    except ProfileError as exc:
        return CheckResult("Profile", "FAIL", str(exc))
    return CheckResult(
        "Profile", "OK",
        f"{profile['profile_id']} ({profile['work_authorization']})")


def warn_score_threshold(profile) -> CheckResult:
    """A hand-raised score floor silently empties the report."""
    score = profile.get("minimum_score", 0)
    if score > HIGH_SCORE_THRESHOLD:
        return CheckResult(
            "Score threshold", "WARN",
            f"minimum_score is {score}; most matches score 95-120")
    return CheckResult("Score threshold", "OK", f"minimum_score is {score}")


def warn_empty_targeting(profile) -> CheckResult:
    """Without roles or skills every posting ranks the same."""
    if not profile.get("target_roles") and not profile.get("skills"):
        return CheckResult(
            "Targeting", "WARN",
            "No target_roles and no skills; every posting will rank alike")
    return CheckResult("Targeting", "OK", "Roles or skills configured")


def warn_notification_env(profile, env) -> CheckResult:
    """Notifications are enabled in the profile but unconfigured in the shell."""
    notifications = profile.get("notifications") or {}
    missing = []
    if notifications.get("email_enabled"):
        missing += [k for k in EMAIL_ENV if not env.get(k)]
    if notifications.get("google_sheets_enabled"):
        missing += [k for k in SHEETS_ENV if not env.get(k)]

    if missing:
        return CheckResult(
            "Notification config", "WARN",
            "Enabled in the profile but unset: " + ", ".join(missing))
    return CheckResult("Notification config", "OK", "Nothing missing")


def check_companies_file(path) -> CheckResult:
    """`companies.yaml` parses and lists at least one board."""
    path = Path(path)
    if not path.exists():
        return CheckResult(
            "Company list", "FAIL", f"{path} not found",
            "python sponsorscan.py discover")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return CheckResult("Company list", "FAIL", f"{path} is not valid YAML: {exc}")

    providers = data.get("companies") or {}
    total = sum(len(entries or []) for entries in providers.values())
    if not total:
        return CheckResult(
            "Company list", "FAIL", f"{path} lists no boards",
            "python sponsorscan.py discover")
    return CheckResult(
        "Company list", "OK",
        f"{total} boards across {len(providers)} providers")


def exit_code(results) -> int:
    """1 when any check failed, so `doctor` works as a CI preflight step."""
    return 1 if any(r.status == "FAIL" for r in results) else 0


def check_dependencies(modules=REQUIRED_MODULES) -> CheckResult:
    """Every module the pipeline imports is installed."""
    missing = [m for m in modules if importlib.util.find_spec(m) is None]
    if missing:
        return CheckResult(
            "Dependencies", "FAIL", "Not installed: " + ", ".join(missing),
            "pip install -r requirements.txt")
    return CheckResult("Dependencies", "OK", "All installed")


def warn_optional_dependencies(modules=OPTIONAL_MODULES) -> CheckResult:
    """Absent optional modules degrade behaviour rather than breaking it."""
    missing = [f"{name} ({why})" for name, why in modules.items()
               if importlib.util.find_spec(name) is None]
    if missing:
        return CheckResult(
            "Optional dependencies", "WARN", "Not installed: " + ", ".join(missing),
            "pip install -r requirements.txt")
    return CheckResult("Optional dependencies", "OK", "All installed")


def check_database(path) -> CheckResult:
    """The SQLite file exists. Its contents are judged by the later checks."""
    path = Path(path)
    if not path.exists():
        return CheckResult(
            "Database", "FAIL", f"{path} not found",
            "python sponsorscan.py load-lca <file.xlsx> --replace")
    size_mb = path.stat().st_size / (1024 * 1024)
    return CheckResult("Database", "OK", f"{path.name} ({size_mb:.1f} MB)")


def warn_authorization(profile) -> CheckResult:
    """Surface contradictions between work_authorization and the reject_ flags.

    Imported lazily: sponsor_daily_report imports sponsorscan, which imports
    this module, so a module-level import would close the cycle.
    """
    from sponsor_daily_report import authorization_warnings

    warnings = authorization_warnings(profile)
    if warnings:
        return CheckResult("Authorization", "WARN", " ".join(warnings))
    return CheckResult("Authorization", "OK", "Consistent with the reject flags")


def format_results(results) -> str:
    """Render check results as the aligned block `doctor` prints."""
    lines = []
    for r in results:
        lines.append(f"  {r.status:<5} {r.name:<22} {r.message}".rstrip())
        if r.remedy:
            lines.append(f"        -> {r.remedy}")

    failed = sum(1 for r in results if r.status == "FAIL")
    warned = sum(1 for r in results if r.status == "WARN")

    if not failed and not warned:
        summary = "All checks passed"
    else:
        parts = []
        if failed:
            parts.append(f"{failed} failed")
        if warned:
            parts.append(f"{warned} warning" + ("s" if warned != 1 else ""))
        summary = ", ".join(parts)

    return "\n".join(lines) + "\n\n" + summary


def run_checks(db_path, companies_path, profile_path, env, today) -> list[CheckResult]:
    """Run every check in pipeline order and return the results.

    Checks are ordered so the first failure is the earliest broken stage, but
    every check still runs, so one invocation shows the whole picture. Stages
    that cannot be reached are skipped rather than reported as failures: with
    no database there is nothing to say about its contents.
    """
    results = [check_dependencies(), warn_optional_dependencies()]

    db_result = check_database(db_path)
    results.append(db_result)

    if db_result.status == "OK":
        con = sqlite3.connect(db_path)
        try:
            results.append(check_lca_loaded(con))
            results.append(check_jobs_fetched(con))
            results.append(warn_stale_jobs(con, today=today))
        finally:
            con.close()

    results.append(check_companies_file(companies_path))

    if profile_path is not None:
        profile_result = check_profile(profile_path)
        results.append(profile_result)
        if profile_result.status == "OK":
            profile = load_profile(profile_path)
            results.append(warn_score_threshold(profile))
            results.append(warn_empty_targeting(profile))
            results.append(warn_authorization(profile))
            results.append(warn_notification_env(profile, env=env))

    return results
