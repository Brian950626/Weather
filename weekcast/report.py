"""把分析結果排版成文字報表或 JSON。"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict
from datetime import date

from .analysis import DayForecast, Ensemble, Signal
from .history import Comparison


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    gap = " " * max(0, width - _width(text))
    return gap + text if right else text + gap


def _label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.month}/{d.day} {'一二三四五六日'[d.weekday()]}"


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
        if fc.confidence_label == "高":
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
        if not notes and fc.confidence_label == "低":
            notes.append(f"成員離散度大(高溫 {fc.tmax.p10:.0f}–{fc.tmax.p90:.0f}°C、降雨機率 {fc.pop:.0%})")
        if notes:
            out.append(f"{_label(fc.day)}:" + ";".join(notes) + "。")
    return out


def render(
    place_name: str,
    ens: Ensemble,
    forecast: list[DayForecast],
    signal_list: list[Signal],
    comparison: Comparison | None = None,
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
        *_table(forecast),
        "",
        "重點",
        *(f"• {line}" for line in highlights(forecast)),
    ]
    if signal_list:
        lines += ["", "天氣型態(氣壓與高空場)"]
        by_day: dict[str, list[str]] = {}
        for s in signal_list:
            by_day.setdefault(s.day, []).append(s.text)
        lines += [f"• {_label(day)}:" + ";".join(texts) + "。" for day, texts in by_day.items()]
    notes = uncertainty(forecast)
    if notes:
        lines += ["", "不確定性"]
        lines += [f"• {n}" for n in notes]
    if comparison:
        moved = [c for c in comparison.changes if abs(c.tmax) >= 1 or abs(c.pop) >= 0.15]
        lines += ["", f"與 {comparison.age_hours:.0f} 小時前的預報比較"]
        if moved:
            for c in moved:
                bits = []
                if abs(c.tmax) >= 1:
                    bits.append(f"高溫{'上修' if c.tmax > 0 else '下修'} {abs(c.tmax):.0f}°C")
                if abs(c.pop) >= 0.15:
                    bits.append(f"降雨機率{'提高' if c.pop > 0 else '降低'} {abs(c.pop) * 100:.0f} 個百分點")
                lines.append(f"• {_label(c.day)}:" + "、".join(bits) + "。")
        else:
            lines.append("• 沒有明顯變化,預報穩定。")
    lines += [
        "",
        "說明:數值是所有成員的中位數,括號為 10–90% 範圍;降雨機率是日雨量達 1 mm 的成員比例;",
        "「偏多」雨量是 90 百分位。各模式整週固定的溫度偏差已先校正到多模式共識。",
        "信心分數越低,代表成員或模式之間越不一致。",
    ]
    return "\n".join(lines)


def to_json(place_name, ens, forecast, signal_list, comparison=None) -> dict:
    return {
        "place": place_name,
        "location": ens.meta,
        "systems": {name: ens.member_count(name) for name in ens.systems},
        "days": [asdict(fc) | {"weekday": fc.weekday} for fc in forecast],
        "signals": [asdict(s) for s in signal_list],
        "highlights": highlights(forecast),
        "uncertainty": uncertainty(forecast),
        "comparison": None
        if comparison is None
        else {
            "previous_at": comparison.previous_at.isoformat(),
            "age_hours": round(comparison.age_hours, 1),
            "changes": [asdict(c) for c in comparison.changes],
        },
    }
