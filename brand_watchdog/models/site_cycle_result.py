"""Modelo SiteCycleResult - resultado de processamento por site no ciclo.

Re-exporta SiteCycleResultModel definido em entities.py para conveniência
de imports diretos (ex: from brand_watchdog.models.site_cycle_result import ...).
"""

from brand_watchdog.models.entities import SiteCycleResultModel

__all__ = ["SiteCycleResultModel"]
