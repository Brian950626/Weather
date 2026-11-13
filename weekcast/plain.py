"""白話總結:把各層的分析翻成一般人看得懂的話,並說明對生活的影響。

前面的表格是給想看細節的人;這裡回答三個問題——天氣怎樣、為什麼、
我該怎麼準備——而且不使用「槽」、「輻散」、「平流」這類術語(術語只在
名詞對照裡出現,方便想往回查表格的人)。

幾個原則:

- 門檻和表格上的天氣用語一致(見 analysis.describe_sky):降雨機率 70% 以上
  是「有雨」、40–70% 是「可能有雨」、20–40% 是「偶有陣雨」,讀者不會在同一頁
  看到兩種說法。
- 「為什麼」先講和結果一致的原因,再講相反的因素,並說明為什麼後者沒有
  占上風。高空的訊號常常互相拉扯,只把它們並列,讀起來就是自相矛盾。
- 每一天都要有「怎麼準備」,不下雨也要說一聲不用帶傘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .analysis import DayForecast
from .periods import PeriodForecast, by_day
from .upper import UpperDay

WET, MAYBE, SHOWERY = 0.7, 0.4, 0.2  # 與 analysis.describe_sky 的門檻相同

GLOSSARY = (
    ("高空槽(槽線)", "高空的低壓帶。它靠近時,前方的空氣被迫上升,容易成雲下雨;通過之後轉為下沉,天氣好轉。"),
    ("高空脊、副熱帶高壓", "高空的高壓帶。空氣下沉、雲長不起來,天氣穩定,夏天常伴隨悶熱。"),
    ("鋒面", "冷空氣和暖空氣的交界。它通過時常有一陣雨,之後風向改變、氣溫下降。"),
    ("東北季風", "秋冬從北方吹來的涼空氣。迎風的北部和東部多雲有雨,背風的中南部通常晴朗。"),
    ("噴流", "約一萬公尺高空的強風帶,時速可超過 200 公里。天氣系統沿著它移動,它靠近時天氣變化快。"),
    ("高層輻散", "高空的空氣向外流出,像抽風機一樣把下面的空氣往上抽,幫助雲雨發展。相反的「輻合」則會壓抑雲雨。"),
    ("暖平流、冷平流", "風把較暖或較冷的空氣吹過來。暖空氣來會升溫、也容易被抬升成雲;冷空氣來會降溫、天氣轉乾。"),
    ("中層濕度", "約三千公尺高的空氣有多潮濕。夠濕,雲才能長高、雨才下得久;太乾,就只有低雲和毛毛雨。"),
    ("K 指數", "衡量大氣容不容易產生雷雨的數字,30 以上表示容易有雷陣雨。"),
    ("系集、成員", "同一個模式用稍微不同的起始條件算幾十次,每一次叫一個成員。大家算出來差不多,預報就可信;差很多,就沒把握。"),
)

# 表格與時間軸上的專業用語 → 一般人看得懂的說法
SYSTEMS = {
    "副熱帶高壓籠罩": "高壓籠罩,天氣穩定、偏暖",
    "高壓脊籠罩": "高壓籠罩,天氣穩定",
    "高空槽接近": "高空有低壓帶靠近,天氣轉不穩定",
    "低壓或鋒面接近": "有低壓或冷暖空氣的交界靠近,天氣轉壞",
    "低壓或熱帶系統接近": "有低壓或颱風類的系統靠近",
    "鋒面通過": "冷暖空氣的交界通過,有雨、之後轉涼",
    "東北季風影響": "東北風帶來涼空氣,迎風面多雲有雨",
    "東北季風增強": "東北風增強,變涼、迎風面有雨",
    "鋒後冷空氣影響": "冷空氣影響,偏涼",
    "冷空氣南下": "冷空氣南下,變涼",
    "冷空氣北上": "冷空氣北上,變涼",
    "偏南風回暖": "吹南風,氣溫回升",
    "偏北風回暖": "吹北風,氣溫回升",
}

# 給「這些數字怎麼看」用的說明,和程式的門檻一致
HOW_TO_READ = (
    ("降雨機率", "70% 以上寫「有雨」,40–70% 是「可能有雨」,20–40% 是「偶有陣雨」,20% 以下不太會下。機率高不代表雨大,要一起看雨量。"),
    ("雨量(毫米)", "一整天不到 1 毫米幾乎沒感覺;1–5 毫米是地面會濕的小雨;5–15 毫米需要撐傘;15–40 毫米雨勢明顯;40 毫米以上是大雨。"),
    ("括號裡的範圍", "例如「28(27–29)」:最可能是 28 度,十次裡約有八次會落在 27 到 29 度之間。範圍越寬,越沒把握。"),
    ("信心度", "看各個模式的意見是否一致。格子越多越一致;它不是命中率,信心高也可能報錯。"),
    ("風速(公里/小時)", "12 以下是微風;12–28 樹葉會搖、感覺得到風;29–49 風偏大,撐傘吃力;50 以上是強風,騎車要小心。"),
    ("顏色", "綠色代表降雨,紅色是高溫,藍色是低溫。"),
)


def _label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.month}/{d.day}({'一二三四五六日'[d.weekday()]})"


def system_words(headline: str) -> str:
    """把「高空槽接近 → 鋒面通過、東北季風增強」這類標題逐段翻成白話。"""
    steps = []
    for step in headline.split("→"):
        words = [SYSTEMS.get(part.strip(), part.strip()) for part in step.split("、")]
        steps.append(";".join(words))
    return ",接著".join(steps)


def rain_words(mm: float) -> str:
    if mm < 1:
        return "幾乎沒有雨"
    if mm < 5:
        return "地面會濕的小雨"
    if mm < 15:
        return "需要撐傘的雨"
    if mm < 40:
        return "雨勢明顯"
    return "大雨"


def wind_words(kmh: float) -> str:
    if kmh < 12:
        return "微風"
    if kmh < 29:
        return "有風"
    if kmh < 50:
        return "風偏大"
    return "強風"


# --------------------------------------------------------------------------- #
# 每一天
# --------------------------------------------------------------------------- #


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


def _kind(fc: DayForecast) -> str:
    return "wet" if fc.pop >= WET else "maybe" if fc.pop >= MAYBE else "dry"


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
    amount = f"{rain_words(fc.precip.p50)}(約 {fc.precip.p50:.0f} 毫米)" if fc.precip.p50 >= 1 else "雨量很少"
    if fc.pop >= WET:
        rain = f"會下雨(機率 {fc.pop:.0%}),多半是{amount}"
        if fc.precip.p90 >= 30:
            rain += f",雨多的話可能到 {fc.precip.p90:.0f} 毫米"
    elif fc.pop >= MAYBE:
        rain = f"可能下雨(機率 {fc.pop:.0%}),有下的話多半是{amount}"
    elif fc.pop >= SHOWERY:
        rain = f"大致不會下雨,但可能有短暫陣雨(機率 {fc.pop:.0%})"
    else:
        cover = fc.cloud.p50 if fc.cloud else 50
        rain = "天氣晴朗" if cover < 40 else "多雲,不會下雨" if cover < 85 else "陰天,但不會下雨"
    when = _when(cells) if fc.pop >= SHOWERY else ""
    change = ""
    if previous is not None:
        delta = fc.tmax.p50 - previous.tmax.p50
        if delta <= -3:
            change = f",比前一天涼 {abs(delta):.0f} 度"
        elif delta >= 3:
            change = f",比前一天暖 {delta:.0f} 度"
    return f"{rain}{',' + when if when else ''};{temps}{change}。"


def _factors(up: UpperDay) -> tuple[list[str], list[str]]:
    """高空的因素分成兩邊:幫助下雨的、壓抑下雨的。"""
    helps, blocks = [], []
    if up.regime == "槽線過境":
        helps.append("高空的低壓帶正好通過上空")
    elif up.regime == "槽前" and up.trough is not None and up.trough >= -15:
        helps.append("高空有一道低壓帶從西邊靠近,它前方的空氣會上升")
    elif up.regime == "槽後":
        blocks.append("高空的低壓帶已經通過,後方的空氣下沉")
    elif up.regime == "脊線上":
        blocks.append("高空是高壓,空氣下沉")
    if up.subtropical_high and up.regime != "脊線上":
        blocks.append("上空有副熱帶高壓,空氣下沉")
    if up.div250 is not None and up.div250 >= 0.3:
        helps.append("更高處的強風正把空氣往外抽,帶動下方空氣上升")
    elif up.div250 is not None and up.div250 <= -0.3:
        blocks.append("更高處的空氣在堆積,往下壓")
    if up.tadv850 is not None and up.tadv850 >= 1.5:
        helps.append("低空有較暖的空氣移入")
    if up.moisture == "深厚":
        helps.append("從低空到高空都很潮濕")
    elif up.moisture == "乾燥":
        blocks.append("空氣偏乾")
    if up.wind925 is not None and up.rh850 is not None and up.wind925 >= 8 and up.rh850 >= 80:
        helps.append("低空風強又潮濕,迎風的山坡和海岸容易有雨")
    return helps, blocks


def _why(up: UpperDay | None, fc: DayForecast, previous_up: UpperDay | None = None) -> str:
    if up is None:
        return ""
    helps, blocks = _factors(up)
    shallow = up.moisture == "淺層"
    kind = _kind(fc)
    join = lambda items: ";".join(items)
    if kind == "wet":
        text = f"會下雨,是因為{join(helps)}。" if helps else "低空有足夠的水氣成雲下雨。"
        limits = list(blocks)
        if shallow:
            limits.append("只有低空潮濕、高處乾燥,雲長不高")
        if limits:
            text += f"不過{join(limits)},所以雨不會太大,也不容易有雷雨。"
    elif kind == "maybe":
        if helps and (blocks or shallow):
            limits = blocks + (["只有低空潮濕、高處乾燥,雲長不高"] if shallow else [])
            text = f"有機會下雨,因為{join(helps)};但{join(limits)},所以不一定下得起來。"
        elif helps:
            text = f"有機會下雨,因為{join(helps)}。"
        else:
            text = "低空有一些水氣,可能飄點雨" + (f";但{join(blocks)},雨下不大。" if blocks else "。")
    else:
        result = "頂多有短暫陣雨" if fc.pop >= SHOWERY else "不太會下雨"
        if blocks:
            text = f"{'雨下不太起來' if fc.pop >= SHOWERY else '不太會下雨'},因為{join(blocks)},雲不容易發展。"
            if helps:
                text += f"雖然{join(helps)},但還不足以成雨。"
        elif helps:
            reason = "水氣只在低空、雲長不高" if shallow else "上升的力量不夠強"
            text = f"雖然{join(helps)},但{reason},所以{result}。"
        else:
            text = "高空沒有明顯的天氣系統,天氣平穩。"
    if up.k is not None and up.k >= 30 and fc.pop >= MAYBE:
        text += "大氣不穩定,下雨時可能伴隨打雷。"
    if previous_up is not None and up.t925 is not None and previous_up.t925 is not None and up.t925 - previous_up.t925 <= -3:
        text += "靠近地面有一波冷空氣移入,所以氣溫下降。"
    elif up.tadv850 is not None and up.tadv850 <= -1.5:
        text += "低空有較冷的空氣移入。"
    return text


def _advice(fc: DayForecast, previous: DayForecast | None, up: UpperDay | None) -> list[str]:
    out = []
    if fc.pop >= WET:
        out.append("出門帶傘")
        if fc.precip.p90 >= 30:
            out.append("留意較大雨勢,低窪處小心積水")
    elif fc.pop >= MAYBE:
        out.append("帶把傘備用")
    elif fc.pop >= SHOWERY:
        out.append("包包放把摺疊傘就夠了")
    else:
        out.append("不用帶傘")
    if up is not None and up.k is not None and up.k >= 30 and fc.pop >= MAYBE:
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
    if fc.pop < SHOWERY and (fc.cloud is None or fc.cloud.p50 < 75):
        out.append("適合戶外活動和曬衣服")
    return out


def _unsure(fc: DayForecast) -> bool:
    return fc.confidence_label in ("低", "中低")


def _caution(fc: DayForecast) -> str:
    if _unsure(fc):
        return "(各模式對這天的看法不一致,預報可能再變,請之後再確認。)"
    if fc.lead >= 5:
        return "(距離現在較遠,細節還會調整。)"
    return ""


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


# --------------------------------------------------------------------------- #
# 整週
# --------------------------------------------------------------------------- #


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


def _span(days: list[DayForecast]) -> str:
    return _label(days[0].day) if len(days) == 1 else f"{_label(days[0].day)}到{_label(days[-1].day)}"


def _spans(days: list[DayForecast]) -> str:
    """把不一定連續的日子寫成「11/14(六)到11/15(日)、11/18(三)」。"""
    runs: list[list[DayForecast]] = []
    for fc in days:
        if runs and fc.lead == runs[-1][-1].lead + 1:
            runs[-1].append(fc)
        else:
            runs.append([fc])
    return "、".join(_span(run) for run in runs)


def _temps(days: list[DayForecast]) -> str:
    low, high = min(f.tmax.p50 for f in days), max(f.tmax.p50 for f in days)
    return f"白天約 {high:.0f} 度" if round(high) == round(low) else f"白天約 {low:.0f}–{high:.0f} 度"


def _segment(days: list[DayForecast], previous: list[DayForecast] | None) -> str:
    kind = _kind(days[0])
    if kind == "wet":
        heavy = any(f.precip.p90 >= 30 for f in days)
        light = all(f.precip.p50 < 5 for f in days)
        what = "會下雨" + (",雨勢可能不小" if heavy else ",但雨量不大" if light else "")
    elif kind == "maybe":
        what = "雲多、可能下雨"
    else:
        showery = any(f.pop >= SHOWERY for f in days)
        cover = [f.cloud.p50 for f in days if f.cloud]
        sky = "多雲到晴" if cover and sum(cover) / len(cover) < 70 else "雲量偏多"
        what = f"天氣穩定、{sky}" + (",偶爾有短暫陣雨" if showery else ",不太會下雨")
    change = ""
    if previous:
        delta = days[0].tmax.p50 - previous[-1].tmax.p50
        if delta <= -3:
            change = ",而且明顯變涼"
        elif delta >= 3:
            change = ",氣溫回升"
    lead = "接著 " if previous else ""
    return f"{lead}{_span(days)}{what}{change},{_temps(days)}。"


def _headline(forecast: list[DayForecast]) -> str:
    wet = [f for f in forecast if _kind(f) == "wet"]
    unsettled = [f for f in forecast if _kind(f) != "dry"]
    dry = [f for f in forecast if _kind(f) == "dry"]
    if not unsettled:
        return "未來一週天氣穩定,幾乎不會下雨。"
    if len(wet) == len(forecast):
        return "未來一週幾乎天天都會下雨。"
    # 把一週看成「穩定」和「不穩定」交替的幾段;只有兩段時才說前好後壞
    blocks = []
    for fc in forecast:
        steady = _kind(fc) == "dry"
        if not blocks or blocks[-1] != steady:
            blocks.append(steady)
    if blocks == [True, False]:
        return f"未來一週前好後壞:前面幾天穩定,{_label(unsettled[0].day)}起轉為有雨。"
    if blocks == [False, True]:
        return f"未來一週前壞後好:前面幾天有雨,{_label(dry[0].day)}起轉為穩定。"
    if not dry:
        return f"未來一週天氣都不穩定,{_spans(wet)}最容易下雨。" if wet else "未來一週天氣都不太穩定,每天都可能下雨。"
    if len(dry) >= len(unsettled):
        return f"未來一週大致穩定,只有{_spans(unsettled)}{'會' if wet else '可能'}下雨。"
    return f"未來一週多雨,只有{_spans(dry)}比較穩定。"


def week_summary(forecast: list[DayForecast], upper_days: list[UpperDay] | None = None) -> WeekSummary | None:
    if not forecast:
        return None
    wet = [f for f in forecast if _kind(f) == "wet"]

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
    heavy = [f for f in forecast if f.pop >= MAYBE and f.precip.p90 >= 30]
    if heavy:
        watch.append(_spans(heavy) + "雨勢可能較大")
    cooling = [b for a, b in zip(forecast, forecast[1:]) if b.tmax.p50 - a.tmax.p50 <= -3]
    if cooling:
        watch.append(_spans(cooling) + "明顯變涼,記得加衣服")
    hot = [f for f in forecast if f.tmax.p50 >= 33]
    if hot:
        watch.append(_spans(hot) + "很熱,注意防曬補水")
    windy = [f for f in forecast if (f.gust and f.gust.p90 >= 55) or (f.wind and f.wind.p50 >= 35)]
    if windy:
        watch.append(_spans(windy) + "風大")
    unsure = [f for f in forecast if _unsure(f)]
    if unsure:
        watch.append(_spans(unsure) + "的預報還不確定,出門前再看一次")
    return WeekSummary(_headline(forecast), story, cause, watch)


def overview(forecast: list[DayForecast], upper_days: list[UpperDay] | None) -> str:
    """整週的總結,一段文字。"""
    summary = week_summary(forecast, upper_days)
    return summary.text() if summary else ""
