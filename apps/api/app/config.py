from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://commerceradar:commerceradar@localhost:5432/commerceradar"
    redis_url: str = "redis://localhost:6379/0"
    # Identify ourselves honestly to sites; add a contact URL once the public domain is live.
    bot_user_agent: str = "CommerceRadarBot/0.2"
    product_pages_per_run: int = 20  # per store per extraction job
    product_page_delay: float = 1.0  # seconds between product pages (robots Crawl-delay wins if larger)
    urlscan_api_key: str = ""

    # ---- accounts / billing (M8) ----
    session_days: int = 30
    allow_registration: bool = True  # first registered user becomes platform admin
    public_web_url: str = "http://localhost:3000"
    stripe_secret_key: str = ""  # billing is disabled until all three are set
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_business: str = ""

    # ---- monitoring (spec §15); every interval is an env var, e.g. RECHECK_ACTIVE_DAYS=30 ----
    monitor_tick_seconds: int = 600  # how often the scheduler tick runs
    monitor_rechecks_per_tick: int = 10  # due stores re-verified per tick
    monitor_backlog_per_tick: int = 60  # max never-verified candidates queued per tick
    monitor_max_in_flight: int = 40  # adaptive: only top the queue up to this many jobs (measured bottleneck)
    monitor_candidate_rechecks_per_tick: int = 5
    auto_extract_products: bool = True  # product re-check after every positive store check
    recheck_new_store_days: float = 7
    recheck_active_days: float = 21
    recheck_changed_days: float = 2
    recheck_inactive_days: float = 60
    retry_transient_days: float = 1
    retry_negative_days: float = 3
    retry_inconclusive_days: float = 14
    candidate_recheck_days: float = 30
    new_store_window_days: int = 30
    changed_window_days: int = 14
    inactive_after_negative_checks: int = 3
    inactive_after_failed_checks: int = 6
    inactive_min_days_since_success: int = 90
    stuck_after_minutes: int = 120
    max_job_attempts: int = 6
    signals_refresh_per_tick: int = 200

    # ---- scale / operations (M9) ----
    worker_concurrency: int = 2  # same value the worker is started with; used for capacity estimates
    vps_monthly_cost_usd: float = 0  # optional: enables the $/store estimate in /admin/metrics
    job_evidence_retention_days: int = 90  # older, superseded verification evidence is compacted
    compact_per_tick: int = 500  # optional; urlscan search works keyless at lower limits


settings = Settings()
