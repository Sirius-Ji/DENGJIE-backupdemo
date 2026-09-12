import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dengjie.db")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    fallback_llm_base_url: str = os.getenv("FALLBACK_LLM_BASE_URL", "")
    fallback_llm_api_key: str = os.getenv("FALLBACK_LLM_API_KEY", "")
    term_start: str = os.getenv("TERM_START", "2026-09-07")


settings = Settings()