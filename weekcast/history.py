"""保存每次的預報摘要,用來比較「這次預報和前幾次差多少」。

這是把 Tropical Tidbits 的 Trend GIF / 上下方向鍵(同一個有效時間、比較前後
幾次模式起報)的概念搬到數值上:預報連續幾次都往同一方向修正,代表趨勢可信;
來回擺盪則代表模式還沒抓到答案。

紀錄是一串「一次執行一筆」的摘要。命令列存在本機的快取資料夾;網頁版沒有
固定的磁碟,所以把紀錄放在發布出去的 data.json 裡,下次產生網頁時再讀回來。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analysis import DayForecast

MIN_AGE = timedelta(hours=5)  # 全球模式約每 6 小時更新一次,太近的紀錄是同一次起報
MAX_AGE = timedelta(days=3)
KEEP = 40
TREND_RUNS = 3  # 看趨勢時最多往回看幾次起報
# (每次修正至少要多大才算數, 來回擺盪時每次至少要多大)
TREND_STEPS = {"pop": (0.10, 0.15), "tmax": (1.0, 1.5)}


def default_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "weekcast"


def _path(directory: Path, latitude: float, longitude: float) -> Path:
    return directory / f"{latitude:.2f}_{longitude:.2f}.jsonl"


@dataclass
class Change:
    day: str
    tmax: float  # 高溫中位數的變化(°C)
    pop: float  # 降雨機率的變化(0–1)


@dataclass
class Trend:
    """同一天的預報在最近幾次起報之間怎麼變。"""

    day: str
    what: str  # pop / tmax
    kind: str  # rising / falling / swinging
    values: list[float]  # 由舊到新,最後一個是這次的預報


@dataclass
class Comparison:
    previous_at: datetime
    age_hours: float
    changes: list[Change]
    trends: list[Trend] = field(default_factory=list)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # 忽略寫到一半的行
    return rows


def snapshot(forecast: list[DayForecast], now: datetime) -> dict:
    """一次執行的摘要:每天的高低溫中位數與降雨機率。"""
    return {
        "at": now.isoformat(),
        "days": {fc.day: {"tmax": round(fc.tmax.p50, 2), "tmin": round(fc.tmin.p50, 2), "pop": round(fc.pop, 3)} for fc in forecast},
    }


def extend(rows: list[dict], forecast: list[DayForecast], now: datetime, keep: int = KEEP) -> list[dict]:
    """在紀錄後面加上這次的摘要,只留最近 keep 筆。"""
    return [*rows, snapshot(forecast, now)][-keep:]


def _earlier_runs(rows: list[dict], now: datetime) -> list[tuple[datetime, dict]]:
    """可以拿來比較的先前起報,由新到舊;彼此至少相隔 MIN_AGE,才是不同次的模式起報。"""
    dated = []
    for row in rows:
        try:
            at = datetime.fromisoformat(row["at"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(row.get("days"), dict):
            dated.append((at, row))
    picked, reference = [], now
    for at, row in sorted(dated, key=lambda item: item[0], reverse=True):
        if reference - at >= MIN_AGE and now - at <= MAX_AGE:
            picked.append((at, row))
            reference = at
    return picked


def _trend(day: str, what: str, values: list[float]) -> Trend | None:
    """由舊到新的一串預報值 → 連續同方向修正,或來回擺盪。"""
    step, swing = TREND_STEPS[what]
    moves = [b - a for a, b in zip(values, values[1:])]
    if len(moves) < 2:
        return None
    run = 0  # 最近連續幾次往同一個方向修正
    for move in reversed(moves):
        if abs(move) >= step and (move > 0) == (moves[-1] > 0):
            run += 1
        else:
            break
    if run >= 2:
        return Trend(day, what, "rising" if moves[-1] > 0 else "falling", values[-run - 1 :])
    last, before = moves[-1], moves[-2]
    if abs(last) >= swing and abs(before) >= swing and (last > 0) != (before > 0):
        return Trend(day, what, "swinging", values[-3:])
    return None


def compare_runs(rows: list[dict], forecast: list[DayForecast], now: datetime | None = None) -> Comparison | None:
    """與先前(至少 5 小時前)的預報比較;沒有可比較的紀錄時回傳 None。"""
    now = now or datetime.now(timezone.utc)
    runs = _earlier_runs(rows, now)
    if not runs:
        return None
    at, latest = runs[0]
    changes, trends = [], []
    for fc in forecast:
        old = latest["days"].get(fc.day)
        if not old:
            continue
        try:
            changes.append(Change(fc.day, fc.tmax.p50 - old["tmax"], fc.pop - old["pop"]))
            for what, current in (("pop", fc.pop), ("tmax", fc.tmax.p50)):
                values = [current]
                for _, row in runs[:TREND_RUNS]:
                    if fc.day not in row["days"]:
                        break
                    values.insert(0, row["days"][fc.day][what])
                trend = _trend(fc.day, what, values)
                if trend:
                    trends.append(trend)
        except (KeyError, TypeError):
            continue  # 格式不合的舊紀錄
    if not changes:
        return None
    return Comparison(at, (now - at).total_seconds() / 3600, changes, trends)


def compare(directory: Path, latitude: float, longitude: float, forecast: list[DayForecast], now: datetime | None = None):
    """與存在本機的先前預報比較;沒有可比較的紀錄時回傳 None。"""
    return compare_runs(_read(_path(directory, latitude, longitude)), forecast, now)


def record(directory: Path, latitude: float, longitude: float, forecast: list[DayForecast], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    path = _path(directory, latitude, longitude)
    rows = extend(_read(path), forecast, now)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
