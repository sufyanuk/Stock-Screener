"""E-mail (SMTP) and Telegram notifications for newly qualifying Top-5 stocks. Each
(user, session, ticker, channel) is sent at most once (unique constraint)."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import NotificationLog, Recommendation, User


def _message(rec: Recommendation) -> str:
    return (f"New qualifying stock #{rec.rank}: {rec.ticker} (score {rec.score:.1f}) at ${rec.price_at_rec:.2f}. "
            f"Entry {rec.entry}, stop {rec.stop}, T1 {rec.target1}. Not investment advice.")


def send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise RuntimeError("SMTP not configured")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from or s.smtp_user, to, subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


def send_telegram(chat_id: str, text: str) -> None:
    tok = get_settings().telegram_bot_token
    if not tok:
        raise RuntimeError("Telegram not configured")
    r = httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
    r.raise_for_status()


def notify_new(db: Session, recs: list[Recommendation]) -> int:
    users = db.scalars(select(User).where((User.notify_email.is_(True)) | (User.notify_telegram.is_(True)))).all()
    sent = 0
    for rec in recs:
        for u in users:
            for channel, enabled in (("email", u.notify_email), ("telegram", u.notify_telegram and u.telegram_chat_id)):
                if not enabled:
                    continue
                if db.scalars(select(NotificationLog).where(NotificationLog.user_id == u.id,
                                                            NotificationLog.session_date == rec.session_date,
                                                            NotificationLog.ticker == rec.ticker,
                                                            NotificationLog.channel == channel)).first():
                    continue
                ok, detail = True, None
                try:
                    if channel == "email":
                        send_email(u.email, f"Stock Intel: {rec.ticker} qualified", _message(rec))
                    else:
                        send_telegram(u.telegram_chat_id, _message(rec))
                    sent += 1
                except Exception as e:  # logged, never fatal to a scan
                    ok, detail = False, repr(e)[:500]
                db.add(NotificationLog(user_id=u.id, session_date=rec.session_date, ticker=rec.ticker,
                                       channel=channel, ok=ok, detail=detail))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
    return sent
