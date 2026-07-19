"""
Phase 3 - Email notifications via Gmail SMTP.

Uses a Gmail "App Password" (not your real Gmail password) so the
script never touches your actual account credentials.

One-time setup:
  1. Turn on 2-Step Verification on your Google account, if not already:
     https://myaccount.google.com/signinoptions/two-step-verification
  2. Generate an App Password: https://myaccount.google.com/apppasswords
     (choose "Mail" as the app -- it gives you a 16-character code)
  3. Copy .env.example to .env and fill in:
       EMAIL_SENDER=your_gmail_address@gmail.com
       EMAIL_APP_PASSWORD=the 16-character app password
       EMAIL_RECIPIENT=where you want notifications sent
"""

import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(subject, body):
    """Send a plain-text email. Returns True on success, False on failure
    (and prints why, rather than crashing the whole run over a bad email)."""
    sender = os.environ.get("EMAIL_SENDER")
    app_password = os.environ.get("EMAIL_APP_PASSWORD")
    recipient = os.environ.get("EMAIL_RECIPIENT")

    if not all([sender, app_password, recipient]):
        print("Email not sent -- missing EMAIL_SENDER / EMAIL_APP_PASSWORD / "
              "EMAIL_RECIPIENT in your .env file. See .env.example.")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(sender, app_password)
            server.sendmail(sender, [recipient], msg.as_string())
        print(f"Email sent to {recipient}.")
        return True
    except smtplib.SMTPAuthenticationError:
        print("Email failed: Gmail rejected the login. Double check that "
              "EMAIL_APP_PASSWORD is an App Password, not your real Gmail "
              "password, and that 2-Step Verification is enabled.")
        return False
    except Exception as e:
        print(f"Email failed: {e}")
        return False