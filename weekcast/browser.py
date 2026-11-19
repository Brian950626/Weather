"""在瀏覽器裡(Pyodide)執行的進入點:網頁搜尋欄查詢任意地點、總覽加入地點一起比較時使用。

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


async def _fetch(latitude: float, longitude: float, days: int, get):
    """抓一個地點需要的資料。高空或時段資料抓不到時,仍給出其餘部分。"""
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
    return raw, upper, period


async def forecast(name: str, latitude: float, longitude: float, days: int = 7, get=_get) -> str:
    """回傳一個地點的預報 HTML 區塊。"""
    raw, upper, period = await _fetch(latitude, longitude, days, get)
    block, _ = site.build_place(name, raw, upper, days, period)
    return block


async def bundle(name: str, latitude: float, longitude: float, days: int = 7, get=_get) -> str:
    """一次算出地點頁的 HTML 與資料(JSON 字串),地點分頁與總覽的比較共用,不必算兩次。"""
    raw, upper, period = await _fetch(latitude, longitude, days, get)
    block, data = site.build_place(name, raw, upper, days, period)
    data.pop("runs", None)  # 搜尋的地點沒有歷次紀錄
    return json.dumps({"block": block, "data": data}, ensure_ascii=False)


def overview(names_json: str, payloads_json: str, index_json: str) -> str:
    """把選的地點(預設地點來自 data.json,加入的地點來自 bundle)重畫成總覽頁的本體。"""
    names, payloads, index = json.loads(names_json), json.loads(payloads_json), json.loads(index_json)
    return site._overview_body(names, payloads, {n: int(index.get(n, i)) for i, n in enumerate(names)})
