"""Modelo NotificationDedup - deduplicação de notificações por email.

Re-exporta NotificationDedupModel definido em entities.py para conveniência
de imports diretos (ex: from brand_watchdog.models.notification_dedup import ...).
"""

from brand_watchdog.models.entities import NotificationDedupModel

__all__ = ["NotificationDedupModel"]
