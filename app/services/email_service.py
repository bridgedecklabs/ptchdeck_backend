import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import resend
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


def send_invite_email(recipient_email: str, invite_token: str, firm_name: str) -> bool:
    try:
        resend.api_key = settings.RESEND_API_KEY
        invite_link = f"https://ptchdeck.com/invite?token={invite_token}"
        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
          <h2 style="color:#1a1a1a">You've been invited to join {firm_name} on PtchDeck</h2>
          <p>You have been invited to join {firm_name} on PtchDeck.</p>
          <p style="margin:32px 0">
            <a href="{invite_link}"
               style="background:#000;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600">
              Accept Invite
            </a>
          </p>
          <p style="color:#888;font-size:13px">
            This link expires in 48 hours.<br>
            If you didn't expect this invite, you can ignore this email.
          </p>
        </div>
        """
        resend.Emails.send({
            "from": "PtchDeck <noreply@ptchdeck.com>",
            "to": [recipient_email],
            "subject": f"You've been invited to join {firm_name} on PtchDeck",
            "html": html,
        })
        return True
    except Exception as e:
        print(f"Invite email send failed: {e}")
        return False


def send_application_confirmation_email(
    founder_email: str,
    founder_name: str,
    company_name: str,
    cohort_name: str,
) -> bool:
    try:
        resend.api_key = settings.RESEND_API_KEY
        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
          <h2 style="color:#1a1a1a">Application received</h2>
          <p>Hi {founder_name},</p>
          <p>We've received your application for <strong>{company_name}</strong> to <strong>{cohort_name}</strong>.</p>
          <p>We'll review your submission and be in touch if we'd like to move forward.</p>
          <p style="color:#888;font-size:13px">
            You're receiving this because you submitted an application via PtchDeck.
          </p>
        </div>
        """
        resend.Emails.send({
            "from": "PtchDeck <noreply@ptchdeck.com>",
            "to": [founder_email],
            "subject": f"Application received — {cohort_name}",
            "html": html,
        })
        return True
    except Exception as e:
        print(f"Confirmation email send failed: {e}")
        return False
