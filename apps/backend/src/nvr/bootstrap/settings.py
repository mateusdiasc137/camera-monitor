"""Configuração da aplicação (variáveis de ambiente NVR_* e arquivo .env)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NVR_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("./data"), description="banco, índice, snapshots e estado")
    bind_host: str = Field(default="127.0.0.1", description="o backend fica atrás do proxy HTTPS (ADR-003)")
    bind_port: int = 8000
    cookie_secure: bool = Field(
        default=True, description="cookie de sessão só por HTTPS; desligar apenas em desenvolvimento local"
    )
    trusted_proxies: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1"],
        description="origens cujo X-Forwarded-For é aceito (o proxy HTTPS local)",
    )
    secret_backend: Literal["auto", "keyring", "file", "memory"] = Field(
        default="auto",
        description="cofre das credenciais de câmeras: auto = keyring se houver, senão arquivo cifrado",
    )
    secret_key_file: Path | None = Field(
        default=None,
        description="chave do cofre em arquivo (fora do data_dir); padrão: pasta de configuração do usuário",
    )
    storage_roots: list[Path] = Field(
        default_factory=lambda: [Path("./data/recordings")],
        description="raízes nas quais a UI pode escolher o local de gravação",
    )

    def resolved_secret_key_file(self) -> Path:
        if self.secret_key_file is not None:
            return self.secret_key_file
        base = (
            Path(os.environ["APPDATA"])
            if os.name == "nt" and "APPDATA" in os.environ
            else Path.home() / ".config"
        )
        return base / "nvr" / "secret.key"
