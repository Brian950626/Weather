"""把分析結果排版成文字報表或 JSON。"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict
from datetime import date

from .analysis import DayForecast, Ensemble, Signal
from .history import Comparison, Trend
from .periods import PERIODS, PeriodForecast, by_day
from .upper import PWAT_RICH, UpperDay, WindShift, compass, trough_passage


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    gap = " " * max(0, width - _width(text))
    return gap + text if right else text + gap


def _label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.month}/{d.day}({'一二三四五六日'[d.weekday()]})"


def _range(spread, unit: str = "") -> str:
    return f"{spread.p50:.0f}{unit} ({spread.p10:.0f}–{spread.p90:.0f})"


def _table(forecast: list[DayForecast]) -> list[str]:
    head = ["日期", "天氣", "高溫°C (10–90%)", "低溫°C (10–90%)", "降雨機率", "雨量mm 中位/偏多", "最大風 km/h", "信心"]
    rows = [head]
    for fc in forecast:
        wind = "–"
        if fc.wind:
            wind = f"{fc.wind.p50:.0f}"
            if fc.gust:
                wind += f"(陣風 {fc.gust.p50:.0f})"
        rows.append(
            [
                _label(fc.day),
                fc.sky,
                _range(fc.tmax),
                _range(fc.tmin),
                f"{fc.pop:.0%}",
                f"{fc.precip.p50:.0f} / {fc.precip.p90:.0f}",
                wind,
                f"{fc.confidence_label} {fc.confidence}",
            ]
        )
    widths = [max(_width(r[i]) for r in rows) for i in range(len(head))]
    lines = ["  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]
    lines.insert(1, "  ".join("─" * w for w in widths))
    return lines


def _runs(days: list[DayForecast]) -> str:
    """把連續的日期合併成「11/14 六–11/16 一」這樣的區間。"""
    if not days:
        return ""
    groups = [[days[0]]]
    for fc in days[1:]:
        if fc.lead == groups[-1][-1].lead + 1:
            groups[-1].append(fc)
        else:
            groups.append([fc])
    return "、".join(_label(g[0].day) if len(g) == 1 else f"{_label(g[0].day)}–{_label(g[-1].day)}" for g in groups)


def highlights(forecast: list[DayForecast]) -> list[str]:
    if not forecast:
        return []
    out = []
    warm = max(forecast, key=lambda f: f.tmax.p50)
    cool = min(forecast, key=lambda f: f.tmax.p50)
    line = f"氣溫:白天高溫 {cool.tmax.p50:.0f}–{warm.tmax.p50:.0f}°C,最暖 {_label(warm.day)}、最涼 {_label(cool.day)}"
    night = min(forecast, key=lambda f: f.tmin.p50)
    line += f";最低溫出現在 {_label(night.day)} 約 {night.tmin.p50:.0f}°C。"
    out.append(line)

    swings = [(b.tmax.p50 - a.tmax.p50, b) for a, b in zip(forecast, forecast[1:])]
    drops = [f"{_label(fc.day)} 降 {abs(delta):.0f}°C" for delta, fc in swings if delta <= -3]
    rises = [f"{_label(fc.day)} 升 {delta:.0f}°C" for delta, fc in swings if delta >= 3]
    if drops or rises:
        out.append("明顯變化:" + "、".join(drops + rises) + "(與前一天相比)。")

    wet = [f for f in forecast if f.pop >= 0.6]
    maybe = [f for f in forecast if 0.3 <= f.pop < 0.6]
    dry = [f for f in forecast if f.pop < 0.3]
    parts = []
    if wet:
        parts.append(f"{_runs(wet)} 降雨機率高")
    if maybe:
        parts.append(f"{_runs(maybe)} 可能有雨")
    if dry:
        parts.append(f"{_runs(dry)} 大致無雨")
    out.append("降雨:" + ";".join(parts) + "。")

    heavy = [f for f in forecast if f.pop_heavy >= 0.1]
    if heavy:
        detail = "、".join(f"{_label(f.day)}({f.pop_heavy:.0%})" for f in heavy)
        out.append(f"大雨風險:日雨量達 50 mm 以上的機率 {detail}。")

    windy = [f for f in forecast if (f.gust and f.gust.p90 >= 60) or (f.wind and f.wind.p50 >= 40)]
    if windy:
        out.append(f"強風:{_runs(windy)} 風勢較大。")
    return out


def uncertainty(forecast: list[DayForecast]) -> list[str]:
    out = []
    for fc in forecast:
        if fc.confidence >= 70:
            continue
        notes = []
        if fc.scenario:
            s = fc.scenario
            notes.append(f"成員分成兩群:{s.low_share:.0%} 高溫約 {s.low:.0f}°C、{s.high_share:.0%} 約 {s.high:.0f}°C")
        if fc.tmax_gap >= 2 and len(fc.system_tmax) > 1:
            parts = " / ".join(f"{n} {v:.0f}" for n, v in sorted(fc.system_tmax.items(), key=lambda kv: -kv[1]))
            notes.append(f"各模式高溫中位數 {parts}°C")
        if fc.pop_gap >= 0.4 and len(fc.system_pop) > 1:
            parts = " / ".join(f"{n} {v:.0%}" for n, v in sorted(fc.system_pop.items(), key=lambda kv: -kv[1]))
            notes.append(f"各模式降雨機率 {parts}")
        if not notes and fc.confidence < 45:
            notes.append(f"成員離散度大(高溫 {fc.tmax.p10:.0f}–{fc.tmax.p90:.0f}°C、降雨機率 {fc.pop:.0%})")
        if notes:
            out.append(f"{_label(fc.day)}:" + ";".join(notes) + "。")
    return out


def period_rows(rows: list[PeriodForecast]) -> list[list[str]]:
    """每天一列、四個時段各一格:「氣溫 降雨機率」。"""
    table = [["日期", *(f"{name} {start:02d}–{end:02d}" for name, start, end in PERIODS)]]
    for day, cells in by_day(rows).items():
        line = [_label(day)]
        for name, _, _ in PERIODS:
            cell = cells.get(name)
            if cell is None or cell.temp is None:
                line.append("–")
            elif cell.pop is None:  # 資料沒有涵蓋到這個時段結束,雨量不完整
                line.append(f"{cell.temp.p50:.0f}°")
            else:
                line.append(f"{cell.temp.p50:.0f}° {cell.sky} {cell.pop:.0%}")
        table.append(line)
    return table


def _grid(table: list[list[str]]) -> list[str]:
    widths = [max(_width(row[i]) for row in table) for i in range(len(table[0]))]
    lines = ["  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip() for row in table]
    lines.insert(1, "  ".join("─" * w for w in widths))
    return lines


def _signed(value: float | None, digits: int = 1) -> str:
    return "–" if value is None else f"{value:+.{digits}f}"


def _wind(direction: float | None, speed: float | None) -> str:
    return "–" if direction is None or speed is None else f"{compass(direction)} {speed:.0f}"


def _flow(row: UpperDay) -> str:
    """500 hPa 型態,附上最近槽線的位置。"""
    if row.regime in ("槽前", "槽後") and row.trough is not None:
        side = "西" if row.trough < 0 else "東"
        return f"{row.regime}(槽在{side} {abs(row.trough):.0f}°)"
    return row.regime or "–"


def _jet(row: UpperDay) -> str:
    if row.jet_core is None:
        return "–"
    if not row.has_jet:
        return f"無({row.jet_core:.0f})"
    if row.jet_offset == 0:
        return f"上空 {row.jet_core:.0f}"
    far = "+" if row.jet_at_edge else ""  # 最大風速在剖面邊緣,噴流軸可能更遠
    return f"{'北' if row.jet_offset > 0 else '南'} {abs(row.jet_offset)}°{far} {row.jet_core:.0f}"


def _low_wind(row: UpperDay) -> str:
    """925 hPa 的風;當天風向大轉變時寫成「西→東北 5」,因為平均風向沒有意義。"""
    if row.shift is not None and row.wind925 is not None:
        return f"{compass(row.shift.from_dir)}→{compass(row.shift.to_dir)} {row.wind925:.0f}"
    return _wind(row.dir925, row.wind925)


def upper_rows(rows: list[UpperDay]) -> list[list[str]]:
    """高空分析表(第一列是標題),文字報表與網頁共用。"""
    table = [["日期", "250風", "噴流軸", "輻散", "500槽脊", "高度m", "700濕度", "850溫/濕/風", "平流", "925風", "PWAT", "K", "CAPE"]]
    for r in rows:
        low = "–" if r.t850 is None or r.rh850 is None else f"{r.t850:.0f}°C {r.rh850:.0f}% {_wind(r.dir850, r.wind850)}"
        table.append(
            [
                _label(r.day),
                _wind(r.dir250, r.wind250),
                _jet(r),
                _signed(r.div250),
                _flow(r),
                "–" if r.z500 is None else f"{r.z500:.0f}" + (" 副高" if r.subtropical_high else ""),
                "–" if r.rh700 is None else f"{r.rh700:.0f}%",
                low,
                _signed(r.tadv850),
                _low_wind(r),
                "–" if r.pwat is None else f"{r.pwat:.0f}",
                "–" if r.k is None else f"{r.k:.0f}",
                "–" if r.cape is None else f"{r.cape:.0f}",
            ]
        )
    return table


def _clock(stamp: str) -> str:
    return f"{int(stamp[11:13])} 時"


def shift_text(shift: WindShift) -> str:
    """低層風向轉變的說明,例如「925 hPa 風向在 6 時到 12 時之間由西風轉北風,…」。"""
    if shift.start[:10] == shift.end[:10]:
        when = f" {_clock(shift.start)}到 {_clock(shift.end)}之間"
    else:
        when = f"前一天 {_clock(shift.start)}到當天 {_clock(shift.end)}之間"
    text = f"925 hPa 風向在{when}由{compass(shift.from_dir)}風轉{compass(shift.to_dir)}風,低層鋒面(冷空氣前緣)通過"
    if shift.cooling is not None and shift.cooling >= 1:
        text += f",之後一天內 925 hPa 降溫 {shift.cooling:.0f}°C"
    return text


def upper_impact(rows: list[UpperDay], forecast: list[DayForecast], southern: bool = False) -> list[str]:
    """把高空研判和地面的系集預報對照,說明高空如何影響地面天氣。"""
    out = []
    before, after = ("偏北風", "偏南風") if southern else ("偏南風", "偏北風")  # 槽前的風來自赤道一側
    passage = trough_passage(rows)
    if passage:
        first, second = passage
        when = f" {_label(first)} 過境" if first == second else f"在 {_label(first)} 到 {_label(second)} 之間通過"
        out.append(f"槽線:500 hPa 槽線{when},通過前高空吹{before}、較易有雨,通過後轉{after}。")
    jets = [r for r in rows if r.has_jet and abs(r.jet_offset) <= 10]  # 太遠的噴流影響不到本地
    if jets:
        peak = max(jets, key=lambda r: r.jet_core)
        close = min(jets, key=lambda r: abs(r.jet_offset))
        where = "正上方" if close.jet_offset == 0 else f"{'北' if close.jet_offset > 0 else '南'}方 {abs(close.jet_offset)}°"
        out.append(
            f"噴流:250 hPa 噴流軸最強 {peak.jet_core:.0f} m/s({_label(peak.day)}),"
            f"最接近時在{where}({_label(close.day)})。"
        )

    surface = {fc.day: fc for fc in forecast}
    previous = None
    for row in rows:
        fc = surface.get(row.day)
        reasons = row.lift + row.sink
        text = row.verdict + (f"({'、'.join(reasons)})" if reasons else "")
        if fc:
            wet, dry = fc.pop >= 0.6, fc.pop < 0.3
            if row.outlook in ("wet", "showery") and dry:
                text += f";但系集降雨機率只有 {fc.pop:.0%},留意之後上修"
            elif row.outlook == "wet" and not wet:
                text += f";但系集降雨機率只有 {fc.pop:.0%},高空條件比系集的降雨預報樂觀,以系集為準"
            elif row.outlook in ("wet", "showery") and wet:
                text += f";與系集降雨機率 {fc.pop:.0%} 一致"
            elif row.outlook == "neutral" and wet:
                text += f";系集降雨機率卻有 {fc.pop:.0%},雨主要由這裡看不到的低層或地形因素造成"
            elif row.outlook == "shallow" and wet:
                text += f";降雨機率雖有 {fc.pop:.0%},雨量不大(中位數 {fc.precip.p50:.0f} mm)"
            elif row.outlook == "stable" and wet:
                text += f";但系集降雨機率 {fc.pop:.0%},雨應來自低層或地形,高空並不支持"
            elif row.outlook == "stable" and dry:
                text += ";與系集少雨一致"
        extras = []
        if row.shift is not None:
            extras.append(shift_text(row.shift))
        if row.rh700_early is not None and row.rh700_late is not None:
            if row.rh700_early >= 70 and row.rh700_late <= 45:
                extras.append(f"700 hPa 濕度由 {row.rh700_early:.0f}% 降到 {row.rh700_late:.0f}%,中層在當天轉乾")
            elif row.rh700_early <= 45 and row.rh700_late >= 70:
                extras.append(f"700 hPa 濕度由 {row.rh700_early:.0f}% 升到 {row.rh700_late:.0f}%,中層在當天轉濕")
        if row.pwat is not None and row.pwat >= PWAT_RICH and row.outlook in ("wet", "showery"):
            extras.append(f"可降水量 {row.pwat:.0f} mm,整層水氣充沛,有雨時雨勢可能較大")
        if row.wind925 is not None and row.rh850 is not None and row.wind925 >= 8 and row.rh850 >= 80:
            extras.append(f"925 hPa {compass(row.dir925)}風 {row.wind925:.0f} m/s 輸送低層水氣,迎風面易有地形雨")
        if previous is not None and row.t925 is not None and previous.t925 is not None:
            change = row.t925 - previous.t925
            if change <= -3:
                extras.append(f"925 hPa 一天降溫 {abs(change):.0f}°C,低層冷空氣移入")
            elif change >= 3:
                extras.append(f"925 hPa 一天升溫 {change:.0f}°C")
        out.append(f"{_label(row.day)}:{text}。" + "".join(f"{e}。" for e in extras))
        previous = row
    return out


def _trend_text(trend: Trend) -> str:
    if trend.what == "pop":
        name, series = "降雨機率", " → ".join(f"{v:.0%}" for v in trend.values)
    else:
        name, series = "高溫", " → ".join(f"{v:.0f}" for v in trend.values) + "°C"
    if trend.kind == "swinging":
        return f"{name}來回擺盪({series}),模式還沒有定論"
    direction = "上修" if trend.kind == "rising" else "下修"
    return f"{name}連續 {len(trend.values) - 1} 次{direction}({series}),修正方向一致、趨勢較可信"


def comparison_lines(comparison: Comparison) -> list[str]:
    """和上一次預報差多少,以及最近幾次起報是同方向修正還是來回擺盪。"""
    notes: dict[str, list[str]] = {}
    for c in comparison.changes:
        bits = []
        if abs(c.tmax) >= 1:
            bits.append(f"高溫{'上修' if c.tmax > 0 else '下修'} {abs(c.tmax):.0f}°C")
        if abs(c.pop) >= 0.15:
            bits.append(f"降雨機率{'提高' if c.pop > 0 else '降低'} {abs(c.pop) * 100:.0f} 個百分點")
        if bits:
            notes.setdefault(c.day, []).append("、".join(bits))
    for trend in comparison.trends:
        notes.setdefault(trend.day, []).append(_trend_text(trend))
    if not notes:
        return ["沒有明顯變化,預報穩定。"]
    return [f"{_label(day)}:" + ";".join(parts) + "。" for day, parts in sorted(notes.items())]


def render(
    place_name: str,
    ens: Ensemble,
    forecast: list[DayForecast],
    signal_list: list[Signal],
    comparison: Comparison | None = None,
    upper_days: list[UpperDay] | None = None,
    period_list: list[PeriodForecast] | None = None,
    brief=None,
) -> str:
    meta = ens.meta
    lat, lon = meta.get("latitude") or 0.0, meta.get("longitude") or 0.0
    where = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
    counts = " + ".join(f"{name} {ens.member_count(name)}" for name in ens.systems)
    total = sum(ens.member_count(name) for name in ens.systems)
    lines = [
        f"{place_name} 未來 {len(forecast)} 天天氣預報",
        f"{where} · 時區 {meta.get('timezone') or 'UTC'} · 模式格點海拔 {meta.get('elevation') or 0:.0f} m",
        f"資料:{counts} = {total} 個系集成員",
        "",
    ]
    if brief is not None:  # 先講結論:這一週分成哪幾段
        lines += [brief.lede, *(f"• 時段{'一二三'[s.index - 1]} {s.summary}" for s in brief.stages), ""]
    lines += _table(forecast)
    if period_list:
        lines += ["", "時段預報(氣溫、天氣、6 小時內降雨機率)", *_grid(period_rows(period_list))]
    lines += ["", "重點", *(f"• {line}" for line in highlights(forecast))]
    if signal_list:
        lines += ["", "天氣型態(氣壓與高空場)"]
        by_day: dict[str, list[str]] = {}
        for s in signal_list:
            by_day.setdefault(s.day, []).append(s.text)
        lines += [f"• {_label(day)}:" + ";".join(texts) + "。" for day, texts in by_day.items()]
    if upper_days:
        lines += [
            "",
            "高空分析(ECMWF 與 AIFS 系集的中位數;風速 m/s,輻散 10⁻⁵/s,平流 °C/日)",
            *_grid(upper_rows(upper_days)),
            "",
            "高空對地面的影響",
            *(f"• {line}" for line in upper_impact(upper_days, forecast, lat < 0)),
        ]
    notes = uncertainty(forecast)
    if notes:
        lines += ["", "不確定性"]
        lines += [f"• {n}" for n in notes]
    if comparison:
        lines += ["", f"與 {comparison.age_hours:.0f} 小時前的預報比較", *(f"• {line}" for line in comparison_lines(comparison))]
    lines += [
        "",
        "說明:數值是所有成員的中位數,括號為 10–90% 範圍;降雨機率是日雨量達 1 mm 的成員比例;",
        "「偏多」雨量是 90 百分位。各模式整週固定的溫度偏差已先校正到多模式共識。",
        "信心分數越低,代表成員或模式之間越不一致。",
    ]
    if upper_days:
        lines += [
            "高空表:噴流軸欄是 250 hPa 最大風速的位置(相對測站)與風速,未達 30 m/s 記為無,",
            "「20°+」表示在分析範圍邊緣、可能更遠;槽脊欄的度數是槽線相對測站的經度距離;",
            "輻散為正代表高層空氣流出、有利下方上升;平流為正是暖平流;925 風欄的「西→東北」表示當天",
            "低層風向轉變(鋒面通過);PWAT 是整層大氣的可降水量(mm);K 指數 30 以上或 CAPE(J/kg,",
            "當天最大值)1000 以上容易有雷雨。",
        ]
    return "\n".join(lines)


def to_json(place_name, ens, forecast, signal_list, comparison=None, upper_days=None, period_list=None) -> dict:
    return {
        "place": place_name,
        "location": ens.meta,
        "systems": {name: ens.member_count(name) for name in ens.systems},
        "days": [asdict(fc) | {"weekday": fc.weekday} for fc in forecast],
        "periods": [asdict(p) for p in period_list or []],
        "signals": [asdict(s) for s in signal_list],
        "highlights": highlights(forecast),
        "uncertainty": uncertainty(forecast),
        "upper": None
        if not upper_days
        else {
            "days": [asdict(r) for r in upper_days],
            "impact": upper_impact(upper_days, forecast, (ens.meta.get("latitude") or 0.0) < 0),
        },
        "comparison": None
        if comparison is None
        else {
            "previous_at": comparison.previous_at.isoformat(),
            "age_hours": round(comparison.age_hours, 1),
            "changes": [asdict(c) for c in comparison.changes],
            "trends": [asdict(t) for t in comparison.trends],
            "notes": comparison_lines(comparison),
        },
    }
