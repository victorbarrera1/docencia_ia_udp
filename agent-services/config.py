import os
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

def _load_env_file(filepath: Path):
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k not in os.environ:
                            os.environ[k] = v.strip('"\'')
        except Exception:
            pass

# Cargar automáticamente .env local del microservicio y .env.local del proyecto raíz
_base_dir = Path(__file__).resolve().parent
_load_env_file(_base_dir / ".env")
_load_env_file(_base_dir.parent / ".env.local")
_load_env_file(_base_dir.parent / ".env")

# Intento de carga con pydantic-settings, con fallback a BaseModel y os.getenv
try:
    from pydantic_settings import BaseSettings
    _BaseClass = BaseSettings
except ImportError:
    _BaseClass = BaseModel

class AgentSettings(_BaseClass):
    """
    Configuración central y securizada para el microservicio de agentes UDP.
    """
    app_name: str = Field(default_factory=lambda: os.getenv("APP_NAME", "Docencia IA UDP - Agent Services"))
    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    host: str = Field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    allowed_origins: str = Field(default_factory=lambda: os.getenv("ALLOWED_ORIGINS", "http://localhost:3000"))
    agent_service_api_key: str = Field(default_factory=lambda: os.getenv("AGENT_SERVICE_API_KEY", ""))
    
    # Proveedores de LLM: OpenRouter (Puerta unificada 100% Gratuita con openrouter/free)
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_base_url: str = Field(default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    default_openrouter_model: str = Field(default_factory=lambda: os.getenv("DEFAULT_OPENROUTER_MODEL", "openrouter/free"))
    activity_designer_model: str = Field(default_factory=lambda: os.getenv("ACTIVITY_DESIGNER_MODEL", "openrouter/free"))
    evaluator_model: str = Field(default_factory=lambda: os.getenv("EVALUATOR_MODEL", "openrouter/free"))
    # IA-04: contrato de rag_chunks.embedding; cambiarlo exige migración y reindexación.
    embedding_model: str = Field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small"))
    embedding_dim: int = Field(default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "1536")), gt=0)
    embedding_batch_size: int = Field(default_factory=lambda: int(os.getenv("EMBEDDING_BATCH_SIZE", "32")), gt=0, le=256)
    # IA-06: indexar en segundo plano cada versión recién subida; si falla, el backfill la recoge.
    rag_auto_index: bool = Field(default_factory=lambda: os.getenv("RAG_AUTO_INDEX", "1").strip().lower() not in ("0", "false", "no"))
    openrouter_site_url: str = Field(default_factory=lambda: os.getenv("OPENROUTER_SITE_URL", "https://udp.cl"))
    openrouter_site_name: str = Field(default_factory=lambda: os.getenv("OPENROUTER_SITE_NAME", "Docencia IA UDP"))

    # Cliente LLM robusto (IA-03)
    llm_timeout_s: float = Field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_S", "30")))
    llm_max_retries: int = Field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))
    llm_backoff_base_s: float = Field(default_factory=lambda: float(os.getenv("LLM_BACKOFF_BASE_S", "0.5")))
    llm_fake: bool = Field(default_factory=lambda: os.getenv("LLM_FAKE", "").strip().lower() in ("1", "true", "yes"))
    @field_validator("llm_fake", mode="before")
    @classmethod
    def _llm_fake_from_text(cls, value):
        """LLM_FAKE vacío (p. ej. `LLM_FAKE=` en .env) es false en vez de fallar con bool_parsing."""
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes")
        return value

    # Prefijos de modelo que aceptan response_format json_schema estricto (separados por coma).
    llm_json_schema_models: str = Field(default_factory=lambda: os.getenv("LLM_JSON_SCHEMA_MODELS", "openai/,google/"))

    # Modelo del chat jerárquico (core/engine.py), vía OpenRouter; mismo modelo que se usaba directo con Gemini.
    chat_model: str = Field(default_factory=lambda: os.getenv("CHAT_MODEL", "google/gemini-2.5-flash"))

    # Registro auditable del uso de IA (IA-07): listener que escribe en ai_audit_log.
    audit_enabled: bool = Field(default_factory=lambda: os.getenv("AI_AUDIT_ENABLED", "1").strip().lower() not in ("0", "false", "no"))
    audit_timeout_s: float = Field(default_factory=lambda: float(os.getenv("AI_AUDIT_TIMEOUT_S", "3")))

    # DEPRECADOS: desde IA-07 el chat pasa por OpenRouter (CHAT_MODEL) y nada los lee.
    # Se retiran, junto con google-genai, cuando se coordine con el frontend.
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    default_frontier_model: str = Field(default_factory=lambda: os.getenv("DEFAULT_FRONTIER_MODEL", "gemini-2.5-flash"))
    default_worker_model: str = Field(default_factory=lambda: os.getenv("DEFAULT_WORKER_MODEL", "gemini-2.5-flash"))
    
    # Presupuestos y límites agénticos
    max_steps_per_turn: int = Field(default_factory=lambda: int(os.getenv("MAX_STEPS_PER_TURN", "5")))
    max_tokens_budget: int = Field(default_factory=lambda: int(os.getenv("MAX_TOKENS_BUDGET", "4000")))
    
    # Integración Supabase
    supabase_url: str = Field(default_factory=lambda: os.getenv("NEXT_PUBLIC_SUPABASE_URL", ""))
    supabase_service_role_key: str = Field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))

    # Ingesta de PDF (IA-05). El bucket y el límite de 20 MiB también están en la migración.
    submission_bucket: str = Field(default_factory=lambda: os.getenv("SUBMISSION_BUCKET", "entregas"))
    # Límites en MiB y miles de caracteres: el escáner de PII confunde números de 7-8 dígitos con RUT.
    max_pdf_bytes: int = Field(default_factory=lambda: int(os.getenv("MAX_PDF_MB", "20")) * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default_factory=lambda: int(os.getenv("MAX_PDF_PAGES", "300")), gt=0)
    max_pdf_text_chars: int = Field(default_factory=lambda: int(os.getenv("MAX_PDF_TEXT_KCHARS", "1500")) * 1000, gt=0)
    # ~800 tokens con solape de ~100, a 4 caracteres por token (sin tokenizador en el servicio).
    chunk_chars: int = Field(default_factory=lambda: int(os.getenv("CHUNK_CHARS", "3200")), gt=0)
    chunk_overlap_chars: int = Field(default_factory=lambda: int(os.getenv("CHUNK_OVERLAP_CHARS", "400")), ge=0)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = AgentSettings()
