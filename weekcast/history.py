"""保存每次的預報摘要,用來比較「這次預報和上次差多少」。

這是把 Tropical Tidbits 的 Trend GIF / 上下方向鍵(同一個有效時間、比較前後
幾次模式起報)的概念搬到數值上:預報連續幾次都往同一方向修正,代表趨勢可信;
來回擺盪則代表模式還沒抓到答案。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analysis import DayForecast

MIN_AGE = timedelta(hours=5)  # 全球模式約每 6 小時更新一次,太近的紀錄是同一次起報
MAX_AGE = timedelta(days=3)
KEEP = 40


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
class Comparison:
    previous_at: datetime
    age_hours: float
    changes: list[Change]


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


def compare(directory: Path, latitude: float, longitude: float, forecast: list[DayForecast], now: datetime | None = None):
    """與先前(至少 5 小時前)的預報比較;沒有可比較的紀錄時回傳 None。"""
    now = now or datetime.now(timezone.utc)
    candidates = []
    for row in _read(_path(directory, latitude, longitude)):
        try:
            at = datetime.fromisoformat(row["at"])
        except (KeyError, ValueError):
            continue
        if MIN_AGE <= now - at <= MAX_AGE:
            candidates.append((at, row))
    if not candidates:
        return None
    at, row = max(candidates, key=lambda c: c[0])
    changes = []
    for fc in forecast:
        old = row.get("days", {}).get(fc.day)
        if old:
            changes.append(Change(fc.day, fc.tmax.p50 - old["tmax"], fc.pop - old["pop"]))
    if not changes:
        return None
    return Comparison(at, (now - at).total_seconds() / 3600, changes)


def record(directory: Path, latitude: float, longitude: float, forecast: list[DayForecast], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    path = _path(directory, latitude, longitude)
    rows = _read(path)
    rows.append(
        {
            "at": now.isoformat(),
            "days": {fc.day: {"tmax": round(fc.tmax.p50, 2), "tmin": round(fc.tmin.p50, 2), "pop": round(fc.pop, 3)} for fc in forecast},
        }
    )
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[-KEEP:]), encoding="utf-8")
