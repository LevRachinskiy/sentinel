import json
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./sentinel.db"))
    demo_mode: bool = field(default_factory=lambda: os.getenv("DEMO_MODE", "false").lower() == "true")
    tokens: dict = field(default_factory=lambda: json.loads(os.getenv("AUTH_TOKENS", "{}")))
    sandbox_url: str = field(default_factory=lambda: os.getenv("SANDBOX_URL", "http://127.0.0.1:8001"))
    sandbox_token: str = field(default_factory=lambda: os.getenv("SANDBOX_TOKEN", "local-sandbox-only"))
    provider: str = field(default_factory=lambda: os.getenv("MODEL_PROVIDER", "baseline"))
    model: str = field(default_factory=lambda: os.getenv("MODEL_NAME", ""))
    kube_tenant: str = field(default_factory=lambda: os.getenv("KUBE_TENANT", ""))
    kube_namespace: str = field(default_factory=lambda: os.getenv("KUBE_NAMESPACE", "sentinel-lab"))
    kube_deployment: str = field(default_factory=lambda: os.getenv("KUBE_DEPLOYMENT", "checkout"))
    kube_api: str = field(default_factory=lambda: os.getenv("KUBE_API_URL", "https://kubernetes.default.svc"))
    kube_token_path: str = field(
        default_factory=lambda: os.getenv(
            "KUBE_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token"
        )
    )
    kube_ca_path: str = field(
        default_factory=lambda: os.getenv(
            "KUBE_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        )
    )
    kube_template_path: str = field(default_factory=lambda: os.getenv("KUBE_STABLE_TEMPLATE", ""))
    telemetry_url: str = field(default_factory=lambda: os.getenv("TELEMETRY_URL", ""))
    telemetry_token: str = field(default_factory=lambda: os.getenv("TELEMETRY_TOKEN", ""))
    worker_enabled: bool = True

    def validate(self):
        if not self.demo_mode and not self.tokens:
            raise ValueError("Set AUTH_TOKENS, or explicitly enable DEMO_MODE for local use")
        if self.provider not in {"baseline", "openai", "anthropic"}:
            raise ValueError("Unsupported model provider")
        if self.provider != "baseline" and not self.model:
            raise ValueError("MODEL_NAME must be explicitly configured")
        if self.kube_tenant and (not self.kube_template_path or not self.telemetry_url or self.demo_mode):
            raise ValueError("Kubernetes requires authenticated mode, a stable template, and TELEMETRY_URL")
        for token, identity in self.tokens.items():
            if len(token) < 24 or set(identity) != {"tenant", "actor", "role"}:
                raise ValueError("Each AUTH_TOKENS record needs a long token, tenant, actor, and role")
            if identity["role"] not in {"viewer", "operator"}:
                raise ValueError("Role must be viewer or operator")
