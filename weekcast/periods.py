"""時段預報:把每一天拆成凌晨、上午、下午、晚上四段。

逐日預報看不出雨是早上下還是晚上下。這裡用每 3 小時一筆的系集資料,
以同樣的方法(所有成員合併、各系集等權重)算出每個 6 小時時段的氣溫、
降雨機率與雲量。

時效越遠,時段的可信度下降得比整天快:鋒面晚到半天,整天的預報仍然對,
時段預報卻會整個錯開。

時間標記的意義(2026-11-12 以逐時資料實測核對):

- 雨量是「往前累積」:標示 15:00 的那一筆是 12–15 時下的雨,不是 15–18 時。
  所以 12–18 時的雨量要取標示 15:00 與 18:00 的兩筆,晚上的最後一筆是隔天 00:00。
- 氣溫與雲量是該時刻的瞬間值,12–18 時取標示 12:00 與 15:00 的兩筆。
- 時區與 UTC 的差不是 3 小時的倍數時(台灣是 +8),每一筆實際上再早 1–2 小時,
  這是資料來源對齊 UTC 的結果,無法再修正。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .analysis import Spread, _section, describe_sky, wfraction
from .systems import PERIOD_VARS

PERIODS = (("凌晨", 0, 6), ("上午", 6, 12), ("下午", 12, 18), ("晚上", 18, 24))
RAIN_MM = 0.5  # 6 小時內達此雨量算有雨(逐日用 1 mm,時段較短所以門檻較低)


@dataclass
class PeriodForecast:
    day: str
    name: str  # 凌晨 / 上午 / 下午 / 晚上
    start: int  # 起始時刻(當地時間)
    temp: Spread | None
    precip: Spread | None  # 資料不足以涵蓋整個時段時是 None
    pop: float | None
    cloud: Spread | None
    sky: str = ""


def _pool(by_system: dict[str, list[float]]):
    return [(v, 1 / len(values) / len(by_system)) for values in by_system.values() for v in values]


def period_forecast(raw: dict, days: int | None = None) -> list[PeriodForecast]:
    """由 client.fetch_periods() 的結果算出各時段的預報。"""
    times = raw["hourly"]["time"]
    systems = _section(raw, "hourly", PERIOD_VARS)
    stamps = [datetime.fromisoformat(t) for t in times]
    step = stamps[1] - stamps[0] if len(stamps) > 1 else timedelta(hours=3)
    day_list = list(dict.fromkeys(t[:10] for t in times))[: days or None]
    out = []
    for day in day_list:
        midnight = datetime.fromisoformat(day)
        for name, start, end in PERIODS:
            begin, finish = midnight + timedelta(hours=start), midnight + timedelta(hours=end)
            instant = [i for i, t in enumerate(stamps) if begin <= t < finish]
            if not instant:
                continue
            # 累積量:每一筆涵蓋它之前的一個時間步,所以取 (begin, finish] 的各筆
            accumulated = [i for i, t in enumerate(stamps) if begin < t <= finish]
            covered = len(accumulated) * step >= finish - begin

            def members(var, reducer, steps):
                by_system = {}
                for system, variables in systems.items():
                    values = []
                    for member in variables.get(var, []):
                        got = [member[i] for i in steps if i < len(member) and member[i] is not None]
                        if len(got) == len(steps):
                            values.append(reducer(got))
                    if values:
                        by_system[system] = values
                return _pool(by_system)

            mean = lambda xs: sum(xs) / len(xs)
            rain = members("precipitation", sum, accumulated) if covered else []
            temp = members("temperature_2m", mean, instant)
            cloud = members("cloud_cover", mean, instant)
            if not rain and not temp:
                continue
            row = PeriodForecast(
                day=day,
                name=name,
                start=start,
                temp=Spread.of(temp),
                precip=Spread.of(rain),
                pop=wfraction(rain, lambda v: v >= RAIN_MM) if rain else None,
                cloud=Spread.of(cloud),
            )
            if row.pop is not None and row.precip is not None:
                # describe_sky 的雨量門檻是整天的量,時段只有四分之一天,換算後再判斷
                scaled = Spread(row.precip.p10 * 4, row.precip.p50 * 4, row.precip.p90 * 4)
                row.sky = describe_sky(row.pop, scaled, row.cloud)
            out.append(row)
    return out


def by_day(rows: list[PeriodForecast]) -> dict[str, dict[str, PeriodForecast]]:
    table: dict[str, dict[str, PeriodForecast]] = {}
    for row in rows:
        table.setdefault(row.day, {})[row.name] = row
    return table
