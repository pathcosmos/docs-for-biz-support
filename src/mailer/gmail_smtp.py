"""Send HTML mail via Gmail SMTP using an app password. From: must be the same
account as the SMTP login — Google rejects mismatched From/login. Cc list is
per-archive; gov-support uses MAIL_CC_GOV_SUPPORT, the others have no Cc."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class MailerError(RuntimeError):
    pass


def send_html(
    *,
    subject: str,
    html: str,
    to: list[str],
    cc: list[str] | None = None,
    user: str | None = None,
    app_password: str | None = None,
) -> str:
    """Send one HTML email and return Gmail's accepted-recipient summary string
    (useful as a marker value). Raises MailerError on failure."""

    user = user or os.environ.get("GMAIL_USER")
    app_password = app_password or os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not app_password:
        raise MailerError("GMAIL_USER / GMAIL_APP_PASSWORD not set")
    if not to:
        raise MailerError("`to` is empty")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Reply-To"] = user
    msg["Message-ID"] = make_msgid(domain=user.split("@", 1)[-1])
    msg.set_content("This email requires an HTML-capable client.")
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
            s.login(user, app_password)
            refused = s.send_message(msg)
    except (smtplib.SMTPException, OSError, TimeoutError, ssl.SSLError) as e:
        raise MailerError(f"SMTP failure: {e}") from e

    if refused:
        raise MailerError(f"Refused recipients: {refused}")
    return str(msg["Message-ID"])
