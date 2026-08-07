from datetime import date

import pytest

from src.config.archives import ARCHIVES
from src.mailer import gmail_smtp
from src.push.github_push import wait_for_pages


def test_wait_for_pages_retries_until_expected_document_is_visible():
    cfg = ARCHIVES["gov-support"]
    responses = iter(["old page", f"{cfg.title} 2026-08-07"])
    url = wait_for_pages(
        cfg,
        date(2026, 8, 7),
        timeout_seconds=1,
        interval_seconds=0,
        fetcher=lambda _: next(responses),
    )
    assert url.endswith("/2026-08-07.html")


def test_send_html_returns_real_message_id(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def login(self, user, password):
            assert user == "bot@example.com"

        def send_message(self, message):
            sent.append(message)
            return {}

    monkeypatch.setattr(gmail_smtp.smtplib, "SMTP_SSL", FakeSMTP)
    marker = gmail_smtp.send_html(
        subject="test",
        html="<b>hello</b>",
        to=["recipient@example.com"],
        user="bot@example.com",
        app_password="password",
    )
    assert marker == sent[0]["Message-ID"]
    assert marker.startswith("<") and marker.endswith(">")


def test_send_html_wraps_network_errors(monkeypatch):
    class BrokenSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("network down")

    monkeypatch.setattr(gmail_smtp.smtplib, "SMTP_SSL", BrokenSMTP)
    with pytest.raises(gmail_smtp.MailerError, match="network down"):
        gmail_smtp.send_html(
            subject="test",
            html="<b>hello</b>",
            to=["recipient@example.com"],
            user="bot@example.com",
            app_password="password",
        )
