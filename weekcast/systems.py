"""本工具使用的全球系集預報系統。

這些就是 Tropical Tidbits「Forecast Models → Ensemble」分類下的那幾套系集
(EPS、GEFS、GEPS、AIFS-ENS),再加上 ICON 的系集版本。資料經由 Open-Meteo
Ensemble API 取得數值,而不是抓 Tropical Tidbits 的圖。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnsembleSystem:
    api_id: str  # Open-Meteo `models=` 參數值
    suffix: str  # 多模式回應中變數名稱的後綴
    name: str  # 報表上顯示的名稱
    center: str  # 作業中心
    tt_name: str  # Tropical Tidbits 上對應的名稱


SYSTEMS: tuple[EnsembleSystem, ...] = (
    EnsembleSystem("ecmwf_ifs025", "ecmwf_ifs025_ensemble", "ECMWF", "歐洲中期天氣預報中心", "EPS"),
    EnsembleSystem("gfs025", "ncep_gefs025", "GEFS", "美國 NCEP", "GEFS"),
    EnsembleSystem("gem_global", "gem_global_ensemble", "GEPS", "加拿大氣象局", "GEPS"),
    EnsembleSystem("icon_global", "icon_global_eps", "ICON", "德國氣象局", "ICON"),
    EnsembleSystem("ecmwf_aifs025", "ecmwf_aifs025_ensemble", "AIFS", "ECMWF(AI 模式)", "AIFS-ENS"),
)

BY_API_ID = {s.api_id: s for s in SYSTEMS}
BY_SUFFIX = {s.suffix: s for s in SYSTEMS}

DAILY_VARS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "cloud_cover_mean",
)
HOURLY_VARS = (
    "pressure_msl",
    "temperature_850hPa",
    "geopotential_height_500hPa",
)
