"""高空分析:從 250 / 500 / 700 / 850 / 925 hPa 判斷槽脊、噴流與它們對地面天氣的影響。

地面的雨和溫度是結果,原因在上面。這個模組用「成分法」把原因拆開來看:

- 舉升:500 hPa 槽前、250 hPa 高層輻散、850 hPa 暖平流、925 hPa 低層鋒面(風向轉變)
- 水氣:850 hPa(低層)與 700 hPa(中層)的相對濕度,加上整層的可降水量(PWAT)
- 不穩定:K 指數(850、700、500 hPa 的溫度與露點)與 CAPE

三者俱備才會有系統性降雨;只有低層水氣而中層乾,雨就只來自淺層的雲。

沒有使用模式的垂直速度:資料來源沒有說明正負號的定義,實測(與降雨的相關)
也無法確認,而且單點的瞬時值雜訊很大,所以改用可以自己算、正負號明確的
高層輻散與溫度平流來代表上升或下沉。

資料有兩份(都取系集成員的中位數):

- 垂直剖面:測站上空各層,ECMWF 與 AIFS 兩套系集。
- 十字剖面:500 hPa 高度沿測站緯度往東西各 40°(找槽線與脊線),250 hPa 風
  沿測站經度往南北各 20°(找噴流軸),ECMWF 系集。相鄰點同時用來算水平梯度。

逐日彙整時要小心「先平均再計算」:鋒面通過的那一天,低層風會從西風轉成東北風,
把一天四筆的 u、v 直接平均,兩個方向互相抵銷,風速會被低估、風向轉變也看不到。
所以風速取各時刻風速的平均,溫度平流逐時刻計算後再平均,風向轉變則直接在
6 小時一筆的序列上找。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .analysis import _section, wquantile
from .systems import CROSS_VARS, PROFILE_VARS

EARTH_RADIUS = 6_371_000.0
STENCIL = 5  # 計算水平梯度時使用的相鄰點距離(度)
JET_SPEED = 30.0  # m/s,250 hPa 風速達此值視為噴流
SUBTROPICAL_HIGH = 5880.0  # m,500 hPa 副熱帶高壓的慣用特徵線
NEAR_AXIS = 5.0  # 槽線或脊線在這個經度範圍內,視為正在測站上空
LIFT_RANGE = 15.0  # 槽線在西方這個範圍內,槽前的舉升才算影響得到測站
K_UNSTABLE = 30.0  # K 指數達此值視為不穩定
CAPE_UNSTABLE = 1000.0  # J/kg,CAPE 達此值視為不穩定
PWAT_RICH = 50.0  # mm,可降水量達此值代表整層水氣充沛
SHIFT_TURN = 80.0  # 度,12 小時內低層風向轉這麼多才算風向轉變
SHIFT_SPEED = 3.0  # m/s,轉變後的風要有這個強度
SHIFT_FROM_SPEED = 2.0  # m/s,轉變前的風太弱時風向沒有意義
SHIFT_POLEWARD = 70.0  # 度,轉變後的風向與正北(南半球為正南)的夾角上限
SHIFT_MIN_LATITUDE = 15.0  # 更靠近赤道的地方沒有鋒面


# --------------------------------------------------------------------------- #
# 基本工具
# --------------------------------------------------------------------------- #


def wind_components(speed: float, direction: float) -> tuple[float, float]:
    """風速與風向(風的來向)轉成 (u, v),u 向東為正、v 向北為正。"""
    rad = math.radians(direction)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def wind_direction(u: float, v: float) -> float:
    return math.degrees(math.atan2(-u, -v)) % 360


def compass(direction: float) -> str:
    return ("北", "東北", "東", "東南", "南", "西南", "西", "西北")[round(direction / 45) % 8]


def dew_point(temperature: float, humidity: float) -> float:
    """Magnus 公式:由氣溫(°C)與相對濕度(%)求露點。"""
    gamma = math.log(max(humidity, 1.0) / 100) + 17.625 * temperature / (243.04 + temperature)
    return 243.04 * gamma / (17.625 - gamma)


def k_index(t850: float, rh850: float, t700: float, rh700: float, t500: float) -> float:
    """K 指數 = (T850 − T500) + Td850 − (T700 − Td700)。

    第一項是溫度遞減率,第二項是低層水氣,第三項是中層有多乾。
    一般 30 以上代表容易發生雷雨,20 以下大氣穩定。
    """
    return (t850 - t500) + dew_point(t850, rh850) - (t700 - dew_point(t700, rh700))


def _mean(values):
    kept = [v for v in values if v is not None]
    return sum(kept) / len(kept) if kept else None


def _pooled_median(by_system: dict, extract, index: int):
    """各系集等權重的成員中位數;extract 從單一系統取出各成員在該時間的值。"""
    groups = [g for g in (extract(variables, index) for variables in by_system.values()) if g]
    if not groups:
        return None
    pairs = [(v, 1 / len(g) / len(groups)) for g in groups for v in g]
    return wquantile(pairs, 0.5)


def _scalar(var: str):
    def extract(variables, index):
        return [m[index] for m in variables.get(var, []) if index < len(m) and m[index] is not None]

    return extract


def _component(level: str, which: int):
    """取出各成員在某一層的 u(which=0)或 v(which=1)。"""

    def extract(variables, index):
        speeds = variables.get(f"wind_speed_{level}", [])
        directions = variables.get(f"wind_direction_{level}", [])
        if len(speeds) != len(directions):
            return []
        out = []
        for s, d in zip(speeds, directions):
            if index < len(s) and index < len(d) and s[index] is not None and d[index] is not None:
                out.append(wind_components(s[index], d[index])[which])
        return out

    return extract


def _max(values):
    kept = [v for v in values if v is not None]
    return max(kept) if kept else None


def _daily(times: list[str], series: list, reducer=_mean) -> dict[str, float | None]:
    """把逐時間步的數值依當地日期彙整(預設取平均)。"""
    groups: dict[str, list] = {}
    for stamp, value in zip(times, series):
        groups.setdefault(stamp[:10], []).append(value)
    return {day: reducer(values) for day, values in groups.items()}


def _halves(times: list[str], series: list) -> dict[str, tuple[float | None, float | None]]:
    """每一天前半與後半的平均,用來看一天之內的變化。少於四筆的日子不分。"""
    groups: dict[str, list] = {}
    for stamp, value in zip(times, series):
        groups.setdefault(stamp[:10], []).append(value)
    out = {}
    for day, values in groups.items():
        half = len(values) // 2
        out[day] = (_mean(values[:half]), _mean(values[half:])) if len(values) >= 4 else (None, None)
    return out


# --------------------------------------------------------------------------- #
# 低層風向轉變(鋒面通過)
# --------------------------------------------------------------------------- #


@dataclass
class WindShift:
    """低層風在短時間內轉成來自極地一側:冷鋒或冷空氣前緣通過測站的訊號。"""

    start: str  # 轉變前最後一筆的時間
    end: str  # 轉變後第一筆的時間
    from_dir: float
    to_dir: float
    cooling: float | None = None  # 轉變後 24 小時內 925 hPa 降了幾度(沒有降溫則為 None)


def _turn(a: float, b: float) -> float:
    """兩個風向之間的夾角(0–180°)。"""
    return abs((a - b + 180) % 360 - 180)


def find_wind_shifts(times: list[str], u: list, v: list, temperature: list, latitude: float) -> dict[str, WindShift]:
    """在逐時間步的低層風序列上找風向轉變,回傳 {日期: WindShift}。

    條件:轉變後的風來自極地一側(北半球是西北到東北)且夠強,而且和 12 小時內
    某一筆夠強的風相比,風向轉了 80° 以上。同一道鋒面只記一次(之後 24 小時內
    不重複)。逐日平均看不到這種變化:西風 4 m/s 加東北風 7 m/s,平均起來只剩
    北風 2 m/s。
    """
    if abs(latitude) < SHIFT_MIN_LATITUDE:
        return {}
    pole = 0.0 if latitude >= 0 else 180.0
    stamps = [datetime.fromisoformat(t) for t in times]
    known = lambda i: u[i] is not None and v[i] is not None
    out: dict[str, WindShift] = {}
    last = None
    for k in range(1, len(times)):
        if not known(k) or math.hypot(u[k], v[k]) < SHIFT_SPEED:
            continue
        direction = wind_direction(u[k], v[k])
        if _turn(direction, pole) > SHIFT_POLEWARD:
            continue
        if last is not None and stamps[k] - stamps[last] < timedelta(hours=24):
            continue
        for j in range(k - 1, -1, -1):  # 由近到遠,取最近的一筆,轉變的時間範圍才不會被拉長
            if stamps[k] - stamps[j] > timedelta(hours=12):
                break
            if not known(j) or math.hypot(u[j], v[j]) < SHIFT_FROM_SPEED:
                continue
            before = wind_direction(u[j], v[j])
            if _turn(direction, before) < SHIFT_TURN:
                continue
            cooling = None
            later = [i for i in range(k, len(times)) if stamps[i] - stamps[k] <= timedelta(hours=24) and temperature[i] is not None]
            if temperature[j] is not None and later:
                drop = temperature[j] - min(temperature[i] for i in later)
                cooling = drop if drop > 0 else None
            out[times[k][:10]] = WindShift(times[j], times[k], before, direction, cooling)
            last = k
            break
    return out


# --------------------------------------------------------------------------- #
# 槽線與脊線
# --------------------------------------------------------------------------- #


def _prominence(series: list[float], i: int) -> float:
    """峰值 i 比兩側「必須先下降到多低才能走到更高的地方」高出多少。"""
    peak = series[i]
    lows = []
    for step in (-1, 1):
        j, low = i + step, peak
        while 0 <= j < len(series) and series[j] <= peak:
            low = min(low, series[j])
            j += step
        lows.append(low)
    return peak - max(lows)


def find_axes(offsets: list[float], heights: list[float], min_prominence: float) -> tuple[list[float], list[float]]:
    """沿緯圈的 500 hPa 高度找槽線(極小)與脊線(極大)的經度偏移。

    只取夠明顯的極值(以 prominence 過濾雜訊),並用拋物線內插出比格點更細的
    位置。剖面兩端無法判斷是不是極值,所以不算。
    """
    found = {}
    for sign in (-1, 1):  # -1 找槽,+1 找脊
        series = [sign * h for h in heights]
        axes = []
        for i in range(1, len(series) - 1):
            if series[i] > series[i - 1] and series[i] >= series[i + 1] and _prominence(series, i) >= min_prominence:
                curve = series[i - 1] - 2 * series[i] + series[i + 1]
                shift = 0.5 * (series[i - 1] - series[i + 1]) / curve if curve else 0.0
                axes.append(offsets[i] + max(-0.5, min(0.5, shift)) * (offsets[i + 1] - offsets[i]))
        found[sign] = axes
    return found[-1], found[1]


def classify_flow(offsets, heights, latitude: float, easterly: bool = False):
    """判斷測站相對於槽脊的位置。回傳 (型態, 相關槽線的偏移, 最近脊線的偏移)。

    「相關槽線」在槽前是西邊(上游)最近的那條,在槽後是東邊剛通過的那條。

    西風帶的槽脊由西向東移動:槽線在西邊時,測站吹偏南風(槽前),氣流上升;
    槽線已經過去、在東邊時,測站吹偏北風(槽後),氣流下沉。判斷依據是測站
    兩側的高度差——往東高度增加就是槽在西邊,南北半球都成立。
    """
    if easterly:
        return "東風帶", None, None
    subtropical = abs(latitude) < 30
    troughs, ridges = find_axes(offsets, heights, 5.0 if subtropical else 20.0)
    nearest = lambda axes: min(axes, key=abs) if axes else None
    trough, ridge = nearest(troughs), nearest(ridges)
    by_offset = dict(zip(offsets, heights))
    slope = by_offset[STENCIL] - by_offset[-STENCIL]
    flat = 8.0 if subtropical else 20.0
    if trough is not None and abs(trough) <= NEAR_AXIS:
        regime = "槽線過境"
    elif ridge is not None and abs(ridge) <= NEAR_AXIS:
        regime = "脊線上"
    elif slope >= flat:
        regime = "槽前"
    elif slope <= -flat:
        regime = "槽後"
    else:
        regime = "平直"
    if regime == "槽前":
        trough = nearest([t for t in troughs if t < 0])
    elif regime == "槽後":
        trough = nearest([t for t in troughs if t > 0])
    return regime, trough, ridge


# --------------------------------------------------------------------------- #
# 逐日高空分析
# --------------------------------------------------------------------------- #


@dataclass
class UpperDay:
    day: str
    # 250 hPa
    wind250: float | None = None  # m/s
    dir250: float | None = None
    jet_core: float | None = None  # 南北剖面上的最大風速
    jet_offset: float | None = None  # 噴流軸相對測站的緯度偏移(正值在北)
    jet_at_edge: bool = False  # 最大風速落在剖面邊緣,真正的噴流軸可能更遠
    div250: float | None = None  # 高層輻散,10⁻⁵ s⁻¹
    # 500 hPa
    z500: float | None = None
    t500: float | None = None
    wind500: float | None = None
    dir500: float | None = None
    regime: str = ""
    trough: float | None = None  # 最近槽線的經度偏移(負值在西)
    ridge: float | None = None
    subtropical_high: bool = False
    # 700 hPa
    rh700: float | None = None
    rh700_early: float | None = None  # 當天前半的平均
    rh700_late: float | None = None  # 當天後半的平均
    # 850 hPa
    t850: float | None = None
    rh850: float | None = None
    wind850: float | None = None
    dir850: float | None = None
    tadv850: float | None = None  # 溫度平流,°C/日
    # 925 hPa
    t925: float | None = None
    wind925: float | None = None
    dir925: float | None = None
    shift: WindShift | None = None  # 當天的低層風向轉變(鋒面通過)
    # 綜合
    k: float | None = None
    cape: float | None = None  # 當天各時刻的最大值,J/kg
    pwat: float | None = None  # 可降水量,mm
    lift: list[str] = field(default_factory=list)
    sink: list[str] = field(default_factory=list)
    moisture: str = ""  # 深厚 / 淺層 / 乾燥 / 中等
    outlook: str = ""  # wet / showery / shallow / stable / neutral
    verdict: str = ""

    @property
    def has_jet(self) -> bool:
        return self.jet_core is not None and self.jet_core >= JET_SPEED

    @property
    def unstable(self) -> bool:
        return (self.k is not None and self.k >= K_UNSTABLE) or (self.cape is not None and self.cape >= CAPE_UNSTABLE)


def _assess(day: UpperDay) -> None:
    """把各層的訊號歸納成舉升、水氣、不穩定三種成分,再給出研判。"""
    if day.regime == "槽線過境":
        day.lift.append("500 hPa 槽線過境")
    elif day.regime == "槽前" and day.trough is not None and -LIFT_RANGE <= day.trough < 0:
        day.lift.append(f"500 hPa 槽前(槽線在西方 {abs(day.trough):.0f}°)")
    elif day.regime == "槽後":
        day.sink.append("500 hPa 槽後")
    elif day.regime == "脊線上":
        day.sink.append("500 hPa 脊線上")
    if day.subtropical_high and day.regime not in ("槽線過境",):
        day.sink.append("副熱帶高壓籠罩")
    if day.div250 is not None:
        if day.div250 >= 0.3:
            day.lift.append("250 hPa 高層輻散")
        elif day.div250 <= -0.3:
            day.sink.append("250 hPa 高層輻合")
    if day.tadv850 is not None:
        if day.tadv850 >= 1.5:
            day.lift.append("850 hPa 暖平流")
        elif day.tadv850 <= -1.5:
            day.sink.append("850 hPa 冷平流")
    if day.shift is not None:
        day.lift.append("925 hPa 低層鋒面通過")

    low, mid = day.rh850, day.rh700
    if low is None or mid is None:
        day.moisture = ""
    elif low >= 70 and mid >= 70:
        day.moisture = "深厚"
    elif low >= 75 and mid < 55:
        day.moisture = "淺層"
    elif low < 60 and mid < 50:
        day.moisture = "乾燥"
    else:
        day.moisture = "中等"

    net = len(day.lift) - len(day.sink)
    unstable = day.unstable
    thunder = ",且大氣不穩定,可能有雷雨" if unstable else ""
    if day.moisture == "淺層":
        day.outlook = "shallow"
        if net >= 1:
            day.verdict = "有舉升,但水氣只在低層、中層乾燥,降雨以淺層為主、雨量有限"
        else:
            day.verdict = "水氣只在低層、中層乾燥,若有雨是低雲的淺層降雨,雨量有限"
    elif day.moisture == "乾燥":
        if net >= 1:
            day.outlook, day.verdict = "neutral", "有舉升但水氣不足,以雲量增加為主"
        else:
            day.outlook, day.verdict = "stable", "高空不利上升、水氣不足,天氣穩定"
    elif net >= 2 and day.moisture == "深厚":
        day.outlook, day.verdict = "wet", "舉升與深厚水氣俱備,有利系統性降雨" + thunder
    elif net >= 1:
        day.outlook, day.verdict = "showery", f"有舉升條件、水氣{day.moisture or '不明'},有降雨機會" + thunder
    elif net >= 0 and day.moisture == "深厚" and unstable:
        day.outlook, day.verdict = "showery", "水氣深厚且大氣不穩定,不需明顯舉升也容易有對流性陣雨或雷雨"
    elif net <= -1 and day.moisture == "深厚":
        day.outlook, day.verdict = "neutral", "水氣充足但高空不利上升,降雨不易發展"
    elif net <= -1:
        day.outlook, day.verdict = "stable", "高空不利上升運動,天氣穩定"
    elif day.lift and day.sink:
        day.outlook, day.verdict = "neutral", "舉升與抑制因素並存、互相抵銷"
    else:
        day.outlook, day.verdict = "neutral", "高空沒有明顯的舉升或下沉"


def analyse(upper: dict, latitude: float, days: int | None = None) -> list[UpperDay]:
    """由 client.fetch_upper() 的結果做逐日高空分析。"""
    profile = upper["profile"]
    times = profile["hourly"]["time"]
    site = _section(profile, "hourly", PROFILE_VARS)
    steps = range(len(times))

    def stepwise(extract):
        return [_pooled_median(site, extract, i) for i in steps]

    series = {
        name: stepwise(_scalar(var))
        for name, var in {
            "z500": "geopotential_height_500hPa",
            "t500": "temperature_500hPa",
            "t700": "temperature_700hPa",
            "rh700": "relative_humidity_700hPa",
            "t850": "temperature_850hPa",
            "rh850": "relative_humidity_850hPa",
            "t925": "temperature_925hPa",
            "pwat": "total_column_integrated_water_vapour",
            "cape": "cape",
        }.items()
    }
    scalars = {name: _daily(times, values, _max if name == "cape" else _mean) for name, values in series.items()}
    rh700_halves = _halves(times, series["rh700"])
    # 各層的風:u、v 逐時刻取成員中位數;風速另外取成員風速的中位數,不由 u、v 的平均反推
    wind_steps = {level: (stepwise(_component(f"{level}hPa", 0)), stepwise(_component(f"{level}hPa", 1))) for level in (250, 500, 850, 925)}
    winds = {level: (_daily(times, u), _daily(times, v)) for level, (u, v) in wind_steps.items()}
    wind_speeds = {level: _daily(times, stepwise(_scalar(f"wind_speed_{level}hPa"))) for level in wind_steps}
    shifts = find_wind_shifts(times, *wind_steps[925], series["t925"], latitude)

    # 十字剖面:每個點、每一天的成員中位數
    cross = upper["cross"]
    cross_times = cross["data"][0]["hourly"]["time"]
    grid: dict[tuple[str, int], dict[str, dict]] = {}
    t850_steps: dict[tuple[str, int], list] = {}  # 逐時刻的 850 hPa 溫度,算平流用
    for point, data in zip(cross["points"], cross["data"]):
        one = _section(data, "hourly", CROSS_VARS)
        along = lambda extract: [_pooled_median(one, extract, i) for i in range(len(cross_times))]
        where = (point["axis"], point["offset"])
        t850_steps[where] = along(_scalar("temperature_850hPa"))
        grid[where] = {
            "z500": _daily(cross_times, along(_scalar("geopotential_height_500hPa"))),
            "t850": _daily(cross_times, t850_steps[where]),
            "u250": _daily(cross_times, along(_component("250hPa", 0))),
            "v250": _daily(cross_times, along(_component("250hPa", 1))),
        }
    we_offsets = sorted(o for axis, o in grid if axis == "we")
    ns_offsets = sorted(o for axis, o in grid if axis == "ns")
    at = lambda axis, offset, name, day: grid.get((axis, offset), {}).get(name, {}).get(day)

    dx = EARTH_RADIUS * math.cos(math.radians(latitude)) * math.radians(2 * STENCIL)
    dy = EARTH_RADIUS * math.radians(2 * STENCIL)

    # 850 hPa 溫度平流 = −(u ∂T/∂x + v ∂T/∂y),逐時刻計算後再取日平均。
    # 風和溫度梯度在鋒面通過當天都會變號,先各自平均再相乘會互相抵銷。
    cross_index = {stamp: j for j, stamp in enumerate(cross_times)}
    u850_steps, v850_steps = wind_steps[850]

    def advection(i: int):
        j = cross_index.get(times[i])
        if j is None:
            return None
        corners = [t850_steps.get(key, [None] * len(cross_times))[j] for key in (("we", STENCIL), ("we", -STENCIL), ("ns", STENCIL), ("ns", -STENCIL))]
        if None in corners or u850_steps[i] is None or v850_steps[i] is None:
            return None
        te, tw, tn, ts = corners
        return -(u850_steps[i] * (te - tw) / dx + v850_steps[i] * (tn - ts) / dy) * 86400

    tadv850 = _daily(times, [advection(i) for i in steps])

    out = []
    for day in list(dict.fromkeys(t[:10] for t in times))[: days or None]:
        row = UpperDay(day=day, **{k: scalars[k][day] for k in ("z500", "t500", "rh700", "t850", "rh850", "t925", "pwat", "cape")})
        row.rh700_early, row.rh700_late = rh700_halves[day]
        row.shift = shifts.get(day)
        for level in (250, 500, 850, 925):
            speed_field, dir_field = f"wind{level}", f"dir{level}"
            u, v = winds[level][0][day], winds[level][1][day]
            if u is not None and v is not None:
                # 風速是各時刻風速的平均;風向取向量平均的方向(一天內風向大轉變時見 shift)
                speed = wind_speeds[level][day]
                setattr(row, speed_field, speed if speed is not None else math.hypot(u, v))
                setattr(row, dir_field, wind_direction(u, v))

        # 500 hPa 槽脊
        heights = [at("we", o, "z500", day) for o in we_offsets]
        if all(h is not None for h in heights):
            u500 = winds[500][0][day]
            easterly = abs(latitude) < 20 and u500 is not None and u500 < 0
            row.regime, row.trough, row.ridge = classify_flow(we_offsets, heights, latitude, easterly)
        # 赤道附近 500 hPa 高度終年都在 5880 m 上下,這條線只在副熱帶才有意義
        row.subtropical_high = 15 <= abs(latitude) < 40 and row.z500 is not None and row.z500 >= SUBTROPICAL_HIGH

        # 250 hPa 噴流軸(南北剖面上風速最大的位置)
        speeds = {}
        for offset in [0, *ns_offsets]:
            axis = "we" if offset == 0 else "ns"
            u, v = at(axis, offset, "u250", day), at(axis, offset, "v250", day)
            if u is not None and v is not None:
                speeds[offset] = math.hypot(u, v)
        if speeds:
            row.jet_offset = max(speeds, key=speeds.get)
            row.jet_core = speeds[row.jet_offset]
            row.jet_at_edge = row.jet_offset in (min(speeds), max(speeds))

        # 250 hPa 輻散 = ∂u/∂x + ∂v/∂y − v·tanφ/a。最後一項是球面的修正:經線往極地
        # 收攏,往極地吹的風即使風速不變也會輻合。
        ue, uw = at("we", STENCIL, "u250", day), at("we", -STENCIL, "u250", day)
        vn, vs = at("ns", STENCIL, "v250", day), at("ns", -STENCIL, "v250", day)
        v0 = at("we", 0, "v250", day)
        if None not in (ue, uw, vn, vs):
            metric = v0 * math.tan(math.radians(latitude)) / EARTH_RADIUS if v0 is not None else 0.0
            row.div250 = ((ue - uw) / dx + (vn - vs) / dy - metric) * 1e5

        row.tadv850 = tadv850.get(day)

        t700 = scalars["t700"][day]
        if None not in (row.t850, row.rh850, t700, row.rh700, row.t500):
            row.k = k_index(row.t850, row.rh850, t700, row.rh700, row.t500)
        _assess(row)
        out.append(row)
    return out


def trough_passage(rows: list[UpperDay]) -> tuple[str, str] | None:
    """找出 500 hPa 槽線通過測站的時間。

    回傳 (前一天, 當天):兩者相同表示槽線當天就在上空,不同表示在這兩天之間通過。
    """
    for prev, cur in zip(rows, rows[1:]):
        if cur.regime == "槽線過境":
            return cur.day, cur.day
        approaching = prev.regime in ("槽前", "槽線過境") and prev.trough is not None and -LIFT_RANGE <= prev.trough <= NEAR_AXIS
        gone = cur.regime != "槽前" and (cur.trough is None or cur.trough > 0 or cur.trough < prev.trough - 10)
        if approaching and gone:
            return prev.day, cur.day
    return None
