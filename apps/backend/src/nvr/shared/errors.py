"""Erros de aplicação compartilhados.

Adaptadores convertem exceções de bibliotecas/protocolos nestes tipos; a camada HTTP os traduz para
respostas. Nenhuma exceção de SDK deve atravessar a camada de aplicação.
"""

from __future__ import annotations


class AppError(Exception):
    """Base dos erros de aplicação. `code` é estável e vai para a API."""

    code = "app_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(AppError):
    code = "not_found"


class ValidationFailed(AppError):
    """`fields` mapeia campo → mensagem, para a UI marcar cada campo inválido."""

    code = "validation_failed"

    def __init__(self, message: str, fields: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.fields = fields or {}


class Conflict(AppError):
    code = "conflict"


class Unauthorized(AppError):
    code = "unauthorized"


class Forbidden(AppError):
    code = "forbidden"


class UnsupportedFeature(AppError):
    code = "unsupported_feature"


class CameraOffline(AppError):
    code = "camera_offline"


class AuthenticationFailed(AppError):
    """A câmera recusou as credenciais (diferente de `Unauthorized`, que é o usuário do painel)."""

    code = "camera_auth_failed"


class OperationTimeout(AppError):
    code = "operation_timeout"


class ResourceBusy(AppError):
    code = "resource_busy"
