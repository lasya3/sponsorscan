"""Failed boards reach the notification email, which is the only place most
users look. A wrong slug would otherwise disappear without a trace."""

import importlib.util
import smtplib
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "send_job_email", REPO / "scripts" / "send_job_email.py")
email = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(email)

ROW = {"company": "Stripe", "title": "Software Engineer", "url": "https://x"}
FAILURES = [("lever/plaid", "404 Client Error")]


def test_failures_are_read_from_the_database(tmp_path):
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE fetch_failures (board TEXT, error TEXT, failed_at TEXT)")
    con.execute("INSERT INTO fetch_failures VALUES ('lever/plaid', '404 Client Error', 'x')")
    con.commit()
    con.close()
    assert email.load_failures(db) == FAILURES


def test_missing_database_or_table_means_no_failures(tmp_path):
    assert email.load_failures(tmp_path / "absent.db") == []
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    assert email.load_failures(db) == []


def test_failed_boards_appear_in_both_bodies():
    text = email.build_plain_text([ROW], "Me", "48", FAILURES)
    page = email.build_html([ROW], "Me", "48", FAILURES)
    for body in (text, page):
        assert "lever/plaid" in body
        assert "404 Client Error" in body


def test_no_failure_section_when_every_board_answered():
    assert "failed" not in email.build_plain_text([ROW], "Me", "48").lower()
    assert "failed" not in email.build_html([ROW], "Me", "48").lower()


# --------------------------------------------------------- setting it up

def test_app_password_pasted_with_googles_spaces_still_works():
    assert email.normalize_app_password(" abcd efgh ijkl mnop ") == "abcdefghijklmnop"


def test_rejected_login_explains_the_app_password():
    exc = smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
    message = email.explain_smtp_error(exc, "me@gmail.com")
    assert "me@gmail.com" in message
    assert "apppasswords" in message


def _credentials(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("NOTIFICATION_EMAIL", "you@example.com")


def test_check_logs_in_without_sending(monkeypatch, capsys):
    _credentials(monkeypatch)
    logins, sent = [], []
    monkeypatch.setattr(email, "verify_login",
                        lambda sender, password, host, port: logins.append(password))
    monkeypatch.setattr(email, "send_email", lambda **kw: sent.append(kw))

    assert email.main(["--check"]) == 0
    assert logins == ["abcdefghijklmnop"]
    assert sent == []
    assert "Nothing was sent" in capsys.readouterr().out


def test_check_explains_a_rejected_login(monkeypatch, capsys):
    _credentials(monkeypatch)

    def reject(*_args):
        raise smtplib.SMTPAuthenticationError(535, b"BadCredentials")

    monkeypatch.setattr(email, "verify_login", reject)
    assert email.main(["--check"]) == 1
    assert "App Password" in capsys.readouterr().err


def test_missing_variable_points_at_the_guide(monkeypatch, capsys):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    assert email.main(["--check"]) == 1
    assert "docs/EMAIL_SETUP.md" in capsys.readouterr().err
