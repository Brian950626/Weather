"""白話總結:把各層的分析翻成一般人看得懂的話,並說明對生活的影響。

前面的表格是給想看細節的人;這裡回答三個問題——這天天氣怎樣、為什麼、
我該怎麼準備——而且不使用「槽」、「輻散」、「平流」這類術語(術語只在
最後的名詞對照裡出現一次,方便想往回查表格的人)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .analysis import DayForecast
from .periods import PeriodForecast, by_day
from .upper import UpperDay

GLOSSARY = (
    ("高空槽(槽線)", "高空的低壓帶。它靠近時,前方的空氣被迫上升,容易成雲下雨;通過之後轉為下沉,天氣好轉。"),
    ("高空脊、副熱帶高壓", "高空的高壓帶。空氣下沉、雲長不起來,天氣穩定,夏天常伴隨悶熱。"),
    ("噴流", "約一萬公尺高空的強風帶,時速可超過 200 公里。天氣系統沿著它移動,它靠近時天氣變化快。"),
    ("高層輻散", "高空的空氣向外流出,像抽風機一樣把下面的空氣往上抽,幫助雲雨發展。相反的「輻合」則會壓抑雲雨。"),
    ("暖平流、冷平流", "風把較暖或較冷的空氣吹過來。暖空氣來會升溫、也容易被抬升成雲;冷空氣來會降溫、天氣轉乾。"),
    ("中層濕度", "約三千公尺高的空氣有多潮濕。夠濕,雲才能長高、雨才下得久;太乾,就只有低雲和毛毛雨。"),
    ("K 指數", "衡量大氣容不容易產生雷雨的數字,30 以上表示容易有雷陣雨。"),
)


def _label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.month}/{d.day}({'一二三四五六日'[d.weekday()]})"


@dataclass
class PlainDay:
    day: str
    weather: str  # 這天天氣怎樣
    why: str  # 為什麼(高空在做什麼)
    advice: list[str] = field(default_factory=list)  # 該怎麼準備
    caution: str = ""  # 預報有多可靠

    def text(self) -> str:
        parts = [f"{_label(self.day)}:{self.weather}"]
        if self.why:
            parts.append(f"原因:{self.why}")
        if self.advice:
            parts.append("建議:" + "、".join(self.advice) + "。")
        if self.caution:
            parts.append(self.caution)
        return "".join(parts)


def _when(cells: dict[str, PeriodForecast] | None) -> str:
    """雨主要落在一天的哪個時段。"""
    if not cells:
        return ""
    rated = [(c.pop, name) for name, c in cells.items() if c.pop is not None]
    if not rated:
        return ""
    wet = [name for pop, name in rated if pop >= 0.5]
    if len(wet) >= 3:
        return "幾乎整天都可能下"
    if wet:
        return "主要在" + "和".join(wet)
    top = max(rated)
    return f"比較可能在{top[1]}" if top[0] >= 0.3 else ""


def _weather(fc: DayForecast, previous: DayForecast | None, cells) -> str:
    temps = f"白天約 {fc.tmax.p50:.0f} 度、清晨約 {fc.tmin.p50:.0f} 度"
    if fc.pop >= 0.6:
        size = "雨勢可能不小" if fc.precip.p90 >= 30 else "雨量不大" if fc.precip.p50 < 5 else "有明顯雨勢"
        rain = f"容易下雨(機率 {fc.pop:.0%}),{size}"
    elif fc.pop >= 0.3:
        rain = f"可能會下雨(機率 {fc.pop:.0%})"
    else:
        cover = fc.cloud.p50 if fc.cloud else 50
        rain = "天氣晴朗" if cover < 40 else "多雲但不太會下雨" if cover < 85 else "陰天但不太會下雨"
    when = _when(cells) if fc.pop >= 0.3 else ""
    change = ""
    if previous is not None:
        delta = fc.tmax.p50 - previous.tmax.p50
        if delta <= -3:
            change = f",比前一天涼 {abs(delta):.0f} 度"
        elif delta >= 3:
            change = f",比前一天暖 {delta:.0f} 度"
    return f"{rain}{',' + when if when else ''};{temps}{change}。"


def _why(up: UpperDay | None, fc: DayForecast, previous_up: UpperDay | None = None) -> str:
    if up is None:
        return ""
    reasons = []
    rainy = fc.pop >= 0.6
    if up.regime == "槽線過境":
        reasons.append("高空的低壓帶正好通過上空,空氣容易上升成雲")
    elif up.regime == "槽前" and up.trough is not None and up.trough >= -15:
        reasons.append("高空有一道低壓帶從西邊靠近,它前方的空氣會上升,有利雲雨發展")
    elif up.regime == "槽後":
        reasons.append("高空的低壓帶已經通過,後方的空氣下沉,天氣趨於穩定")
    elif up.regime == "脊線上":
        reasons.append("高空是高壓,空氣下沉、雲不容易發展")
    elif up.regime == "東風帶":
        reasons.append("位在熱帶的東風帶,天氣主要看水氣多寡與午後對流")
    if up.subtropical_high and up.regime != "脊線上":
        reasons.append("副熱帶高壓籠罩,天氣偏穩定")
    if up.div250 is not None and up.div250 >= 0.3:
        reasons.append("更高處的強風正把空氣往外抽,幫助下方空氣上升")
    elif up.div250 is not None and up.div250 <= -0.3:
        reasons.append("更高處的空氣在堆積,壓抑雲的發展")
    if up.tadv850 is not None and up.tadv850 >= 1.5:
        reasons.append("低空有較暖的空氣移入")
    elif up.tadv850 is not None and up.tadv850 <= -1.5:
        reasons.append("低空有較冷的空氣移入")

    if up.moisture == "深厚":
        reasons.append("從低空到高空都很潮濕,雲能長得高、雨可以下得久")
    elif up.moisture == "淺層":
        reasons.append("只有低空潮濕、高處乾燥,雲長不高,所以就算下雨也多是短暫雨或毛毛雨")
    elif up.moisture == "乾燥":
        reasons.append("空氣偏乾,不容易成雲")
    if up.k is not None and up.k >= 30:
        reasons.append("大氣不穩定,可能出現雷陣雨")
    if up.wind925 is not None and up.rh850 is not None and up.wind925 >= 8 and up.rh850 >= 80:
        reasons.append("低空風強又潮濕,迎風的山坡和海岸容易有雨")
    if previous_up is not None and up.t925 is not None and previous_up.t925 is not None and up.t925 - previous_up.t925 <= -3:
        reasons.append("靠近地面有一波冷空氣移入,所以氣溫下降")
    stable_aloft = up.subtropical_high or up.regime in ("脊線上", "槽後")
    if rainy and stable_aloft:
        reasons.append("要注意的是高空其實是穩定的,所以這天的雨以持續性的降雨為主,不容易出現劇烈雷雨")
    return ";".join(reasons) + "。" if reasons else "高空沒有明顯的天氣系統。"


def _advice(fc: DayForecast, previous: DayForecast | None, up: UpperDay | None) -> list[str]:
    out = []
    if fc.pop >= 0.6:
        out.append("出門帶傘")
        if fc.precip.p90 >= 30:
            out.append("留意較大雨勢,低窪處小心積水")
    elif fc.pop >= 0.3:
        out.append("帶把傘備用")
    if up is not None and up.k is not None and up.k >= 30 and fc.pop >= 0.3:
        out.append("打雷時避免待在空曠處")
    if previous is not None and fc.tmax.p50 - previous.tmax.p50 <= -3:
        out.append("明顯變涼,加件外套")
    if fc.tmin.p50 < 14:
        out.append("早晚偏冷,注意保暖")
    if fc.tmax.p50 >= 33:
        out.append("天氣炎熱,注意防曬和補充水分")
    elif fc.tmax.p50 - fc.tmin.p50 >= 9:
        out.append("日夜溫差大,採洋蔥式穿法")
    if (fc.gust and fc.gust.p90 >= 55) or (fc.wind and fc.wind.p50 >= 35):
        out.append("風大,騎車和海邊活動要小心")
    if fc.pop < 0.2 and (fc.cloud is None or fc.cloud.p50 < 75):
        out.append("適合戶外活動和曬衣服")
    return out


def _caution(fc: DayForecast) -> str:
    if fc.confidence_label == "低":
        return "(各模式對這天的看法不一致,預報可能再變,請之後再確認。)"
    if fc.lead >= 5:
        return "(距離現在較遠,細節還會調整。)"
    return ""


@dataclass
class WeekSummary:
    """整週的總結:先講結論,再講經過、原因與要注意的事。"""

    headline: str  # 一句話
    story: list[str]  # 依時間順序,一段天氣一句
    cause: str  # 為什麼(白話)
    watch: list[str]  # 最需要注意的事

    def text(self) -> str:
        parts = [self.headline, "".join(self.story)]
        if self.cause:
            parts.append(self.cause)
        if self.watch:
            parts.append("要注意:" + ";".join(self.watch) + "。")
        return "".join(parts)


def _kind(fc: DayForecast) -> str:
    return "wet" if fc.pop >= 0.6 else "maybe" if fc.pop >= 0.3 else "dry"


def _span(days: list[DayForecast]) -> str:
    return _label(days[0].day) if len(days) == 1 else f"{_label(days[0].day)}到{_label(days[-1].day)}"


def _temps(days: list[DayForecast]) -> str:
    low, high = min(f.tmax.p50 for f in days), max(f.tmax.p50 for f in days)
    return f"白天約 {high:.0f} 度" if round(high) == round(low) else f"白天約 {low:.0f}–{high:.0f} 度"


def _segment(days: list[DayForecast], previous: list[DayForecast] | None) -> str:
    kind = _kind(days[0])
    if kind == "wet":
        heavy = any(f.precip.p90 >= 30 for f in days)
        light = all(f.precip.p50 < 5 for f in days)
        what = "容易下雨" + (",雨勢可能不小" if heavy else ",但雨量不大" if light else "")
    elif kind == "maybe":
        what = "雲多、可能下雨,不一定每個地方都下"
    else:
        cover = [f.cloud.p50 for f in days if f.cloud]
        what = "天氣穩定、" + ("多雲到晴" if cover and sum(cover) / len(cover) < 70 else "雲量偏多但不太會下雨")
    change = ""
    if previous:
        delta = days[0].tmax.p50 - previous[-1].tmax.p50
        if delta <= -3:
            change = ",而且明顯變涼"
        elif delta >= 3:
            change = ",氣溫回升"
    lead = "接著 " if previous else ""
    return f"{lead}{_span(days)}{what}{change},{_temps(days)}。"


def week_summary(forecast: list[DayForecast], upper_days: list[UpperDay] | None = None) -> WeekSummary | None:
    if not forecast:
        return None
    wet = [f for f in forecast if f.pop >= 0.6]
    dry = [f for f in forecast if f.pop < 0.3]
    if not wet and len(dry) == len(forecast):
        headline = "未來一週天氣穩定,幾乎不會下雨。"
    elif len(wet) == len(forecast):
        headline = "未來一週幾乎天天都容易下雨。"
    elif not wet:
        headline = "未來一週沒有明顯的降雨,但有幾天雲多、可能飄點雨。"
    elif _kind(forecast[0]) == "dry" and _kind(forecast[-1]) != "dry":
        turn = next(f for f in forecast if _kind(f) != "dry")
        headline = f"未來一週前好後壞:前面幾天穩定,{_label(turn.day)}起轉為有雨。"
    elif _kind(forecast[0]) != "dry" and _kind(forecast[-1]) == "dry":
        headline = "未來一週前壞後好:前面幾天有雨,之後轉為穩定。"
    else:
        headline = f"未來一週有 {len(wet)} 天容易下雨、{len(dry)} 天穩定,好壞天氣交替。"

    groups: list[list[DayForecast]] = []
    for fc in forecast:
        if groups and _kind(groups[-1][-1]) == _kind(fc):
            groups[-1].append(fc)
        else:
            groups.append([fc])
    story = [_segment(g, groups[i - 1] if i else None) for i, g in enumerate(groups)]

    cause = ""
    if upper_days:
        approaching = [u for u in upper_days if u.lift and u.regime in ("槽前", "槽線過境")]
        stable = [u for u in upper_days if u.outlook == "stable"]
        shallow = [u for u in upper_days if u.outlook == "shallow"]
        cold = [b for a, b in zip(upper_days, upper_days[1:]) if a.t925 is not None and b.t925 is not None and b.t925 - a.t925 <= -3]
        notes = []
        if approaching:
            notes.append(f"{_label(approaching[0].day)}前後有一道高空的低壓帶接近,是天氣轉壞的主因")
        if cold:
            notes.append(f"{_label(cold[0].day)}有一波冷空氣從近地面移入,所以會變涼")
        if shallow and wet:
            notes.append("下雨的日子多半是低雲帶來的小雨,不是大範圍的強降雨")
        if stable and not approaching:
            notes.append("高空以高壓為主,所以天氣穩定")
        if notes:
            cause = "原因:" + ";".join(notes) + "。"

    watch = []
    heavy = [f for f in forecast if f.pop >= 0.6 and f.precip.p90 >= 30]
    if heavy:
        watch.append("、".join(_label(f.day) for f in heavy) + "雨勢可能較大")
    cooling = [b for a, b in zip(forecast, forecast[1:]) if b.tmax.p50 - a.tmax.p50 <= -3]
    if cooling:
        watch.append("、".join(_label(f.day) for f in cooling) + "明顯變涼,記得加衣服")
    hot = [f for f in forecast if f.tmax.p50 >= 33]
    if hot:
        watch.append("、".join(_label(f.day) for f in hot) + "很熱,注意防曬補水")
    windy = [f for f in forecast if (f.gust and f.gust.p90 >= 55) or (f.wind and f.wind.p50 >= 35)]
    if windy:
        watch.append("、".join(_label(f.day) for f in windy) + "風大")
    unsure = [f for f in forecast if f.confidence_label == "低"]
    if unsure:
        watch.append("、".join(_label(f.day) for f in unsure) + "的預報還不確定,出門前再看一次")
    return WeekSummary(headline, story, cause, watch)


def overview(forecast: list[DayForecast], upper_days: list[UpperDay] | None) -> str:
    """整週的總結,一段文字。"""
    summary = week_summary(forecast, upper_days)
    return summary.text() if summary else ""


def plain_days(
    forecast: list[DayForecast],
    upper_days: list[UpperDay] | None = None,
    period_list: list[PeriodForecast] | None = None,
    southern: bool = False,
) -> list[PlainDay]:
    uppers = {u.day: u for u in upper_days or []}
    cells = by_day(period_list) if period_list else {}
    out, previous, previous_up = [], None, None
    for fc in forecast:
        up = uppers.get(fc.day)
        out.append(
            PlainDay(
                day=fc.day,
                weather=_weather(fc, previous, cells.get(fc.day)),
                why=_why(up, fc, previous_up),
                advice=_advice(fc, previous, up),
                caution=_caution(fc),
            )
        )
        previous, previous_up = fc, up
    return out
