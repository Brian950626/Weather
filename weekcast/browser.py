"""在瀏覽器裡(Pyodide)執行的進入點:網頁搜尋欄查詢任意地點時使用。

分析程式和命令列、自動更新用的是同一份,只有抓資料的方式不同:瀏覽器裡沒有
urllib 可用,改用 Pyodide 提供的 pyfetch。這樣搜尋到的地點和預設城市的算法
完全一致,不必另外維護一套 JavaScript 版本。
"""

from __future__ import annotations

import json

from . import client, site


async def _get(params: dict):
    from pyodide.http import pyfetch  # 只在瀏覽器裡存在

    response = await pyfetch(client.url_for(params))
    data = json.loads(await response.string())
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("reason") or "資料來源回報錯誤")
    return data


async def forecast(name: str, latitude: float, longitude: float, days: int = 7, get=_get) -> str:
    """回傳一個地點的預報 HTML 區塊。高空或時段資料抓不到時,仍給出其餘部分。"""
    raw = await get(client.surface_params(latitude, longitude, days))
    zone = raw.get("timezone") or "GMT"
    try:
        points = client.cross_points(latitude, longitude)
        upper = {
            "profile": await get(client.profile_params(latitude, longitude, days, zone)),
            "cross": {"points": points, "data": await get(client.cross_params(points, days, zone))},
        }
    except Exception:
        upper = None
    try:
        period = await get(client.period_params(latitude, longitude, days, zone))
    except Exception:
        period = None
    block, _ = site.build_place(name, raw, upper, days, period)
    return block
