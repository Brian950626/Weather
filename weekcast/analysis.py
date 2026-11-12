"""把系集原始資料轉成逐日預報、信心度與天氣型態訊號。

設計依據見 docs/tropical-tidbits-notes.md,重點是三件事:

1. 不看單一模式的單一次預報,而是把多套系集的所有成員放在一起看分布。
2. 成員之間的離散度(spread)與模式之間的分歧,就是預報的不確定性。
3. 地面天氣(溫度、雨)要搭配高空與氣壓場(500 hPa 高度、850 hPa 溫度、
   海平面氣壓)的變化一起解讀,才知道「為什麼」。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from .systems import BY_SUFFIX, DAILY_VARS, HOURLY_VARS

_MEMBER = re.compile(r"_member(\d+)$")
_SINGLE = "系集"

RAIN_MM = 1.0  # 算作「有雨」的日雨量門檻
MODERATE_MM = 10.0
HEAVY_MM = 50.0


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


@dataclass
class Ensemble:
    """整理後的系集資料:system → 變數 → 成員 → 時間序列。"""

    meta: dict
    days: list[str]
    hours: list[str]
    daily: dict[str, dict[str, list[list]]] = field(default_factory=dict)
    hourly: dict[str, dict[str, list[list]]] = field(default_factory=dict)

    @property
    def systems(self) -> list[str]:
        return list(self.daily)

    def member_count(self, system: str) -> int:
        return max((len(m) for m in self.daily.get(system, {}).values()), default=0)


def _split_key(key: str, known_vars) -> tuple[str, str] | None:
    """把 'temperature_2m_max_member03_ncep_gefs025' 拆成 (系統, 變數)。"""
    system = _SINGLE
    for suffix, info in BY_SUFFIX.items():
        if key.endswith("_" + suffix):
            key = key[: -len(suffix) - 1]
            system = info.name
            break
    var = _MEMBER.sub("", key)
    return (system, var) if var in known_vars else None


def _section(raw: dict, name: str, known_vars) -> dict[str, dict[str, list[list]]]:
    out: dict[str, dict[str, list[list]]] = {}
    for key, series in (raw.get(name) or {}).items():
        if key == "time":
            continue
        parsed = _split_key(key, known_vars)
        if parsed is None or all(v is None for v in series):
            continue  # 該模式不提供這個變數
        system, var = parsed
        out.setdefault(system, {}).setdefault(var, []).append(series)
    return out


def parse(raw: dict) -> Ensemble:
    """解析 Open-Meteo Ensemble API 的回應。"""
    if raw.get("error"):
        raise ValueError(f"資料來源回報錯誤:{raw.get('reason', '未知原因')}")
    if "daily" not in raw or "time" not in raw["daily"]:
        raise ValueError("回應中沒有逐日資料")
    meta = {k: raw.get(k) for k in ("latitude", "longitude", "elevation", "timezone", "timezone_abbreviation")}
    ens = Ensemble(
        meta=meta,
        days=list(raw["daily"]["time"]),
        hours=list((raw.get("hourly") or {}).get("time", [])),
        daily=_section(raw, "daily", DAILY_VARS),
        hourly=_section(raw, "hourly", HOURLY_VARS),
    )
    if not ens.daily:
        raise ValueError("回應中沒有任何可用的系集成員")
    return ens


# --------------------------------------------------------------------------- #
# 加權統計
# --------------------------------------------------------------------------- #

Pairs = list[tuple[float, float]]  # (數值, 權重)


def wquantile(pairs: Pairs, q: float) -> float:
    """加權分位數(以各樣本權重中點做線性內插)。"""
    if not pairs:
        return math.nan
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    position = 0.0
    points = []
    for value, weight in ordered:
        points.append(((position + weight / 2) / total, value))
        position += weight
    if q <= points[0][0]:
        return points[0][1]
    for (c0, v0), (c1, v1) in zip(points, points[1:]):
        if q <= c1:
            return v0 + (v1 - v0) * (q - c0) / (c1 - c0) if c1 > c0 else v1
    return points[-1][1]


def wfraction(pairs: Pairs, predicate) -> float:
    """符合條件的成員所佔的權重比例。"""
    total = sum(w for _, w in pairs)
    return sum(w for v, w in pairs if predicate(v)) / total if total else math.nan


def pool(ens: Ensemble, section: str, var: str, index: int, offsets: dict[str, float] | None = None):
    """把各系集在某個時間點的成員合併成一個大系集。

    每套系集的總權重相同,避免成員多的系統(ECMWF 51 個)壓過成員少的
    (GEFS 31 個)——模式之間的差異往往比同一模式內成員的差異更有資訊量。
    回傳 (加權樣本, 各系統的成員值);給了 offsets 時會先扣掉各系統的偏差。
    """
    by_system: dict[str, list[float]] = {}
    for system, variables in getattr(ens, section).items():
        shift = (offsets or {}).get(system, 0.0)
        values = [m[index] - shift for m in variables.get(var, []) if index < len(m) and m[index] is not None]
        if values:
            by_system[system] = values
    pairs = [(v, 1 / len(values) / len(by_system)) for values in by_system.values() for v in values]
    return pairs, by_system


def median(values: list[float]) -> float:
    return wquantile([(v, 1.0) for v in values], 0.5)


MIN_DAYS_FOR_OFFSETS = 4


def system_offsets(ens: Ensemble, var: str) -> dict[str, float]:
    """估計各系集相對於多模式共識的系統性偏差。

    各模式的格點位置、地形與海陸分布不同,同一個地點可能整週都固定偏高或
    偏低幾度(例如格點落在海上的模式白天永遠比較涼)。這種固定偏差不是天氣
    的不確定性,不扣掉的話會讓每一天看起來都「模式分歧、信心低」。

    作法:每天算「該系統中位數 − 各系統中位數的中位數」,再取整段預報期間
    的中位數。整週都存在的差距會被當成偏差扣掉;只出現幾天的差距(真正的
    預報分歧)則會保留下來。天數太少時無法區分兩者,就不校正。
    """
    gaps: dict[str, list[float]] = {}
    for i in range(len(ens.days)):
        _, by_system = pool(ens, "daily", var, i)
        if len(by_system) < 2:
            continue
        medians = {system: median(values) for system, values in by_system.items()}
        consensus = median(list(medians.values()))
        for system, value in medians.items():
            gaps.setdefault(system, []).append(value - consensus)
    return {system: median(g) for system, g in gaps.items() if len(g) >= MIN_DAYS_FOR_OFFSETS}


# --------------------------------------------------------------------------- #
# 逐日預報
# --------------------------------------------------------------------------- #


@dataclass
class Spread:
    p10: float
    p50: float
    p90: float

    @property
    def width(self) -> float:
        return self.p90 - self.p10

    @classmethod
    def of(cls, pairs: Pairs) -> "Spread | None":
        if not pairs:
            return None
        return cls(wquantile(pairs, 0.1), wquantile(pairs, 0.5), wquantile(pairs, 0.9))


@dataclass
class Scenario:
    """成員分成兩群時的兩種劇本(例如鋒面過/不過)。"""

    low: float
    low_share: float
    high: float
    high_share: float


@dataclass
class DayForecast:
    day: str
    lead: int  # 距離預報起始日的天數
    tmax: Spread
    tmin: Spread
    precip: Spread
    pop: float  # 日雨量 ≥ 1 mm 的機率
    pop_moderate: float  # ≥ 10 mm
    pop_heavy: float  # ≥ 50 mm
    wind: Spread | None
    gust: Spread | None
    cloud: Spread | None
    system_tmax: dict[str, float]  # 各系集的高溫中位數
    system_pop: dict[str, float]  # 各系集的降雨機率
    members: int
    confidence: int = 0
    confidence_label: str = ""
    sky: str = ""
    scenario: Scenario | None = None

    @property
    def weekday(self) -> str:
        return "一二三四五六日"[date.fromisoformat(self.day).weekday()]

    @property
    def tmax_gap(self) -> float:
        values = list(self.system_tmax.values())
        return max(values) - min(values) if len(values) > 1 else 0.0

    @property
    def pop_gap(self) -> float:
        values = list(self.system_pop.values())
        return max(values) - min(values) if len(values) > 1 else 0.0


def score_confidence(lead: int, t_spread: float, t_gap: float, pop: float, pop_gap: float) -> int:
    """0–100 的信心分數。扣分項都是經驗法則,不是機率。

    - 成員離散度:高溫 10–90% 區間超過 2°C 開始扣分。
    - 模式分歧:各系集高溫中位數相差超過 1°C、降雨機率差距大時扣分。
    - 下不下雨不明確:降雨機率越接近 50% 扣越多。
    - 預報時效:系集普遍離散度不足(過度自信),所以越遠再多扣一點。
    """
    score = 100.0
    score -= 2 * lead
    score -= min(35, max(0.0, t_spread - 2.0) * 7)
    score -= min(20, max(0.0, t_gap - 1.0) * 8)
    score -= 25 * (1 - abs(2 * pop - 1))
    score -= min(15, pop_gap * 25)
    return max(0, round(score))


def confidence_label(score: int) -> str:
    return "高" if score >= 70 else "中" if score >= 45 else "低"


def describe_sky(pop: float, precip: Spread, cloud: Spread | None) -> str:
    if pop >= 0.7:
        if precip.p50 >= HEAVY_MM:
            return "大雨"
        if precip.p50 >= MODERATE_MM:
            return "明顯降雨"
        return "有雨"
    if pop >= 0.4:
        return "可能有雨"
    cover = cloud.p50 if cloud else 60.0
    if pop >= 0.2:
        return "多雲偶雨" if cover >= 50 else "晴偶陣雨"
    if cover >= 85:
        return "陰"
    if cover >= 55:
        return "多雲"
    if cover >= 25:
        return "晴時多雲"
    return "晴"


def find_scenario(pairs: Pairs, min_gap: float = 3.0, min_share: float = 0.2) -> Scenario | None:
    """偵測成員是否明顯分成兩群。

    先用一維 2-means 分群,再要求三件事同時成立:兩群中心相距夠遠、
    Ashman's D > 2,而且兩群中心之間的中段幾乎沒有成員。最後一項是關鍵:
    只是離散度大的單峰或均勻分布,切一半也會得到兩個「分得很開」的群,
    但中段會有很多成員;真正的兩種劇本中間是空的。
    """
    if len(pairs) < 10:
        return None
    lo, hi = wquantile(pairs, 0.15), wquantile(pairs, 0.85)
    if hi - lo < min_gap:
        return None
    for _ in range(25):
        cut = (lo + hi) / 2
        groups = [[p for p in pairs if p[0] <= cut], [p for p in pairs if p[0] > cut]]
        if not groups[0] or not groups[1]:
            return None
        means = [sum(v * w for v, w in g) / sum(w for _, w in g) for g in groups]
        if abs(means[0] - lo) < 1e-6 and abs(means[1] - hi) < 1e-6:
            break
        lo, hi = means
    shares = [sum(w for _, w in g) for g in groups]
    total = sum(shares)
    variances = [sum(w * (v - m) ** 2 for v, w in g) / s for g, m, s in zip(groups, means, shares)]
    spread = math.sqrt(variances[0] + variances[1])
    separation = math.sqrt(2) * (hi - lo) / spread if spread > 0 else math.inf
    third = (hi - lo) / 3
    middle = wfraction(pairs, lambda v: lo + third < v < hi - third)
    if hi - lo < min_gap or separation <= 2 or min(shares) / total < min_share or middle > 0.1:
        return None
    return Scenario(lo, shares[0] / total, hi, shares[1] / total)


def daily_forecast(ens: Ensemble, days: int | None = None) -> list[DayForecast]:
    out = []
    tmax_offsets = system_offsets(ens, "temperature_2m_max")
    tmin_offsets = system_offsets(ens, "temperature_2m_min")
    for i, day in enumerate(ens.days[: days or len(ens.days)]):
        tmax_pairs, tmax_sys = pool(ens, "daily", "temperature_2m_max", i, tmax_offsets)
        tmin_pairs, _ = pool(ens, "daily", "temperature_2m_min", i, tmin_offsets)
        rain_pairs, rain_sys = pool(ens, "daily", "precipitation_sum", i)
        if not tmax_pairs or not tmin_pairs or not rain_pairs:
            continue  # 超出所有模式的預報範圍
        pop = wfraction(rain_pairs, lambda v: v >= RAIN_MM)
        fc = DayForecast(
            day=day,
            lead=i,
            tmax=Spread.of(tmax_pairs),
            tmin=Spread.of(tmin_pairs),
            precip=Spread.of(rain_pairs),
            pop=pop,
            pop_moderate=wfraction(rain_pairs, lambda v: v >= MODERATE_MM),
            pop_heavy=wfraction(rain_pairs, lambda v: v >= HEAVY_MM),
            wind=Spread.of(pool(ens, "daily", "wind_speed_10m_max", i)[0]),
            gust=Spread.of(pool(ens, "daily", "wind_gusts_10m_max", i)[0]),
            cloud=Spread.of(pool(ens, "daily", "cloud_cover_mean", i)[0]),
            system_tmax={s: median(v) for s, v in tmax_sys.items()},
            system_pop={s: sum(x >= RAIN_MM for x in v) / len(v) for s, v in rain_sys.items()},
            members=sum(len(v) for v in tmax_sys.values()),
        )
        fc.confidence = score_confidence(fc.lead, fc.tmax.width, fc.tmax_gap, fc.pop, fc.pop_gap)
        fc.confidence_label = confidence_label(fc.confidence)
        fc.sky = describe_sky(fc.pop, fc.precip, fc.cloud)
        fc.scenario = find_scenario(tmax_pairs)
        out.append(fc)
    return out


# --------------------------------------------------------------------------- #
# 天氣型態訊號(高空與氣壓場)
# --------------------------------------------------------------------------- #


@dataclass
class Signal:
    day: str
    kind: str  # pressure_fall / pressure_rise / deep_low / cold / warm / ridge / trough
    text: str
    value: float


@dataclass
class DayPattern:
    day: str
    mslp: float | None = None  # 日平均海平面氣壓(系集中位數)
    t850: float | None = None
    z500: float | None = None
    deep_low: float = 0.0  # 出現深低壓的成員比例


def _daily_member_stat(ens: Ensemble, var: str, reducer) -> dict[str, tuple[Pairs, dict]]:
    """把逐時資料依當地日期彙整成每個成員的一個數值,再合併成大系集。"""
    slices: dict[str, list[int]] = {}
    for i, stamp in enumerate(ens.hours):
        slices.setdefault(stamp[:10], []).append(i)
    out = {}
    for day, idx in slices.items():
        by_system: dict[str, list[float]] = {}
        for system, variables in ens.hourly.items():
            values = []
            for member in variables.get(var, []):
                hours = [member[i] for i in idx if i < len(member) and member[i] is not None]
                if len(hours) >= max(1, len(idx) // 2):  # 至少要有半天的資料
                    values.append(reducer(hours))
            if values:
                by_system[system] = values
        pairs = [(v, 1 / len(vals) / len(by_system)) for vals in by_system.values() for v in vals]
        out[day] = (pairs, by_system)
    return out


def deep_low_threshold(latitude: float) -> float:
    """多低算「深低壓」:熱帶與副熱帶氣壓平常變化小,門檻訂得較高。"""
    return 1000.0 if abs(latitude) < 30 else 985.0


def change_thresholds(latitude: float) -> tuple[float, float, float]:
    """(海平面氣壓 hPa, 850 hPa 溫度 °C, 500 hPa 高度 m) 的逐日變化門檻。

    低緯度的氣壓與高空場日變化遠小於中高緯度,同樣 3 hPa 的變化在台灣已經
    是鋒面或冷高壓等級,在歐洲只是日常起伏,所以門檻隨緯度調整。
    """
    return (3.5, 2.0, 25.0) if abs(latitude) < 30 else (6.0, 4.0, 60.0)


def patterns(ens: Ensemble) -> list[DayPattern]:
    if not ens.hours:
        return []
    mean = lambda xs: sum(xs) / len(xs)
    mslp = _daily_member_stat(ens, "pressure_msl", mean)
    mslp_min = _daily_member_stat(ens, "pressure_msl", min)
    t850 = _daily_member_stat(ens, "temperature_850hPa", mean)
    z500 = _daily_member_stat(ens, "geopotential_height_500hPa", mean)
    threshold = deep_low_threshold(ens.meta.get("latitude") or 0.0)
    out = []
    for day in ens.days:
        mid = lambda table: wquantile(table[day][0], 0.5) if day in table and table[day][0] else None
        lows = mslp_min.get(day, ([], {}))[0]
        out.append(
            DayPattern(
                day=day,
                mslp=mid(mslp),
                t850=mid(t850),
                z500=mid(z500),
                deep_low=wfraction(lows, lambda v: v < threshold) if lows else 0.0,
            )
        )
    return out


def signals(ens: Ensemble, days: int | None = None) -> list[Signal]:
    """從氣壓與高空場的逐日變化找出值得注意的天氣型態轉變。"""
    table = patterns(ens)[: days or None]
    latitude = ens.meta.get("latitude") or 0.0
    tropical = abs(latitude) < 30
    threshold = deep_low_threshold(latitude)
    d_mslp, d_t850, d_z500 = change_thresholds(latitude)
    out: list[Signal] = []
    for prev, cur in zip(table, table[1:]):
        if prev.mslp is not None and cur.mslp is not None:
            change = cur.mslp - prev.mslp
            if change <= -d_mslp:
                out.append(Signal(cur.day, "pressure_fall", f"氣壓一天下降 {abs(change):.0f} hPa,低壓或鋒面接近", change))
            elif change >= d_mslp:
                out.append(Signal(cur.day, "pressure_rise", f"氣壓一天上升 {change:.0f} hPa,高壓增強", change))
        if prev.t850 is not None and cur.t850 is not None:
            change = cur.t850 - prev.t850
            if change <= -d_t850:
                out.append(Signal(cur.day, "cold", f"850 hPa 降溫 {abs(change):.0f}°C,冷空氣移入", change))
            elif change >= d_t850:
                out.append(Signal(cur.day, "warm", f"850 hPa 升溫 {change:.0f}°C,暖空氣移入", change))
        if prev.z500 is not None and cur.z500 is not None:
            change = cur.z500 - prev.z500
            if change <= -d_z500:
                out.append(Signal(cur.day, "trough", f"500 hPa 高度下降 {abs(change):.0f} m,高空槽接近,天氣轉不穩定", change))
            elif change >= d_z500:
                out.append(Signal(cur.day, "ridge", f"500 hPa 高度上升 {change:.0f} m,高空脊建立,天氣趨於穩定", change))
    for row in table:
        if row.deep_low >= 0.1:
            what = "留意熱帶系統或颱風" if tropical else "留意強低壓"
            out.append(
                Signal(row.day, "deep_low", f"{row.deep_low:.0%} 的成員氣壓低於 {threshold:.0f} hPa,{what}", row.deep_low)
            )
    return sorted(out, key=lambda s: s.day)
