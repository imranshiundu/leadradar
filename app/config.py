from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_host: str = '0.0.0.0'
    app_port: int = 8080
    app_base_url: str = 'http://localhost:8080'
    database_path: str = './lead_radar.db'
    sources_path: str = './config/sources.example.json'
    profile_path: str = './config/profile.example.json'
    user_agent: str = 'LeadRadarSafe/1.0 (+contact@example.com)'

    groq_api_key: str | None = None
    groq_model: str = 'llama-3.1-8b-instant'
    ai_enabled: bool = True

    brave_search_api_key: str | None = None
    search_max_results: int = 10

    telegram_bot_token: str | None = None
    telegram_owner_chat_id: str | None = None
    telegram_allowed_chat_ids: str | None = None
    telegram_polling_enabled: bool = True

    smtp_host: str = 'smtp.gmail.com'
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_app_password: str | None = None
    smtp_from_name: str = 'Imran'
    smtp_from_email: str | None = None
    smtp_daily_limit: int = 25
    smtp_min_seconds_between_sends: int = 300

    # Reply tracking over IMAPS.
    imap_host: str = 'imap.gmail.com'
    imap_port: int = 993
    inbox_poll_enabled: bool = True
    inbox_poll_minutes: int = 10

    # Optional shared-secret auth for mutating dashboard/API routes.
    dashboard_token: str | None = None

    # Dashboard authentication (session-based).
    auth_enabled: bool = True
    admin_email: str = 'Imranshiundu@gmail.com'
    admin_password: str | None = None

    auto_send_emails: bool = False
    require_manual_approval: bool = True
    respect_robots_txt: bool = True
    crawl_delay_seconds: float = 2.0
    max_leads_per_run: int = 80
    min_need_score_for_alert: int = 65
    min_opportunity_score_for_alert: int = 70

    def allowed_chat_ids(self) -> set[str]:
        if not self.telegram_allowed_chat_ids:
            return set()
        return {x.strip() for x in self.telegram_allowed_chat_ids.split(',') if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
