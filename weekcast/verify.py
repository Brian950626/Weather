"""預報校驗:過去幾天的預報,和後來的實際狀況差多少。

做法和課堂報告的「逐日校驗」相同:誤差是預報減觀測,正值代表預報偏暖;降雨用
列聯表,分成有對、漏報(沒報卻下了)、空報(報了卻沒下)。同一天再比較幾天前的
預報,可以看出時效越遠誤差是不是越大。

對照的不是測站觀測,而是 Open-Meteo 整理的分析資料(各模式最短時效的預報接起來)。
分析場不等於真實,樣本也很少,只能當參考。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

RAIN_POP = 0.4  # 降雨機率達此值算「報了會下雨」(自訂門檻)
RAIN_MM = 1.0  # 分析資料的日雨量達此值算「有下雨」,和降雨機率的定義一致
LEADS = (1, 3, 5)  # 逐日表格列出幾天前的預報
MAX_LEAD = 6


@dataclass
class Check:
    """某一天、某個時效的預報對上實際狀況。"""

    day: str
    lead: int  # 幾天前做的預報
    tmax: float  # 預報
    tmin: float
    pop: float
    tmax_error: float  # 預報減觀測
    tmin_error: float
    rain: str  # 有對 / 漏報 / 空報


@dataclass
class LeadScore:
    lead: int
    count: int
    tmax_bias: float
    tmax_mae: float
    tmin_bias: float
    tmin_mae: float
    hits: int
    misses: int
    false_alarms: int


@dataclass
class Verification:
    observed: dict[str, dict]  # 日期 → {"tmax", "tmin", "rain"}
    checks: list[Check] = field(default_factory=list)
    scores: list[LeadScore] = field(default_factory=list)

    def at(self, day: str, lead: int) -> Check | None:
        return next((c for c in self.checks if c.day == day and c.lead == lead), None)


def parse_observed(raw: dict, today: str) -> dict[str, dict]:
    """把 Open-Meteo 的逐日資料整理成 {日期: {"tmax", "tmin", "rain"}},只留今天以前、資料完整的日子。"""
    daily = raw.get("daily") or {}
    out = {}
    for day, tmax, tmin, rain in zip(
        daily.get("time", []), daily.get("temperature_2m_max", []), daily.get("temperature_2m_min", []), daily.get("precipitation_sum", [])
    ):
        if day < today and None not in (tmax, tmin, rain):
            out[day] = {"tmax": tmax, "tmin": tmin, "rain": rain}
    return out


def verify(runs: list[dict], observed: dict[str, dict]) -> Verification | None:
    """runs 是先前各次執行的摘要(history.snapshot 的格式);沒有可以對照的日子時回傳 None。"""
    latest: dict[tuple[str, int], dict] = {}  # (日期, 時效) → 該時效最晚的一次預報
    for run in sorted((r for r in runs if isinstance(r.get("days"), dict) and r["days"] and "at" in r), key=lambda r: str(r["at"])):
        issued = date.fromisoformat(min(run["days"]))
        for day, values in run["days"].items():
            lead = (date.fromisoformat(day) - issued).days
            if day in observed and 1 <= lead <= MAX_LEAD:
                latest[(day, lead)] = values
    checks = []
    for (day, lead), values in sorted(latest.items()):
        seen = observed[day]
        try:
            said, fell = values["pop"] >= RAIN_POP, seen["rain"] >= RAIN_MM
            checks.append(
                Check(day, lead, values["tmax"], values["tmin"], values["pop"], values["tmax"] - seen["tmax"], values["tmin"] - seen["tmin"],
                      "有對" if said == fell else "漏報" if fell else "空報")
            )
        except (KeyError, TypeError):
            continue
    if not checks:
        return None
    scores = []
    for lead in sorted({c.lead for c in checks}):
        group = [c for c in checks if c.lead == lead]
        mean = lambda values: sum(values) / len(values)
        scores.append(
            LeadScore(
                lead, len(group),
                mean([c.tmax_error for c in group]), mean([abs(c.tmax_error) for c in group]),
                mean([c.tmin_error for c in group]), mean([abs(c.tmin_error) for c in group]),
                sum(c.rain == "有對" for c in group), sum(c.rain == "漏報" for c in group), sum(c.rain == "空報" for c in group),
            )
        )
    return Verification(observed, checks, scores)
