import smtplib
from email.message import EmailMessage

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from sleuth.config import Config

MAGIC_LINK_SALT = "magic-link"
DEFAULT_MAX_AGE_SECONDS = 15 * 60  # 15 minutes


def _serializer(config: Config) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.session_secret, salt=MAGIC_LINK_SALT)


def send_magic_link(email: str, base_url: str, config: Config) -> None:
    token = _serializer(config).dumps(email)
    link = f"{base_url}/auth/email/verify?token={token}"

    message = EmailMessage()
    message["Subject"] = "Your Sleuth login link"
    message["From"] = config.smtp_from_address
    message["To"] = email
    message.set_content(f"Click to log in to Sleuth: {link}\n\nThis link expires in 15 minutes.")

    with smtplib.SMTP(config.smtp_host, config.smtp_port) as smtp:
        smtp.starttls()
        smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


def verify_magic_link_token(token: str, config: Config, max_age: int = DEFAULT_MAX_AGE_SECONDS) -> str | None:
    try:
        return _serializer(config).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
