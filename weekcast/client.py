"""Open-Meteo 資料存取:地名查詢與系集預報。只用標準函式庫。"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import __version__
from .systems import (
    BY_API_ID,
    CROSS_SYSTEM_ID,
    CROSS_VARS,
    DAILY_VARS,
    HOURLY_VARS,
    NS_OFFSETS,
    PERIOD_VARS,
    PROFILE_VARS,
    SYSTEMS,
    UPPER_SYSTEM_IDS,
    WE_OFFSETS,
)

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
USER_AGENT = f"weekcast/{__version__}"
MINUTE_WAIT = 62  # 秒;Open-Meteo 的每分鐘額度
MAX_MINUTE_WAITS = 4

# 常用地名直接內建座標,不必查詢(Open-Meteo 的地名查詢對中文支援有限)。
KNOWN_PLACES: dict[str, tuple[float, float]] = {
    "基隆": (25.13, 121.74),
    "台北": (25.04, 121.56),
    "新北": (25.01, 121.46),
    "桃園": (24.99, 121.31),
    "新竹": (24.80, 120.97),
    "苗栗": (24.56, 120.82),
    "台中": (24.15, 120.67),
    "彰化": (24.08, 120.54),
    "南投": (23.91, 120.68),
    "雲林": (23.71, 120.54),
    "嘉義": (23.48, 120.45),
    "台南": (23.00, 120.21),
    "高雄": (22.63, 120.30),
    "屏東": (22.67, 120.49),
    "宜蘭": (24.75, 121.75),
    "花蓮": (23.98, 121.60),
    "台東": (22.76, 121.14),
    "澎湖": (23.57, 119.58),
    "金門": (24.43, 118.32),
    "馬祖": (26.16, 119.95),
}


class FetchError(RuntimeError):
    """資料來源無法回應(網路、額度或參數問題)。"""


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    country: str = ""


def _get_json(url: str, params: dict, timeout: float = 60, retries: int = 3) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    last = "unknown error"
    minute_waits = 0
    attempt = 0
    while attempt < retries:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            try:
                reason = json.loads(err.read().decode("utf-8")).get("reason", "")
            except (ValueError, UnicodeDecodeError):
                reason = ""
            last = f"HTTP {err.code} {reason}".strip()
            # 每分鐘額度用完:等額度恢復再試。高空剖面一次抓很多點,連續查幾個地點就會碰到。
            if err.code == 429 and "minutely" in reason.lower() and minute_waits < MAX_MINUTE_WAITS:
                minute_waits += 1
                time.sleep(MINUTE_WAIT)
                continue
            # 其他 4xx 重試沒有意義(參數錯誤或每日額度用完)。
            if 400 <= err.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            last = str(err)
        attempt += 1
        if attempt < retries:
            time.sleep(2 * 2 ** (attempt - 1))
    raise FetchError(f"{urllib.parse.urlparse(url).netloc}: {last}")


def _normalize(name: str) -> str:
    name = name.strip().replace("臺", "台")
    for tail in ("市", "縣"):
        if len(name) > 2 and name.endswith(tail):
            name = name[: -len(tail)]
    return name


def geocode(name: str) -> Place:
    """把地名轉成座標。先查內建表,再查 Open-Meteo。"""
    key = _normalize(name)
    if key in KNOWN_PLACES:
        lat, lon = KNOWN_PLACES[key]
        return Place(name.strip(), lat, lon, "台灣")
    data = _get_json(GEOCODE_URL, {"name": name.strip(), "count": 1})
    results = data.get("results") or []
    if not results:
        raise FetchError(f"找不到地點「{name}」,請改用英文地名或 --lat/--lon 指定座標")
    hit = results[0]
    return Place(hit["name"], hit["latitude"], hit["longitude"], hit.get("country", ""))


def fetch_ensemble(latitude: float, longitude: float, days: int = 7, system_ids=None) -> dict:
    """取得多套系集在單一地點的逐日與逐時預報(原始 JSON)。"""
    ids = list(system_ids) if system_ids else [s.api_id for s in SYSTEMS]
    unknown = [i for i in ids if i not in BY_API_ID]
    if unknown:
        raise FetchError(f"未知的模式代碼:{', '.join(unknown)}")
    return _get_json(
        ENSEMBLE_URL,
        {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "daily": ",".join(DAILY_VARS),
            "hourly": ",".join(HOURLY_VARS),
            "models": ",".join(ids),
            "timezone": "auto",
            "forecast_days": days,
        },
        timeout=90,
    )


def cross_points(latitude: float, longitude: float) -> list[dict]:
    """十字剖面上的各點:東西向沿測站緯度,南北向沿測站經度。"""
    points = [
        {"axis": "we", "offset": o, "latitude": latitude, "longitude": (longitude + o + 180) % 360 - 180}
        for o in WE_OFFSETS
    ]
    points += [
        {"axis": "ns", "offset": o, "latitude": latitude + o, "longitude": longitude}
        for o in NS_OFFSETS
        if abs(latitude + o) <= 85
    ]
    return points


def fetch_upper(latitude: float, longitude: float, days: int = 7, timezone: str = "GMT") -> dict:
    """取得高空分析所需的資料:測站垂直剖面與十字剖面(每 6 小時一筆)。

    timezone 要與地面資料相同,逐日彙整時日期才對得起來;十字剖面橫跨多個
    時區,所以不能用 auto。
    """
    common = {
        "timezone": timezone,
        "forecast_days": days,
        "temporal_resolution": "hourly_6",
        "wind_speed_unit": "ms",
    }
    profile = _get_json(
        ENSEMBLE_URL,
        common
        | {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "hourly": ",".join(PROFILE_VARS),
            "models": ",".join(UPPER_SYSTEM_IDS),
        },
        timeout=90,
    )
    points = cross_points(latitude, longitude)
    data = _get_json(
        ENSEMBLE_URL,
        common
        | {
            "latitude": ",".join(f"{p['latitude']:.2f}" for p in points),
            "longitude": ",".join(f"{p['longitude']:.2f}" for p in points),
            "hourly": ",".join(CROSS_VARS),
            "models": CROSS_SYSTEM_ID,
        },
        timeout=120,
    )
    if not isinstance(data, list) or len(data) != len(points):
        raise FetchError("十字剖面的回應格式不符預期")
    return {"profile": profile, "cross": {"points": points, "data": data}}


def fetch_periods(latitude: float, longitude: float, days: int = 7, timezone: str = "GMT") -> dict:
    """取得時段預報所需的資料:每 3 小時一筆的氣溫、雨量、雲量(所有成員)。

    雨量是往前累積的,最後一天晚上(18–24 時)的最後一筆標示在隔天 00:00,
    所以多抓一天。
    """
    return _get_json(
        ENSEMBLE_URL,
        {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "hourly": ",".join(PERIOD_VARS),
            "models": ",".join(s.api_id for s in SYSTEMS),
            "timezone": timezone,
            "forecast_days": days + 1,
            "temporal_resolution": "hourly_3",
        },
        timeout=90,
    )


def split_bundle(raw: dict) -> tuple[dict, dict | None]:
    """把存檔拆成 (地面資料, 高空資料)。舊格式的存檔只有地面資料。"""
    if "surface" in raw:
        return raw["surface"], raw.get("upper")
    return raw, None


def load_raw(path: str) -> dict:
    """讀取先前存下的原始回應(.json 或 .json.gz)。"""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def save_raw(raw: dict, path: str) -> None:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as handle:
        json.dump(raw, handle, separators=(",", ":"))
