from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    FRONTEND_URL: str = "http://localhost:5173"

    # SMTP — only needed when email sending is active
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    CONTACT_TO_EMAIL: str = "hello@ptchdeck.com"

    class Config:
        env_file = ".env"

settings = Settings()
