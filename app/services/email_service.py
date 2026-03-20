import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings

def send_contact_email(name: str, email: str, message: str) -> bool:
    try:
        msg = MIMEMultipart()
        msg["From"] = settings.SMTP_USER
        msg["To"] = settings.CONTACT_TO_EMAIL
        msg["Subject"] = f"PtchDeck Contact: {name or 'Anonymous'}"
        msg["Reply-To"] = email
        body = f"""New contact message from PtchDeck website:\n\nName: {name}\nEmail: {email}\n\nMessage:\n{message}"""
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False
