from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    FRONTEND_URL: str = "http://localhost:5173"
    ENVIRONMENT: str = "development"

    # SMTP — only needed when email sending is active
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    CONTACT_TO_EMAIL: str = "hello@ptchdeck.com"

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""

    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_SERVICE_ACCOUNT_PATH: str = "firebase_service_account.json"
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""  # JSON string for production (no file needed)

    RESEND_API_KEY: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
