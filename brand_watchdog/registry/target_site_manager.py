"""Gerenciador de sites-alvo para monitoramento.

Responsável por registrar, remover e listar Target Sites,
com validação de URL, normalização, controle de duplicatas
e limite máximo por conta.
"""

from __future__ import annotations

from sqlalchemy import func, select

from brand_watchdog.models.database import get_session
from brand_watchdog.models.dataclasses import TargetSite, ValidationResult
from brand_watchdog.models.entities import TargetSiteModel
from brand_watchdog.utils.validators import URLValidator


class TargetSiteManager:
    """Gerencia o cadastro de sites-alvo de monitoramento.

    Oferece operações de registro, remoção e listagem de
    Target Sites, aplicando validação de URL, normalização
    para deduplicação e limite máximo configurável.

    Args:
        max_target_sites: Limite máximo de sites por conta.
    """

    def __init__(self, max_target_sites: int = 200) -> None:
        self._max_target_sites = max_target_sites
        self._url_validator = URLValidator()

    def validate_url(self, url: str) -> ValidationResult:
        """Valida URL do site-alvo conforme regras de negócio.

        Args:
            url: URL a ser validada.

        Returns:
            ValidationResult indicando se a URL é válida.
        """
        return self._url_validator.validate(url)

    def normalize_url(self, url: str) -> str:
        """Normaliza URL: lowercase scheme/host, remove trailing slash.

        Args:
            url: URL a ser normalizada.

        Returns:
            URL normalizada como string.
        """
        return self._url_validator.normalize(url)

    async def register(self, url: str) -> TargetSite:
        """Registra novo site-alvo após validação.

        Fluxo: validate → normalize → check duplicate → check limit → persist.

        Args:
            url: URL do site-alvo a registrar.

        Returns:
            TargetSite dataclass com dados do site registrado.

        Raises:
            ValueError: Se a URL for inválida, duplicada ou o limite
                        máximo de sites foi atingido.
        """
        # 1. Validar URL
        validation = self.validate_url(url)
        if not validation.valid:
            raise ValueError(
                f"URL inválida: {validation.error}"
            )

        # 2. Normalizar URL
        normalized = self.normalize_url(url)

        # 3. Verificar duplicata e limite dentro da mesma sessão
        async with get_session() as session:
            # Checar duplicata pela normalized_url
            stmt_dup = select(TargetSiteModel).where(
                TargetSiteModel.normalized_url == normalized
            )
            result_dup = await session.execute(stmt_dup)
            existing = result_dup.scalar_one_or_none()

            if existing is not None:
                raise ValueError(
                    "URL já existe na lista de monitoramento"
                )

            # Checar limite máximo
            stmt_count = select(func.count()).select_from(
                TargetSiteModel
            )
            result_count = await session.execute(stmt_count)
            count = result_count.scalar_one()

            if count >= self._max_target_sites:
                raise ValueError(
                    f"Limite máximo de {self._max_target_sites} "
                    "sites-alvo atingido"
                )

            # 4. Persistir novo registro
            model = TargetSiteModel(
                url=url,
                normalized_url=normalized,
            )
            session.add(model)
            await session.flush()

            # Converter para dataclass de retorno
            return TargetSite(
                id=model.id,
                url=model.url,
                normalized_url=model.normalized_url,
                created_at=model.created_at,
                active=model.active,
            )

    async def remove(self, site_id: str) -> bool:
        """Remove site-alvo da lista de monitoramento.

        Args:
            site_id: ID do site-alvo a remover.

        Returns:
            True se o site foi encontrado e removido, False caso contrário.
        """
        async with get_session() as session:
            stmt = select(TargetSiteModel).where(
                TargetSiteModel.id == site_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()

            if model is None:
                return False

            await session.delete(model)
            return True

    async def list_all(self) -> list[TargetSite]:
        """Lista todos os sites-alvo registrados e ativos.

        Returns:
            Lista de TargetSite dataclasses ordenados por data de criação.
        """
        async with get_session() as session:
            stmt = (
                select(TargetSiteModel)
                .where(TargetSiteModel.active.is_(True))
                .order_by(TargetSiteModel.created_at)
            )
            result = await session.execute(stmt)
            models = result.scalars().all()

            return [
                TargetSite(
                    id=m.id,
                    url=m.url,
                    normalized_url=m.normalized_url,
                    created_at=m.created_at,
                    active=m.active,
                )
                for m in models
            ]
