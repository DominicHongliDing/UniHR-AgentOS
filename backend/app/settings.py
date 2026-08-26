from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    data_dir: Path = Path(__file__).resolve().parents[1] / "data"
    database_path: Path | None = None
    workflow_checkpoint_dsn: str = (
        "postgresql://unihr:unihr_local_only@127.0.0.1:55432/"
        "unihr_workflows?sslmode=disable"
    )
    quality_database_dsn: str = (
        "postgresql://unihr:unihr_local_only@127.0.0.1:55432/"
        "unihr_workflows?sslmode=disable"
    )
    quality_allow_sqlite_fallback: bool = True
    quality_worker_poll_seconds: float = Field(default=1.0, ge=0.2, le=30)
    quality_worker_lease_seconds: int = Field(default=600, ge=30, le=7200)
    quality_max_p95_duration_ms: int = Field(default=60_000, ge=0)
    quality_max_average_cost_cny: float = Field(default=0, ge=0)
    quality_max_total_cost_cny: float = Field(default=0, ge=0)
    quality_min_pricing_coverage: float = Field(default=0, ge=0, le=1)
    quality_budget_enforcement: str = "warning"
    model_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_api_key: str = ""
    router_model: str = "qwen3.7-flash"
    router_fallback_model: str = "qwen-plus"
    primary_model: str = "qwen3.8-max-preview"
    primary_fallback_model: str = "qwen3.7-max"
    primary_last_resort_model: str = "qwen-plus"
    document_model: str = "qwen3.7-max"
    responses_base_url: str = ""
    primary_reasoning_effort: str = "high"
    vision_model: str = "qwen3.7-plus"
    ocr_model: str = "qwen-vl-ocr"
    ocr_max_pages: int = Field(default=8, ge=1, le=30)
    ocr_review_confidence: float = Field(default=0.82, ge=0, le=1)
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimensions: int = 768
    rerank_enabled: bool = True
    rerank_model: str = "qwen3-rerank"
    rerank_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    rerank_min_score: float = Field(default=0.05, ge=0, le=1)
    skill_semantic_retrieval_enabled: bool = True
    skill_semantic_min_score: float = Field(default=0.32, ge=0, le=1)
    skill_candidate_rerank_enabled: bool = True
    request_timeout_seconds: float = Field(default=90.0, gt=0, le=300)
    max_upload_bytes: int = 20 * 1024 * 1024
    max_context_chars: int = 36_000
    context_input_cost_cny_per_million: float = Field(default=0.0, ge=0)
    model_pricing_json: str = "{}"
    knowledge_source_root: Path | None = None
    default_tenant_id: str = "tenant_unihr_demo"
    default_tenant_name: str = "UniHR 演示组织"
    admin_username: str = "admin"
    admin_password: str = "UniHR@2026"
    demo_username: str = "hr-user"
    demo_password: str = "UniHR@2026"
    session_cookie_name: str = "unihr_session"
    session_expire_days: int = Field(default=7, ge=1, le=30)
    excel_automation_enabled: bool = False
    excel_executable: Path = Path(r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE")
    excel_automation_timeout_seconds: int = Field(default=90, ge=15, le=300)
    word_automation_enabled: bool = False
    word_executable: Path = Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE")
    word_automation_timeout_seconds: int = Field(default=90, ge=15, le=300)
    public_search_enabled: bool = True
    public_search_model: str = "qwen-plus"
    public_search_strategy: str = "max"
    public_search_timeout_seconds: float = Field(default=25.0, gt=0, le=120)
    public_search_max_results: int = Field(default=6, ge=1, le=10)
    public_search_freshness_days: int | None = Field(default=30, ge=1, le=3650)
    public_search_min_results: int = Field(default=4, ge=1, le=10)
    cma_weather_enabled: bool = True
    cma_weather_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    exa_mcp_enabled: bool = False
    exa_mcp_url: str = "https://mcp.exa.ai/mcp"
    tavily_mcp_enabled: bool = False
    tavily_mcp_url: str = "https://mcp.tavily.com/mcp"
    tavily_api_key: str = ""
    firecrawl_mcp_enabled: bool = False
    firecrawl_mcp_url: str = "https://mcp.firecrawl.dev/v2/mcp"
    firecrawl_api_key: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_channel_enabled: bool = False
    feishu_channel_mode: str = "webhook"
    feishu_channel_verification_token: str = ""
    feishu_channel_encrypt_key: str = ""
    feishu_channel_default_user_id: str = ""
    feishu_channel_poll_seconds: float = Field(default=0.5, ge=0.2, le=30)
    feishu_channel_lease_seconds: int = Field(default=600, ge=30, le=7200)
    feishu_channel_retry_limit: int = Field(default=3, ge=1, le=10)
    feishu_external_relay_enabled: bool = False
    feishu_external_relay_secret: str = ""
    feishu_external_max_clock_skew_seconds: int = Field(default=300, ge=30, le=3600)
    feishu_oauth_redirect_uri: str = "http://127.0.0.1:8088/api/v1/connections/feishu/callback"
    feishu_oauth_scopes: str = "offline_access calendar:calendar.free_busy:read calendar:calendar.event:create contact:user.base:readonly contact:department.base:readonly docs:document:search im:message bitable:app drive:file:upload"
    connector_token_encryption_key: str = ""
    public_open_timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    public_open_max_pages: int = Field(default=4, ge=1, le=8)
    public_open_max_bytes: int = Field(default=600_000, ge=10_000, le=5_000_000)
    public_open_max_chars: int = Field(default=18_000, ge=1_000, le=80_000)

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("UNIHR_DATA_DIR", cls.model_fields["data_dir"].default))
        database_value = os.getenv("UNIHR_DATABASE_PATH", "").strip()
        checkpoint_dsn = os.getenv("UNIHR_WORKFLOW_POSTGRES_DSN", "").strip()
        quality_dsn = os.getenv("UNIHR_QUALITY_POSTGRES_DSN", "").strip()
        knowledge_value = os.getenv("UNIHR_KNOWLEDGE_SOURCE_ROOT", "").strip()
        external_relay_secret = os.getenv(
            "UNIHR_FEISHU_EXTERNAL_RELAY_SECRET", ""
        ).strip()
        if not external_relay_secret:
            jwt_secret = os.getenv("APP_JWT_SECRET_KEY", "").strip()
            if jwt_secret:
                external_relay_secret = hashlib.sha256(
                    f"unihr-feishu-external-relay:{jwt_secret}".encode("utf-8")
                ).hexdigest()
        return cls(
            data_dir=data_dir,
            database_path=Path(database_value) if database_value else data_dir / "unihr.db",
            workflow_checkpoint_dsn=(
                checkpoint_dsn or cls.model_fields["workflow_checkpoint_dsn"].default
            ),
            quality_database_dsn=(
                quality_dsn or checkpoint_dsn or cls.model_fields["quality_database_dsn"].default
            ),
            quality_allow_sqlite_fallback=os.getenv(
                "UNIHR_QUALITY_ALLOW_SQLITE_FALLBACK", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            quality_worker_poll_seconds=float(
                os.getenv("UNIHR_QUALITY_WORKER_POLL_SECONDS", "1")
            ),
            quality_worker_lease_seconds=int(
                os.getenv("UNIHR_QUALITY_WORKER_LEASE_SECONDS", "600")
            ),
            quality_max_p95_duration_ms=int(
                os.getenv("UNIHR_QUALITY_MAX_P95_DURATION_MS", "60000")
            ),
            quality_max_average_cost_cny=float(
                os.getenv("UNIHR_QUALITY_MAX_AVERAGE_COST_CNY", "0")
            ),
            quality_max_total_cost_cny=float(
                os.getenv("UNIHR_QUALITY_MAX_TOTAL_COST_CNY", "0")
            ),
            quality_min_pricing_coverage=float(
                os.getenv("UNIHR_QUALITY_MIN_PRICING_COVERAGE", "0")
            ),
            quality_budget_enforcement=(
                os.getenv("UNIHR_QUALITY_BUDGET_ENFORCEMENT", "warning").strip().casefold()
                if os.getenv("UNIHR_QUALITY_BUDGET_ENFORCEMENT", "warning").strip().casefold()
                in {"warning", "blocking"} else "warning"
            ),
            model_base_url=os.getenv(
                "UNIHR_MODEL_BASE_URL",
                os.getenv("OPENAI_COMPATIBLE_BASE_URL", cls.model_fields["model_base_url"].default),
            ),
            model_api_key=os.getenv("UNIHR_BAILIAN_API_KEY", "")
            or os.getenv("DASHSCOPE_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", ""),
            router_model=os.getenv("UNIHR_ROUTER_MODEL", "qwen3.7-flash"),
            router_fallback_model=os.getenv("UNIHR_ROUTER_FALLBACK_MODEL", "qwen-plus"),
            primary_model=os.getenv("UNIHR_PRIMARY_MODEL", "qwen3.8-max-preview"),
            primary_fallback_model=os.getenv("UNIHR_PRIMARY_FALLBACK_MODEL", "qwen3.7-max"),
            primary_last_resort_model=os.getenv("UNIHR_PRIMARY_LAST_RESORT_MODEL", "qwen-plus"),
            document_model=os.getenv("UNIHR_DOCUMENT_MODEL", "qwen3.7-max"),
            responses_base_url=os.getenv("UNIHR_RESPONSES_BASE_URL", ""),
            primary_reasoning_effort=os.getenv("UNIHR_PRIMARY_REASONING_EFFORT", "high"),
            vision_model=os.getenv("UNIHR_VISION_MODEL", "qwen3.7-plus"),
            ocr_model=os.getenv("UNIHR_OCR_MODEL", "qwen-vl-ocr"),
            ocr_max_pages=int(os.getenv("UNIHR_OCR_MAX_PAGES", "8")),
            ocr_review_confidence=float(os.getenv("UNIHR_OCR_REVIEW_CONFIDENCE", "0.82")),
            embedding_model=os.getenv("UNIHR_EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            embedding_dimensions=int(os.getenv("UNIHR_EMBEDDING_DIMENSIONS", "768")),
            rerank_enabled=os.getenv(
                "UNIHR_RERANK_ENABLED", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            rerank_model=os.getenv("UNIHR_RERANK_MODEL", "qwen3-rerank"),
            rerank_timeout_seconds=float(os.getenv("UNIHR_RERANK_TIMEOUT_SECONDS", "20")),
            rerank_min_score=float(os.getenv("UNIHR_RERANK_MIN_SCORE", "0.05")),
            skill_semantic_retrieval_enabled=os.getenv(
                "UNIHR_SKILL_SEMANTIC_RETRIEVAL_ENABLED", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            skill_semantic_min_score=float(
                os.getenv("UNIHR_SKILL_SEMANTIC_MIN_SCORE", "0.32")
            ),
            skill_candidate_rerank_enabled=os.getenv(
                "UNIHR_SKILL_CANDIDATE_RERANK_ENABLED", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            request_timeout_seconds=float(os.getenv("UNIHR_MODEL_TIMEOUT_SECONDS", "90")),
            max_upload_bytes=int(os.getenv("UNIHR_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            max_context_chars=int(os.getenv("UNIHR_MAX_CONTEXT_CHARS", "36000")),
            context_input_cost_cny_per_million=float(
                os.getenv("UNIHR_CONTEXT_INPUT_COST_CNY_PER_MILLION", "0")
            ),
            model_pricing_json=os.getenv("UNIHR_MODEL_PRICING_JSON", "{}"),
            # The open-source build never scans a developer-specific folder.
            # Knowledge enters the system only through an explicit path or the upload API.
            knowledge_source_root=(
                Path(knowledge_value) if knowledge_value else data_dir / "knowledge-source"
            ),
            default_tenant_id=os.getenv("UNIHR_DEFAULT_TENANT_ID", "tenant_unihr_demo"),
            default_tenant_name=os.getenv("UNIHR_DEFAULT_TENANT_NAME", "UniHR 演示组织"),
            admin_username=os.getenv("UNIHR_ADMIN_USERNAME", "admin"),
            admin_password=os.getenv("UNIHR_ADMIN_PASSWORD", "UniHR@2026"),
            demo_username=os.getenv("UNIHR_DEMO_USERNAME", "hr-user"),
            demo_password=os.getenv("UNIHR_DEMO_PASSWORD", "UniHR@2026"),
            session_cookie_name=os.getenv("UNIHR_SESSION_COOKIE_NAME", "unihr_session"),
            session_expire_days=int(os.getenv("UNIHR_SESSION_EXPIRE_DAYS", "7")),
            excel_automation_enabled=os.getenv("UNIHR_EXCEL_AUTOMATION_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"},
            excel_executable=Path(os.getenv(
                "UNIHR_EXCEL_EXECUTABLE",
                r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
            )),
            excel_automation_timeout_seconds=int(os.getenv("UNIHR_EXCEL_AUTOMATION_TIMEOUT_SECONDS", "90")),
            word_automation_enabled=os.getenv(
                "UNIHR_WORD_AUTOMATION_ENABLED", "false"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            word_executable=Path(os.getenv(
                "UNIHR_WORD_EXECUTABLE",
                r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
            )),
            word_automation_timeout_seconds=int(os.getenv("UNIHR_WORD_AUTOMATION_TIMEOUT_SECONDS", "90")),
            public_search_enabled=os.getenv(
                "UNIHR_PUBLIC_SEARCH_ENABLED", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            public_search_model=os.getenv("UNIHR_PUBLIC_SEARCH_MODEL", "qwen-plus"),
            public_search_strategy=os.getenv("UNIHR_PUBLIC_SEARCH_STRATEGY", "max"),
            public_search_timeout_seconds=float(os.getenv("UNIHR_PUBLIC_SEARCH_TIMEOUT_SECONDS", "25")),
            public_search_max_results=int(os.getenv("UNIHR_PUBLIC_SEARCH_MAX_RESULTS", "6")),
            public_search_freshness_days=(
                int(os.getenv("UNIHR_PUBLIC_SEARCH_FRESHNESS_DAYS", "30"))
                if os.getenv("UNIHR_PUBLIC_SEARCH_FRESHNESS_DAYS", "30").strip()
                else None
            ),
            public_search_min_results=int(os.getenv("UNIHR_PUBLIC_SEARCH_MIN_RESULTS", "4")),
            cma_weather_enabled=os.getenv(
                "UNIHR_CMA_WEATHER_ENABLED", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            cma_weather_timeout_seconds=float(os.getenv("UNIHR_CMA_WEATHER_TIMEOUT_SECONDS", "10")),
            exa_mcp_enabled=os.getenv(
                "UNIHR_EXA_MCP_ENABLED", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            exa_mcp_url=os.getenv("UNIHR_EXA_MCP_URL", "https://mcp.exa.ai/mcp"),
            tavily_mcp_enabled=os.getenv(
                "UNIHR_TAVILY_MCP_ENABLED", "false"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            tavily_mcp_url=os.getenv("UNIHR_TAVILY_MCP_URL", "https://mcp.tavily.com/mcp"),
            tavily_api_key=os.getenv("UNIHR_TAVILY_API_KEY", ""),
            firecrawl_mcp_enabled=os.getenv(
                "UNIHR_FIRECRAWL_MCP_ENABLED", "false"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            firecrawl_mcp_url=os.getenv(
                "UNIHR_FIRECRAWL_MCP_URL", "https://mcp.firecrawl.dev/v2/mcp"
            ),
            firecrawl_api_key=os.getenv("UNIHR_FIRECRAWL_API_KEY", ""),
            feishu_app_id=os.getenv("UNIHR_FEISHU_APP_ID", ""),
            feishu_app_secret=os.getenv("UNIHR_FEISHU_APP_SECRET", ""),
            feishu_channel_enabled=os.getenv(
                "UNIHR_FEISHU_CHANNEL_ENABLED", "false"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            feishu_channel_mode=(
                os.getenv("UNIHR_FEISHU_CHANNEL_MODE", "webhook").strip().casefold()
                if os.getenv("UNIHR_FEISHU_CHANNEL_MODE", "webhook").strip().casefold()
                in {"webhook", "long_connection"} else "webhook"
            ),
            feishu_channel_verification_token=os.getenv(
                "UNIHR_FEISHU_CHANNEL_VERIFICATION_TOKEN", ""
            ),
            feishu_channel_encrypt_key=os.getenv(
                "UNIHR_FEISHU_CHANNEL_ENCRYPT_KEY", ""
            ),
            feishu_channel_default_user_id=os.getenv(
                "UNIHR_FEISHU_CHANNEL_DEFAULT_USER_ID", ""
            ),
            feishu_channel_poll_seconds=float(
                os.getenv("UNIHR_FEISHU_CHANNEL_POLL_SECONDS", "0.5")
            ),
            feishu_channel_lease_seconds=int(
                os.getenv("UNIHR_FEISHU_CHANNEL_LEASE_SECONDS", "600")
            ),
            feishu_channel_retry_limit=int(
                os.getenv("UNIHR_FEISHU_CHANNEL_RETRY_LIMIT", "3")
            ),
            feishu_external_relay_enabled=os.getenv(
                "UNIHR_FEISHU_EXTERNAL_RELAY_ENABLED", "false"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            feishu_external_relay_secret=external_relay_secret,
            feishu_external_max_clock_skew_seconds=int(os.getenv(
                "UNIHR_FEISHU_EXTERNAL_MAX_CLOCK_SKEW_SECONDS", "300"
            )),
            feishu_oauth_redirect_uri=os.getenv(
                "UNIHR_FEISHU_OAUTH_REDIRECT_URI",
                "http://127.0.0.1:8088/api/v1/connections/feishu/callback",
            ),
            feishu_oauth_scopes=os.getenv(
                "UNIHR_FEISHU_OAUTH_SCOPES",
                "offline_access calendar:calendar.free_busy:read",
            ),
            connector_token_encryption_key=os.getenv(
                "UNIHR_CONNECTOR_TOKEN_ENCRYPTION_KEY", ""
            ),
            public_open_timeout_seconds=float(os.getenv("UNIHR_PUBLIC_OPEN_TIMEOUT_SECONDS", "8")),
            public_open_max_pages=int(os.getenv("UNIHR_PUBLIC_OPEN_MAX_PAGES", "4")),
            public_open_max_bytes=int(os.getenv("UNIHR_PUBLIC_OPEN_MAX_BYTES", "600000")),
            public_open_max_chars=int(os.getenv("UNIHR_PUBLIC_OPEN_MAX_CHARS", "18000")),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "artifacts" / "previews").mkdir(parents=True, exist_ok=True)
