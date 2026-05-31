"""notify.py — 사람 승인/검수 알림(메일). 검수 웹페이지 직링크 포함."""
import smtplib
from email.mime.text import MIMEText


def notify_review(job_id, cfg, reason="검수 대기"):
    n = cfg["notify"]
    link = f"{n['review_base_url']}/{job_id}"
    body = f"[{reason}] 작업 {job_id}\n검수: {link}\n"
    if not n.get("smtp_host"):
        return {"sent": False, "link": link, "body": body}  # 미설정 시 링크만 반환
    msg = MIMEText(body)
    msg["Subject"] = f"[제안서 변환] {reason} — {job_id}"
    msg["From"] = n["from"]
    msg["To"] = ", ".join(n["to"])
    with smtplib.SMTP(n["smtp_host"]) as s:
        s.send_message(msg)
    return {"sent": True, "link": link}
