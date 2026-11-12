"""把逐日預報整理成一份「預報報告」的骨架。

骨架來自課堂上的一週天氣預報報告(先講結論):

1. 重點摘要——把一週依「主要影響系統」分成幾個時段,各有信心度與理由
2. 現況分析——由地面到高層,每一層都寫出「對預報的意義」
3. 綜觀演變與預報理由——每個時段依 500 → 850 → 250 hPa → 水氣的順序,最後做垂直結構檢查
4. 模式比較——各模式怎麼說,以及綜合判斷與理由
5. 注意事項與劇烈天氣
6. 不確定性與替代情境、接下來要觀察什麼

這裡只做判讀與文字,不碰資料抓取與排版。所有門檻都是經驗值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .analysis import DayForecast, DayPattern, Signal, confidence_label, confidence_reasons
from .upper import LIFT_RANGE, PWAT_RICH, SUBTROPICAL_HIGH, UpperDay, _turn, compass, trough_passage

STABLE = {"ridge", "warm", "quiet"}  # 其餘(trough / front / cold / wet / low)算作「有天氣系統影響」
MAX_STAGES = 3


def label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.month}/{d.day}({'一二三四五六日'[d.weekday()]})"


def _span(days: list[str]) -> str:
    return label(days[0]) if len(days) == 1 else f"{label(days[0])}–{label(days[-1])}"


def _range(values, unit: str = "", digits: int = 0) -> str:
    """一串數值的範圍,例如「13–16°C」;全部相同時只寫一個。"""
    kept = [v for v in values if v is not None]
    if not kept:
        return "–"
    low, high = f"{min(kept):.{digits}f}", f"{max(kept):.{digits}f}"
    return f"{low}{unit}" if low == high else f"{low}–{high}{unit}"


# --------------------------------------------------------------------------- #
# 每天的主要影響系統
# --------------------------------------------------------------------------- #


@dataclass
class DayRole:
    day: str
    kind: str  # ridge / warm / quiet / trough / front / cold / wet / low
    system: str  # 報表上的「主要影響系統」


def _monsoon(latitude: float, longitude: float, direction: float | None) -> bool:
    """東亞冬季季風區,而且低層吹北到東風:這時的冷空氣就叫東北季風。"""
    return 10 <= latitude <= 40 and 100 <= longitude <= 135 and direction is not None and (direction >= 330 or direction <= 110)


def day_roles(
    forecast: list[DayForecast],
    signals: list[Signal],
    upper_days: list[UpperDay] | None,
    latitude: float,
    longitude: float = 0.0,
) -> list[DayRole]:
    """判斷每一天由什麼天氣系統主導。

    順序就是優先順序:熱帶系統或深低壓 → 低層鋒面 → 鋒後冷空氣 → 高空槽 → 高壓 → 回暖。
    沒有高空資料時,只靠氣壓、850 hPa 溫度與 500 hPa 高度的逐日變化判斷。
    """
    kinds: dict[str, set[str]] = {}
    for s in signals:
        kinds.setdefault(s.day, set()).add(s.kind)
    rows = {r.day: r for r in upper_days or []}
    pole = 0.0 if latitude >= 0 else 180.0
    out: list[DayRole] = []
    previous: DayForecast | None = None
    for fc in forecast:
        seen, row = kinds.get(fc.day, set()), rows.get(fc.day)
        after_front = bool(out) and out[-1].kind in ("front", "cold")
        cold_side = row is not None and row.dir925 is not None and _turn(row.dir925, pole) <= 100
        monsoon = _monsoon(latitude, longitude, row.dir925 if row else None)
        warming = previous is not None and fc.tmax.p50 - previous.tmax.p50 >= 2
        if "deep_low" in seen:
            role = ("low", "低壓或熱帶系統接近")
        elif row is not None and row.shift is not None:
            strong = row.shift.cooling is not None and row.shift.cooling >= 3
            surge = "東北季風增強" if _monsoon(latitude, longitude, row.shift.to_dir) else "冷空氣南下" if latitude >= 0 else "冷空氣北上"
            role = ("front", f"鋒面通過、{surge}" if strong else "鋒面通過")
        elif (after_front and cold_side and not warming) or {"cold", "pressure_rise"} <= seen:
            role = ("cold", "東北季風影響" if monsoon or row is None and 10 <= latitude <= 40 and 100 <= longitude <= 135 else "鋒後冷空氣影響")
        elif row is not None and fc.pop >= 0.4 and (row.regime == "槽線過境" or (row.regime == "槽前" and row.trough is not None and -LIFT_RANGE <= row.trough < 0)):
            role = ("trough", "高空槽接近")
        elif row is None and "trough" in seen and fc.pop >= 0.4:
            role = ("trough", "高空槽接近")
        elif "pressure_fall" in seen and fc.pop >= 0.5:
            role = ("trough", "低壓或鋒面接近")
        elif fc.pop >= 0.6:
            role = ("wet", "水氣偏多、有雨")
        elif row is not None and row.subtropical_high:
            role = ("ridge", "副熱帶高壓籠罩")
        elif row is not None and row.regime == "脊線上":
            role = ("ridge", "高壓脊籠罩")
        elif "warm" in seen or warming and (row is None or row.tadv850 is None or row.tadv850 >= 1.0):
            role = ("warm", "偏南風回暖" if latitude >= 0 else "偏北風回暖")
        elif "ridge" in seen:
            role = ("ridge", "高壓脊建立")
        else:
            role = ("quiet", "天氣穩定" if fc.pop < 0.3 else "沒有明顯天氣系統,局部短暫雨")
        out.append(DayRole(fc.day, *role))
        previous = fc
    return out


# --------------------------------------------------------------------------- #
# 時段
# --------------------------------------------------------------------------- #


@dataclass
class Stage:
    """一個時段:由同一類天氣系統主導的連續幾天。"""

    index: int
    days: list[str]
    headline: str  # 例如「高空槽接近 → 鋒面通過 → 東北季風影響」
    weather: str  # 例如「多雲到晴」「有雨」
    tmax: tuple[float, float]
    tmin: tuple[float, float]
    pop: tuple[float, float]
    confidence: int
    confidence_label: str
    stable: bool = True  # 這個時段沒有天氣系統影響
    reasons: list[str] = field(default_factory=list)  # 信心度的理由
    rationale: list[tuple[str, str]] = field(default_factory=list)  # (層次, 說明):預報理由
    coupling: str = ""  # 垂直結構檢查

    @property
    def span(self) -> str:
        return _span(self.days)

    @property
    def summary(self) -> str:
        temps = f"高溫 {_range(self.tmax, '°C')}、低溫 {_range(self.tmin, '°C')}"
        pops = _range([p * 100 for p in self.pop], "%")
        return f"{self.span} {self.headline}:{self.weather},{temps},降雨機率 {pops}。信心度{self.confidence_label}。"


def _weather(days: list[DayForecast]) -> str:
    """幾天的天氣合成一句,例如「多雲,後期有雨」。"""
    seen = list(dict.fromkeys(fc.sky for fc in days))
    return seen[0] if len(seen) == 1 else f"{seen[0]}轉{seen[-1]}" if len(seen) == 2 else "、".join(seen[:3])


def _rationale(rows: list[UpperDay], southern: bool, wettest: float = 1.0) -> tuple[list[tuple[str, str]], str]:
    """一個時段的預報理由:500 → 850 → 250 hPa → 水氣,再做垂直結構檢查。"""
    if not rows:
        return [], ""
    out = []
    regimes = list(dict.fromkeys(r.regime for r in rows if r.regime))
    text = "、".join(regimes) or "型態不明"
    troughs = [r.trough for r in rows if r.regime == "槽前" and r.trough is not None]
    if troughs:
        text += f"(槽線在西方 {_range([abs(t) for t in troughs], '°')})"
    heights = [r.z500 for r in rows if r.z500 is not None]
    if heights:
        text += f",高度 {_range(heights, ' m')}"
        if min(heights) >= SUBTROPICAL_HIGH and any(r.subtropical_high for r in rows):
            text += ",在 588 線(5880 m)以內,下沉、穩定、偏暖"
        elif max(heights) < SUBTROPICAL_HIGH and any(r.regime in ("槽前", "槽線過境") for r in rows):
            text += ",槽前氣流上升、有利降雨"
    out.append(("500 hPa", text + "。"))

    winds = list(dict.fromkeys(compass(r.dir850) for r in rows if r.dir850 is not None))
    advection = [r.tadv850 for r in rows if r.tadv850 is not None]
    text = f"溫度 {_range([r.t850 for r in rows], '°C')},{'轉'.join(winds[:3]) or '風向不明'}風"
    if advection:
        if min(advection) >= 1.5:
            text += f",暖平流({_range(advection, ' °C/日', 1)}),增溫並有利上升"
        elif max(advection) <= -1.5:
            text += f",冷平流({_range(advection, ' °C/日', 1)}),降溫、氣流下沉"
        elif max(advection) >= 1.5 and min(advection) <= -1.0:
            text += ",先暖平流、後轉冷平流,是鋒面通過的特徵"
        elif max(advection) >= 1.5:
            text += f",偏暖平流(最大 {max(advection):+.1f} °C/日)"
        elif min(advection) <= -1.5:
            text += f",偏冷平流(最強 {min(advection):+.1f} °C/日)"
        else:
            text += ",溫度平流不明顯"
    shifts = [r for r in rows if r.shift is not None]
    for r in shifts:
        text += f";925 hPa 在 {label(r.day)} 由{compass(r.shift.from_dir)}風轉{compass(r.shift.to_dir)}風(低層鋒面通過)"
        if r.shift.cooling is not None and r.shift.cooling >= 1:
            text += f",之後降溫 {r.shift.cooling:.0f}°C"
    out.append(("850 / 925 hPa", text + "。"))

    jets = [r for r in rows if r.has_jet]
    divergence = [r.div250 for r in rows if r.div250 is not None]
    if jets:
        near = min(jets, key=lambda r: abs(r.jet_offset))
        where = "測站上空" if near.jet_offset == 0 else f"{'北' if near.jet_offset > 0 else '南'}方 {abs(near.jet_offset)}°"
        text = f"噴流軸最近時在{where},最大風速 {_range([r.jet_core for r in jets], ' m/s')}"
    else:
        text = "附近沒有噴流(250 hPa 風速不到 30 m/s)"
    if divergence:
        if max(divergence) >= 0.3 and min(divergence) > -0.3:
            text += ",高層輻散,有利下方空氣上升"
        elif min(divergence) <= -0.3 and max(divergence) < 0.3:
            text += ",高層輻合,不利上升"
        elif max(divergence) >= 0.3:
            text += ",高層輻散與輻合交替"
        else:
            text += ",高層輻散不明顯"
    out.append(("250 hPa", text + "。"))

    text = f"850 hPa 濕度 {_range([r.rh850 for r in rows], '%')}、700 hPa {_range([r.rh700 for r in rows], '%')}"
    pwat = [r.pwat for r in rows if r.pwat is not None]
    if pwat:
        text += f",可降水量 {_range(pwat, ' mm')}" + ("(整層水氣充沛)" if max(pwat) >= PWAT_RICH else "")
    k = [r.k for r in rows if r.k is not None]
    if k:
        text += f";K 指數 {_range(k)}"
    cape = [r.cape for r in rows if r.cape is not None]
    if cape:
        text += f"、CAPE 最大 {max(cape):.0f} J/kg"
    text += ",大氣不穩定、可能有雷雨" if any(r.unstable for r in rows) else ",不穩定度不高,以層狀或地形性降雨為主" if k else ""
    out.append(("水氣與不穩定", text + "。"))

    low = any(r.shift is not None or (r.tadv850 is not None and r.tadv850 >= 1.5) for r in rows)
    high = any(r.regime == "槽線過境" or (r.regime == "槽前" and r.trough is not None and -LIFT_RANGE <= r.trough < 0) or (r.div250 is not None and r.div250 >= 0.3) for r in rows)
    deep = any(r.moisture == "深厚" for r in rows)
    shallow = all(r.moisture in ("淺層", "乾燥") for r in rows)
    if low and high:
        coupling = "低層舉升與高層條件配合" + (",水氣也深厚,有利較大範圍的降雨。" if deep else ",但中層偏乾,雨量受限。" if shallow else "。")
    elif low and wettest < 0.3:
        coupling = "只有低層的弱舉升、高層不配合,不足以造成降雨,天氣穩定。"
    elif low:
        coupling = "低層有舉升、高層不配合,降雨以淺層或地形性為主,集中在迎風面。"
    elif high:
        coupling = "高層有利上升,但低層缺少舉升機制" + (",水氣也不足。" if shallow else ",降雨零星。")
    else:
        coupling = "高低層都沒有明顯的上升機制,天氣穩定。"
    return out, coupling


def _group(kinds: list[str]) -> list[list[int]]:
    """把連續的日子依「穩定/有天氣系統影響」切段,最多 MAX_STAGES 段。"""
    groups: list[list[int]] = []
    for i, kind in enumerate(kinds):
        if groups and (kinds[groups[-1][-1]] in STABLE) == (kind in STABLE):
            groups[-1].append(i)
        else:
            groups.append([i])
    while len(groups) > MAX_STAGES:
        sizes = [len(a) + len(b) for a, b in zip(groups, groups[1:])]
        at = max(i for i, size in enumerate(sizes) if size == min(sizes))  # 一樣短時併後面的
        groups[at : at + 2] = [groups[at] + groups[at + 1]]
    return groups


def outline(role_sets: list[list[dict]]) -> list[dict]:
    """多個地點合起來看:每天取最多地點認定的影響系統,再分成時段。

    role_sets 的每一項是一個地點的逐日角色({"day", "kind", "system"})。
    回傳 [{"days": [...], "headline": ..., "systems": {日期: 系統}}]。
    """
    by_day: dict[str, list[tuple[str, str]]] = {}
    for roles in role_sets:
        for role in roles:
            by_day.setdefault(role["day"], []).append((role["kind"], role["system"]))
    days = sorted(by_day)
    if not days:
        return []
    common = [max(dict.fromkeys(by_day[d]), key=by_day[d].count) for d in days]
    out = []
    for members in _group([kind for kind, _ in common]):
        out.append(
            {
                "days": [days[i] for i in members],
                "headline": " → ".join(dict.fromkeys(common[i][1] for i in members)),
                "stable": all(common[i][0] in STABLE for i in members),
                "systems": {days[i]: common[i][1] for i in members},
            }
        )
    return out


def stages(
    forecast: list[DayForecast],
    roles: list[DayRole],
    upper_days: list[UpperDay] | None = None,
    southern: bool = False,
) -> list[Stage]:
    """把預報期間分成最多三個時段。

    先依「穩定」與「有天氣系統影響」切段;超過三段時,把最短的相鄰兩段併在一起,
    標題寫成「回暖 → 鋒面通過」這樣的先後順序。
    """
    groups = _group([role.kind for role in roles])
    rows = {r.day: r for r in upper_days or []}
    out = []
    for index, members in enumerate(groups, 1):
        days = [forecast[i] for i in members]
        score = round(sum(fc.confidence for fc in days) / len(days))
        weakest = min(days, key=lambda fc: fc.confidence)
        rationale, coupling = _rationale([rows[fc.day] for fc in days if fc.day in rows], southern, max(fc.pop for fc in days))
        out.append(
            Stage(
                index=index,
                days=[fc.day for fc in days],
                headline=" → ".join(dict.fromkeys(roles[i].system for i in members)),
                weather=_weather(days),
                tmax=(min(fc.tmax.p50 for fc in days), max(fc.tmax.p50 for fc in days)),
                tmin=(min(fc.tmin.p50 for fc in days), max(fc.tmin.p50 for fc in days)),
                pop=(min(fc.pop for fc in days), max(fc.pop for fc in days)),
                confidence=score,
                confidence_label=confidence_label(score),
                stable=all(roles[i].kind in STABLE for i in members),
                reasons=[f"{label(weakest.day)}:" + "、".join(confidence_reasons(weakest))] if len(days) > 1 else confidence_reasons(weakest),
                rationale=rationale,
                coupling=coupling,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 現況分析
# --------------------------------------------------------------------------- #


def current_analysis(row: UpperDay | None, patterns: list[DayPattern], southern: bool = False) -> list[tuple[str, str, str]]:
    """預報第一天的各層狀況:(層次, 觀察重點, 對預報的意義),由地面往上。"""
    out = []
    table = [p for p in patterns if p.mslp is not None]
    if table:
        now = table[0]
        meaning = "氣壓變化不大,天氣型態暫時不變"
        if len(table) > 1:
            change = table[1].mslp - now.mslp
            if change >= 2:
                meaning = f"隔天上升 {change:.0f} hPa,高壓增強"
            elif change <= -2:
                meaning = f"隔天下降 {abs(change):.0f} hPa,低壓或鋒面接近"
        out.append(("地面", f"海平面氣壓 {now.mslp:.0f} hPa", meaning))
    if row is None:
        return out
    if row.wind925 is not None and row.dir925 is not None:
        seen = f"{compass(row.dir925)}風 {row.wind925:.0f} m/s" + (f",{row.t925:.0f}°C" if row.t925 is not None else "")
        if row.shift is not None:
            meaning = "風向在當天轉變,低層鋒面通過"
        elif row.wind925 >= 8 and row.rh850 is not None and row.rh850 >= 80:
            meaning = "低層風強且潮濕,迎風面容易有地形雨"
        else:
            meaning = "低層風不強,沒有明顯的水氣輸送"
        out.append(("925 hPa", seen, meaning))
    if row.t850 is not None and row.rh850 is not None:
        seen = f"{row.t850:.0f}°C、濕度 {row.rh850:.0f}%"
        if row.dir850 is not None and row.wind850 is not None:
            seen += f"、{compass(row.dir850)}風 {row.wind850:.0f} m/s"
        if row.tadv850 is None or abs(row.tadv850) < 1.5:
            meaning = "溫度平流不明顯"
        elif row.tadv850 > 0:
            meaning = f"暖平流 {row.tadv850:+.1f} °C/日,增溫並有利上升"
        else:
            meaning = f"冷平流 {row.tadv850:+.1f} °C/日,降溫、氣流下沉"
        out.append(("850 hPa", seen, meaning))
    if row.rh700 is not None:
        meaning = "中層潮濕,雲可以發展得高" if row.rh700 >= 70 else "中層乾燥,只有低雲與淺層降雨" if row.rh700 < 55 else "中層水氣中等"
        out.append(("700 hPa", f"濕度 {row.rh700:.0f}%", meaning))
    if row.z500 is not None:
        seen = f"{row.regime or '型態不明'},高度 {row.z500:.0f} m"
        if row.subtropical_high:
            meaning = "在 588 線(5880 m)以內,副熱帶高壓控制,下沉、穩定"
        elif row.regime == "槽前":
            meaning = "槽前吹" + ("偏北風" if southern else "偏南風") + "、氣流上升,槽線越近越容易下雨"
        elif row.regime == "槽後":
            meaning = "槽後氣流下沉,天氣轉穩定"
        elif row.regime == "槽線過境":
            meaning = "槽線在上空,天氣最不穩定的時候"
        elif row.regime == "脊線上":
            meaning = "脊線上氣流下沉,天氣穩定"
        else:
            meaning = "氣流平直,沒有明顯的槽脊"
        out.append(("500 hPa", seen, meaning))
    if row.jet_core is not None:
        if row.has_jet:
            where = "測站上空" if row.jet_offset == 0 else f"{'北' if row.jet_offset > 0 else '南'}方 {abs(row.jet_offset)}°"
            seen = f"噴流軸在{where},{row.jet_core:.0f} m/s"
        else:
            seen = f"沒有噴流(最大 {row.jet_core:.0f} m/s)"
        if row.div250 is None or abs(row.div250) < 0.3:
            meaning = "高層輻散不明顯,對降雨沒有額外幫助"
        elif row.div250 > 0:
            meaning = "高層輻散,有利下方空氣上升"
        else:
            meaning = "高層輻合,不利上升"
        out.append(("250 hPa", seen, meaning))
    parts = [f"可降水量 {row.pwat:.0f} mm" if row.pwat is not None else "", f"K 指數 {row.k:.0f}" if row.k is not None else "", f"CAPE {row.cape:.0f} J/kg" if row.cape is not None else ""]
    if any(parts):
        meaning = "大氣不穩定,有對流時可能發展成雷雨" if row.unstable else "不穩定度不高,不利深對流"
        out.append(("整層", "、".join(p for p in parts if p), meaning))
    return out


# --------------------------------------------------------------------------- #
# 模式比較
# --------------------------------------------------------------------------- #


@dataclass
class ModelRow:
    day: str
    systems: dict[str, tuple[float, float, float]]  # 模式 → (高溫, 降雨機率, 雨量中位數)
    blend: tuple[float, float, float]  # 綜合(所有成員)
    verdict: str  # 判斷與理由
    split: bool = False  # 模式之間看法明顯分歧


def model_comparison(forecast: list[DayForecast]) -> list[ModelRow]:
    """每天各模式怎麼說,以及綜合判斷。對應報告的「模式比較表」。"""
    out = []
    for fc in forecast:
        systems = {name: (fc.system_tmax.get(name, fc.tmax.p50), pop, fc.system_precip.get(name, 0.0)) for name, pop in fc.system_pop.items()}
        notes, split = [], False
        if len(systems) > 1 and fc.pop_gap >= 0.4:
            split = True
            wet = [n for n, (_, pop, _) in systems.items() if pop >= 0.5]
            dry = [n for n in systems if n not in wet]
            if wet and dry:
                minority, side = (dry, "偏乾") if len(dry) < len(wet) else (wet, "偏濕") if len(wet) < len(dry) else ([], "")
                if minority:
                    notes.append(f"{'、'.join(minority)} {side},其餘模式一致;採多數意見,但降低信心")
                else:
                    notes.append(f"分成兩派({'、'.join(wet)} 有雨,{'、'.join(dry)} 少雨),還沒有定論")
            else:
                notes.append("都偏向" + ("有雨" if wet else "少雨") + ",但機率差距大")
        if len(systems) > 1 and fc.tmax_gap >= 2:
            high = max(systems, key=lambda n: systems[n][0])
            low = min(systems, key=lambda n: systems[n][0])
            notes.append(f"高溫 {high} 最高、{low} 最低,相差 {fc.tmax_gap:.0f}°C")
        if not notes:
            notes.append("各模式一致" if len(systems) > 1 else "只有一套模式,無法比較")
        out.append(ModelRow(fc.day, systems, (fc.tmax.p50, fc.pop, fc.precip.p50), ";".join(notes) + "。", split))
    return out


# --------------------------------------------------------------------------- #
# 注意事項、替代情境、觀察點
# --------------------------------------------------------------------------- #


def _dates(days: list[str]) -> str:
    """把日期合併成「11/15(日)–11/16(一)、11/18(三)」。"""
    groups: list[list[str]] = []
    for day in days:
        if groups and (date.fromisoformat(day) - date.fromisoformat(groups[-1][-1])).days == 1:
            groups[-1].append(day)
        else:
            groups.append([day])
    return "、".join(_span(g) for g in groups)


def hazards(forecast: list[DayForecast], roles: list[DayRole], upper_days: list[UpperDay] | None = None) -> list[tuple[str, str, str]]:
    """注意事項與劇烈天氣:(危害, 時間, 說明)。沒有訊號時回傳空串列。"""
    out = []
    rows = {r.day: r for r in upper_days or []}
    low = [r.day for r in roles if r.kind == "low"]
    if low:
        out.append(("低壓或熱帶系統", _dates(low), "部分成員出現很低的氣壓。單點資料看不出路徑,請看颱風路徑預報與中央氣象署的颱風消息。"))
    heavy = [fc for fc in forecast if fc.pop_heavy >= 0.1]
    if heavy:
        detail = "、".join(f"{label(fc.day)} {fc.pop_heavy:.0%}" for fc in heavy)
        out.append(("大雨", _dates([fc.day for fc in heavy]), f"日雨量達 50 mm 以上的機率:{detail}。山區與迎風面的雨量會比模式格點值大。"))
    else:
        wet = [fc for fc in forecast if fc.precip.p90 >= 20]
        if wet:
            most = max(wet, key=lambda fc: fc.precip.p90)
            out.append(("較大雨勢", _dates([fc.day for fc in wet]), f"偏多的情況下日雨量可達 {most.precip.p90:.0f} mm({label(most.day)});模式解析不了地形,山區與迎風面可能更多。"))
    thunder = [fc.day for fc in forecast if fc.day in rows and rows[fc.day].unstable and rows[fc.day].outlook in ("wet", "showery") and fc.pop >= 0.3]
    if thunder:
        out.append(("雷雨", _dates(thunder), "大氣不穩定(K 指數 30 以上或 CAPE 1000 J/kg 以上)又有舉升條件,降雨時可能伴隨雷擊與短時強降雨。"))
    windy = [fc for fc in forecast if (fc.gust and fc.gust.p90 >= 60) or (fc.wind and fc.wind.p50 >= 40)]
    if windy:
        peak = max(windy, key=lambda fc: fc.gust.p90 if fc.gust else 0)
        out.append(("強風", _dates([fc.day for fc in windy]), f"陣風偏強時可達 {peak.gust.p90:.0f} km/h({label(peak.day)});沿海與空曠地區風更大。" if peak.gust else "平均風速偏大。"))
    drops = [(a, b) for a, b in zip(forecast, forecast[1:]) if b.tmax.p50 - a.tmax.p50 <= -3]
    surges = [fc.day for fc in forecast if fc.day in rows and rows[fc.day].shift is not None and (rows[fc.day].shift.cooling or 0) >= 3]
    if drops or surges:
        days = sorted(set([b.day for _, b in drops] + surges))
        bits = [f"{label(b.day)} 高溫比前一天降 {a.tmax.p50 - b.tmax.p50:.0f}°C" for a, b in drops]
        bits += [f"{label(d)} 鋒面通過後低層一天內降溫 {rows[d].shift.cooling:.0f}°C" for d in surges]
        out.append(("降溫", _dates(days), ";".join(bits) + "。早晚出門留意保暖。"))
    hot = [fc for fc in forecast if fc.tmax.p50 >= 34]
    if hot:
        out.append(("高溫", _dates([fc.day for fc in hot]), f"高溫可達 {max(fc.tmax.p50 for fc in hot):.0f}°C,中午前後避免長時間曝曬。"))
    swing = [fc for fc in forecast if fc.tmax.p50 - fc.tmin.p50 >= 10]
    if swing:
        out.append(("日夜溫差大", _dates([fc.day for fc in swing]), f"日夜溫差可達 {max(fc.tmax.p50 - fc.tmin.p50 for fc in swing):.0f}°C。"))
    return out


def likelihood(share: float) -> str:
    """把成員比例換成定性的可能性。"""
    return "高" if share >= 0.75 else "中高" if share >= 0.6 else "中" if share >= 0.4 else "中低" if share >= 0.25 else "低"


@dataclass
class Scenario:
    day: str
    name: str  # A / B
    content: str
    condition: str  # 發生條件
    likelihood: str  # 高 / 中高 / 中 / 中低 / 低
    share: float


def scenarios(forecast: list[DayForecast], limit: int = 3) -> list[Scenario]:
    """替代情境:挑出最沒把握的幾天,各列出主要情境(A)與另一種可能(B)。

    可能性是成員比例換成的定性描述,不是官方機率。
    """
    def unsure(fc: DayForecast) -> bool:
        return fc.pop_gap >= 0.4 or 0.35 <= fc.pop <= 0.65 or fc.scenario is not None

    out = []
    for fc in sorted((fc for fc in forecast if unsure(fc)), key=lambda fc: fc.confidence)[:limit]:
        if fc.pop_gap >= 0.4 or 0.35 <= fc.pop <= 0.65:
            wet_side = [n for n, p in fc.system_pop.items() if p >= 0.5]
            dry_side = [n for n in fc.system_pop if n not in wet_side]
            who = lambda names: f"{'、'.join(names)} 的預報正確" if names and len(fc.system_pop) > 1 else "少數成員的情況發生"
            wet = (f"有雨,雨量約 {max(fc.precip.p50, 1):.0f}–{max(fc.precip.p90, 2):.0f} mm", who(wet_side), fc.pop)
            dry = ("沒有明顯降雨(不到 1 mm)", who(dry_side), 1 - fc.pop)
            first, second = (wet, dry) if fc.pop >= 0.5 else (dry, wet)
        else:
            s = fc.scenario
            cool = (f"高溫只有約 {s.low:.0f}°C", "雲多、降雨或冷空氣提早到達", s.low_share)
            warm = (f"高溫約 {s.high:.0f}°C", "雲量少或冷空氣延後", s.high_share)
            first, second = (cool, warm) if s.low_share >= s.high_share else (warm, cool)
        for name, (content, condition, share) in zip("AB", (first, second)):
            out.append(Scenario(fc.day, name, content, condition, likelihood(share), share))
    return sorted(out, key=lambda s: (s.day, s.name))


def watch_points(forecast: list[DayForecast], upper_days: list[UpperDay] | None, comparison: list[ModelRow]) -> list[str]:
    """接下來幾次更新該盯什麼。"""
    out = []
    split = [row.day for row in comparison if row.split]
    if split:
        out.append(f"{_dates(split)} 各模式的降雨機率差很多:看下一次更新時,它們是否往同一個方向靠攏(見「模式」分頁的預報趨勢)。")
    rows = upper_days or []
    passage = trough_passage(rows)
    if passage:
        first, second = passage
        when = f"{label(first)} 當天" if first == second else f"{label(first)} 到 {label(second)} 之間"
        out.append(f"500 hPa 槽線目前估計在 {when}通過;提早或延後,降雨的時段會跟著移動。")
    for r in rows:
        if r.shift is not None:
            out.append(f"低層鋒面目前估計在 {label(r.day)} {int(r.shift.start[11:13])}–{int(r.shift.end[11:13])} 時之間通過(誤差 6–12 小時);之後的降溫幅度取決於冷空氣有多厚。")
    highs = [r for r in rows if r.subtropical_high]
    if highs and len(highs) < len(rows):
        out.append(f"500 hPa 高度是否維持在 5880 m 以上({_dates([r.day for r in highs])} 在 588 線以內):這條線退開,天氣才會轉變。")
    late = [fc for fc in forecast if fc.lead >= 5 and fc.confidence < 50]
    if late:
        out.append(f"{_dates([fc.day for fc in late])} 的時效較遠、信心偏低,只看趨勢,不看細節。")
    return out


# --------------------------------------------------------------------------- #
# 整份簡報
# --------------------------------------------------------------------------- #


@dataclass
class Briefing:
    roles: list[DayRole]
    stages: list[Stage]
    headline: str  # 一句話講完這一週的分段
    lede: str  # 先講結論:什麼時候轉變、最要注意什麼、哪一天最沒把握
    current: list[tuple[str, str, str]]
    models: list[ModelRow]
    hazards: list[tuple[str, str, str]]
    scenarios: list[Scenario]
    watch: list[str]


def build(
    forecast: list[DayForecast],
    signals: list[Signal],
    patterns: list[DayPattern],
    upper_days: list[UpperDay] | None,
    latitude: float,
    longitude: float = 0.0,
) -> Briefing:
    southern = latitude < 0
    roles = day_roles(forecast, signals, upper_days, latitude, longitude)
    stage_list = stages(forecast, roles, upper_days, southern)
    models = model_comparison(forecast)
    headline = "這一週" + ("分成 " + str(len(stage_list)) + " 段:" if len(stage_list) > 1 else ":") + ";".join(
        f"{s.span} {s.headline}" for s in stage_list
    ) + "。"
    hazard_list = hazards(forecast, roles, upper_days)
    if len(stage_list) > 1:
        lede = f"這一週分成 {len(stage_list)} 段,天氣在 {'、'.join(label(s.days[0]) for s in stage_list[1:])} 轉變。"
    else:
        lede = f"這一週天氣型態變化不大:{stage_list[0].headline}。"
    if hazard_list:
        lede += "要注意 " + "、".join(f"{when} 的{name}" for name, when, _ in hazard_list[:3]) + "。"
    weakest = min(forecast, key=lambda fc: fc.confidence)
    if weakest.confidence < 65:
        lede += f"最沒把握的是 {label(weakest.day)}。"
    return Briefing(
        roles=roles,
        stages=stage_list,
        headline=headline,
        lede=lede,
        current=current_analysis((upper_days or [None])[0], patterns, southern),
        models=models,
        hazards=hazard_list,
        scenarios=scenarios(forecast),
        watch=watch_points(forecast, upper_days, models),
    )
