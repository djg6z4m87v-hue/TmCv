"""Dashboard reminders and optional SMTP notification; no background worker required."""
import os
import smtplib
import ssl
from datetime import date, timedelta
from email.message import EmailMessage
from database import connect

def upcoming_deadlines(days=7):
    today = date.today()
    end = today + timedelta(days=days)
    with connect() as db:
        rows = db.execute("""SELECT id,title,company,deadline,status FROM jobs
            WHERE deadline != '' AND status NOT IN ('Applied','Offer','Rejected','Withdrawn')
            AND deadline <= ? ORDER BY deadline ASC""", (end.isoformat(),)).fetchall()
    return [dict(row) for row in rows]

def notify_deadlines():
    rows = upcoming_deadlines()
    recipient = os.getenv('ALERT_EMAIL_TO', '').strip()
    host = os.getenv('SMTP_HOST', '').strip()
    if not rows or not recipient or not host:
        return 0
    today = date.today().isoformat()
    with connect() as db:
        previous = db.execute("SELECT value FROM settings WHERE key='last_deadline_alert'").fetchone()
        if previous and previous['value'] == today:
            return 0
    msg = EmailMessage()
    msg['Subject'] = f'Careerpilot: {len(rows)} upcoming or overdue application deadlines'
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = recipient
    msg.set_content('Review these deadlines and verify them on employer websites:\n\n' + '\n'.join(
        f"{row['deadline']} — {row['title']} at {row['company']} ({row['status']})" for row in rows))
    port = int(os.getenv('SMTP_PORT', '465'))
    with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as smtp:
        if os.getenv('SMTP_USER'):
            smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
        smtp.send_message(msg)
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('last_deadline_alert',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (today,))
    return len(rows)
