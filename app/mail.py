"""Outgoing email. In file mode messages are written to var/outbox (never logged), for development and tests."""
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from .config import get_settings


def send(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if s.email_backend == "smtp" and s.smtp_host:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as c:
            if s.smtp_use_tls:
                c.starttls()
            if s.smtp_user:
                c.login(s.smtp_user, s.smtp_password)
            c.send_message(msg)
        return
    out = Path("var/outbox")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{int(time.time() * 1000)}-{to.replace('@', '_at_')}.txt").write_text(f"To: {to}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
