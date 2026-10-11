"""Validated, non-secret configuration; secrets are never serialized here."""
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / ".jarvis")
    workspace: Path = Field(default_factory=lambda: Path.cwd() / ".jarvis" / "workspace")
    vault_roots: list[Path] = Field(default_factory=list)
    model_url: str = "http://127.0.0.1:11434/v1"
    model_name: str = "qwen3:8b"
    allow_cloud: bool = False
    model_key_env: str = "JARVIS_MODEL_API_KEY"
    max_tokens: int = Field(default=512, ge=16, le=8192)
    request_timeout: float = Field(default=60, ge=1, le=180)
    max_file_bytes: int = Field(default=2_000_000, ge=1024, le=20_000_000)
    allowed_tools: list[str] = Field(default_factory=lambda: [
        "read_file", "write_file", "list_files", "calculate", "vault_search", "vision",
        "business_report", "create_website", "edit_video", "focus",
    ])
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"])
    enable_browser: bool = False
    enable_vision: bool = False
    sandbox_image: str = ""
    telegram_chat_id: str = ""
    telegram_user_id: str = ""
    phone_number: str = ""
    twilio_from_number: str = ""
    whatsapp_phone_id: str = ""
    social_forbidden_terms: list[str] = Field(default_factory=list)
    auto_index_seconds: int = Field(default=60, ge=0, le=3600)
    daily_call_limit: int = Field(default=2, ge=0, le=10)
    daily_post_limit: int = Field(default=3, ge=0, le=50)
    daily_message_limit: int = Field(default=20, ge=0, le=100)
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    timezone: str = "Asia/Dubai"

    @model_validator(mode="after")
    def validate_paths_and_network(self):
        self.data_dir = self.data_dir.expanduser().absolute()
        self.workspace = self.workspace.expanduser().absolute()
        self.vault_roots = [root.expanduser().absolute() for root in self.vault_roots]
        parsed = urlparse(self.model_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("model_url must be an HTTP(S) endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("model_url must not contain credentials, query, or fragment")
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not local and (not self.allow_cloud or parsed.scheme != "https"):
            raise ValueError("remote inference requires allow_cloud=true and HTTPS")
        if any("/" in domain or ":" in domain or "*" in domain for domain in self.allowed_domains):
            raise ValueError("allowed_domains must contain exact hostnames")
        if not self.allowed_hosts or any("*" in host or "/" in host for host in self.allowed_hosts):
            raise ValueError("allowed_hosts requires exact hostnames")
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone name") from exc
        return self

    @classmethod
    def load(cls, path: Path | None = None):
        if path is None:
            return cls()
        with Path(path).open("rb") as handle:
            values = tomllib.load(handle)
        base = Path(path).absolute().parent
        for name in ("data_dir", "workspace"):
            if name in values and not Path(values[name]).is_absolute():
                values[name] = base / values[name]
        values["vault_roots"] = [base / p if not Path(p).is_absolute() else Path(p)
                                  for p in values.get("vault_roots", [])]
        return cls(**values)

    def prepare(self):
        if self.data_dir.is_symlink() or self.workspace.is_symlink():
            raise ValueError("state/workspace directories cannot be symlinks")
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
