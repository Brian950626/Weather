"""高空分析:從 250 / 500 / 700 / 850 / 925 hPa 判斷槽脊、噴流與它們對地面天氣的影響。

地面的雨和溫度是結果,原因在上面。這個模組用「成分法」把原因拆開來看:

- 舉升:500 hPa 槽前、250 hPa 高層輻散、850 hPa 暖平流
- 水氣:850 hPa(低層)與 700 hPa(中層)的相對濕度
- 不穩定:K 指數(850、700、500 hPa 的溫度與露點)

三者俱備才會有系統性降雨;只有低層水氣而中層乾,雨就只來自淺層的雲。

沒有使用模式的垂直速度:資料來源沒有說明正負號的定義,實測(與降雨的相關)
也無法確認,而且單點的瞬時值雜訊很大,所以改用可以自己算、正負號明確的
高層輻散與溫度平流來代表上升或下沉。

資料有兩份(都取系集成員的中位數):

- 垂直剖面:測站上空各層,ECMWF 與 AIFS 兩套系集。
- 十字剖面:500 hPa 高度沿測站緯度往東西各 40°(找槽線與脊線),250 hPa 風
  沿測站經度往南北各 20°(找噴流軸),ECMWF 系集。相鄰點同時用來算水平梯度。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .analysis import _section, wquantile
from .systems import CROSS_VARS, PROFILE_VARS

EARTH_RADIUS = 6_371_000.0
STENCIL = 5  # 計算水平梯度時使用的相鄰點距離(度)
JET_SPEED = 30.0  # m/s,250 hPa 風速達此值視為噴流
SUBTROPICAL_HIGH = 5880.0  # m,500 hPa 副熱帶高壓的慣用特徵線
NEAR_AXIS = 5.0  # 槽線或脊線在這個經度範圍內,視為正在測站上空
LIFT_RANGE = 15.0  # 槽線在西方這個範圍內,槽前的舉升才算影響得到測站


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


def _daily(times: list[str], series: list) -> dict[str, float | None]:
    """把逐時間步的數值依當地日期取平均。"""
    groups: dict[str, list] = {}
    for stamp, value in zip(times, series):
        groups.setdefault(stamp[:10], []).append(value)
    return {day: _mean(values) for day, values in groups.items()}


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
    # 綜合
    k: float | None = None
    lift: list[str] = field(default_factory=list)
    sink: list[str] = field(default_factory=list)
    moisture: str = ""  # 深厚 / 淺層 / 乾燥 / 中等
    outlook: str = ""  # wet / showery / shallow / stable / neutral
    verdict: str = ""

    @property
    def has_jet(self) -> bool:
        return self.jet_core is not None and self.jet_core >= JET_SPEED


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
    unstable = day.k is not None and day.k >= 30
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

    def daily(extract):
        return _daily(times, [_pooled_median(site, extract, i) for i in steps])

    scalars = {
        name: daily(_scalar(var))
        for name, var in {
            "z500": "geopotential_height_500hPa",
            "t500": "temperature_500hPa",
            "t700": "temperature_700hPa",
            "rh700": "relative_humidity_700hPa",
            "t850": "temperature_850hPa",
            "rh850": "relative_humidity_850hPa",
            "t925": "temperature_925hPa",
        }.items()
    }
    winds = {level: (daily(_component(f"{level}hPa", 0)), daily(_component(f"{level}hPa", 1))) for level in (250, 500, 850, 925)}

    # 十字剖面:每個點、每一天的成員中位數
    cross = upper["cross"]
    cross_times = cross["data"][0]["hourly"]["time"]
    grid: dict[tuple[str, int], dict[str, dict]] = {}
    for point, data in zip(cross["points"], cross["data"]):
        one = _section(data, "hourly", CROSS_VARS)
        series = lambda extract: _daily(cross_times, [_pooled_median(one, extract, i) for i in range(len(cross_times))])
        grid[(point["axis"], point["offset"])] = {
            "z500": series(_scalar("geopotential_height_500hPa")),
            "t850": series(_scalar("temperature_850hPa")),
            "u250": series(_component("250hPa", 0)),
            "v250": series(_component("250hPa", 1)),
        }
    we_offsets = sorted(o for axis, o in grid if axis == "we")
    ns_offsets = sorted(o for axis, o in grid if axis == "ns")
    at = lambda axis, offset, name, day: grid.get((axis, offset), {}).get(name, {}).get(day)

    dx = EARTH_RADIUS * math.cos(math.radians(latitude)) * math.radians(2 * STENCIL)
    dy = EARTH_RADIUS * math.radians(2 * STENCIL)

    out = []
    for day in list(dict.fromkeys(t[:10] for t in times))[: days or None]:
        row = UpperDay(day=day, **{k: scalars[k][day] for k in ("z500", "t500", "rh700", "t850", "rh850", "t925")})
        for level in (250, 500, 850, 925):
            speed_field, dir_field = f"wind{level}", f"dir{level}"
            u, v = winds[level][0][day], winds[level][1][day]
            if u is not None and v is not None:
                setattr(row, speed_field, math.hypot(u, v))
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

        # 250 hPa 輻散 = ∂u/∂x + ∂v/∂y
        ue, uw = at("we", STENCIL, "u250", day), at("we", -STENCIL, "u250", day)
        vn, vs = at("ns", STENCIL, "v250", day), at("ns", -STENCIL, "v250", day)
        if None not in (ue, uw, vn, vs):
            row.div250 = ((ue - uw) / dx + (vn - vs) / dy) * 1e5

        # 850 hPa 溫度平流 = −(u ∂T/∂x + v ∂T/∂y)
        te, tw = at("we", STENCIL, "t850", day), at("we", -STENCIL, "t850", day)
        tn, ts = at("ns", STENCIL, "t850", day), at("ns", -STENCIL, "t850", day)
        u850, v850 = winds[850][0][day], winds[850][1][day]
        if None not in (te, tw, tn, ts, u850, v850):
            row.tadv850 = -(u850 * (te - tw) / dx + v850 * (tn - ts) / dy) * 86400

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
