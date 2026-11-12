"""產生靜態預報網頁:python -m weekcast.site --out _site 台北 台中 高雄

輸出 index.html(所有地點在同一頁,以分頁切換)與 data.json(同一份資料的
機器可讀版本)。由 .github/workflows/pages.yml 定時執行並發布到 GitHub Pages。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import analysis, client, report, upper

DEFAULT_PLACES = ("台北", "台中", "高雄", "花蓮")
REPO_URL = "https://github.com/Brian950626/Weather"

CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--axis:#c3c2b7;--series:#2a78d6;--series-soft:#b7d3f6;--good:#006300;--warn:#8a5a00;--bad:#b02a2a}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--series:#3987e5;--series-soft:#184f95;--good:#0ca30c;--warn:#fab219;--bad:#e66767}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1080px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.sub{color:var(--ink2);font-size:13px;margin:0}
nav{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
nav button{font:inherit;padding:6px 16px;border:1px solid var(--axis);border-radius:999px;background:var(--surface);color:var(--ink);cursor:pointer}
nav button[aria-selected=true]{background:var(--ink);color:var(--page);border-color:var(--ink)}
section.place{display:none}section.place.on{display:block}
.card{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:14px 16px;margin:12px 0}
.days{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px}
.day{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:10px 12px}
.day b{display:block;font-size:13px;color:var(--ink2);font-weight:600}
.day .sky{font-size:15px;font-weight:600;margin:2px 0}
.day .t{font-size:20px;font-weight:700}.day .t small{font-size:13px;font-weight:400;color:var(--ink2)}
.day .p{font-size:13px;color:var(--ink2)}
.conf{display:inline-block;font-size:12px;margin-top:4px;font-weight:600}
.conf.高{color:var(--good)}.conf.中{color:var(--warn)}.conf.低{color:var(--bad)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--grid)}
th{color:var(--ink2);font-weight:600}
ul{margin:6px 0;padding-left:20px}li{margin:4px 0}
svg{display:block;min-width:560px;width:100%}
svg text{font:12px system-ui,sans-serif;fill:var(--muted)}
svg .val{fill:var(--ink2)}svg .gridline{stroke:var(--grid)}svg .bar{fill:var(--series)}svg .range{fill:var(--series-soft)}
svg g.hit:hover .bar{opacity:.8}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);padding:6px 10px;border-radius:6px;font-size:12px;white-space:pre;display:none;z-index:9}
footer{color:var(--ink2);font-size:12px;margin-top:32px}a{color:var(--series)}
"""

JS = """
const tabs=[...document.querySelectorAll('nav button')],secs=[...document.querySelectorAll('section.place')];
function show(i){tabs.forEach((b,j)=>b.setAttribute('aria-selected',i===j));secs.forEach((s,j)=>s.classList.toggle('on',i===j));
try{localStorage.setItem('place',i)}catch(e){}}
tabs.forEach((b,i)=>b.addEventListener('click',()=>show(i)));
let start=0;try{start=+localStorage.getItem('place')||0}catch(e){}show(start<tabs.length?start:0);
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el=>{
el.addEventListener('mousemove',e=>{tip.textContent=el.dataset.tip;tip.style.display='block';
tip.style.left=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=(e.clientY+14)+'px'});
el.addEventListener('mouseleave',()=>tip.style.display='none')});
"""

esc = html.escape


def _temperature_chart(forecast) -> str:
    """每天一條浮動長條:淺色是 10–90% 的可能範圍,深色是低溫到高溫的中位數。"""
    width, height, left, top, bottom = 720, 220, 44, 16, 30
    lo = min(f.tmin.p10 for f in forecast)
    hi = max(f.tmax.p90 for f in forecast)
    lo, hi = 2 * (int(lo) // 2) - 2, 2 * (int(hi) // 2) + 4
    plot = height - top - bottom
    y = lambda value: top + plot * (hi - value) / (hi - lo)
    slot = (width - left) / len(forecast)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="每日高低溫範圍">']
    step = 2 if hi - lo <= 16 else 4
    for tick in range(lo, hi + 1, step):
        parts.append(f'<line class="gridline" x1="{left}" x2="{width}" y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" text-anchor="end">{tick}°</text>')
    for i, f in enumerate(forecast):
        cx = left + slot * (i + 0.5)
        tip = (
            f"{report._label(f.day)}\n高溫 {f.tmax.p50:.0f}°C(範圍 {f.tmax.p10:.0f}–{f.tmax.p90:.0f})\n"
            f"低溫 {f.tmin.p50:.0f}°C(範圍 {f.tmin.p10:.0f}–{f.tmin.p90:.0f})"
        )
        parts.append(f'<g class="hit" data-tip="{esc(tip)}">')
        parts.append(f'<rect x="{cx - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{plot}" fill="transparent"/>')
        parts.append(
            f'<rect class="range" x="{cx - 9:.1f}" y="{y(f.tmax.p90):.1f}" width="18" '
            f'height="{y(f.tmin.p10) - y(f.tmax.p90):.1f}" rx="4"/>'
        )
        parts.append(
            f'<rect class="bar" x="{cx - 9:.1f}" y="{y(f.tmax.p50):.1f}" width="18" '
            f'height="{max(2, y(f.tmin.p50) - y(f.tmax.p50)):.1f}" rx="4"/>'
        )
        parts.append(f'<text class="val" x="{cx:.1f}" y="{y(f.tmax.p90) - 4:.1f}" text-anchor="middle">{f.tmax.p50:.0f}°</text>')
        parts.append(f'<text class="val" x="{cx:.1f}" y="{y(f.tmin.p10) + 13:.1f}" text-anchor="middle">{f.tmin.p50:.0f}°</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle">{report._label(f.day)}</text></g>')
    parts.append("</svg>")
    return '<div class="scroll">' + "".join(parts) + "</div>"


def _rain_chart(forecast) -> str:
    width, height, left, top, bottom = 720, 150, 44, 22, 30
    plot = height - top - bottom
    y = lambda value: top + plot * (1 - value)
    slot = (width - left) / len(forecast)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="每日降雨機率">']
    for tick in (0, 0.5, 1):
        parts.append(f'<line class="gridline" x1="{left}" x2="{width}" y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" text-anchor="end">{tick:.0%}</text>')
    for i, f in enumerate(forecast):
        cx = left + slot * (i + 0.5)
        tip = f"{report._label(f.day)}\n降雨機率 {f.pop:.0%}\n雨量中位數 {f.precip.p50:.0f} mm,偏多時 {f.precip.p90:.0f} mm"
        parts.append(f'<g class="hit" data-tip="{esc(tip)}">')
        parts.append(f'<rect x="{cx - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{plot}" fill="transparent"/>')
        tall = max(1.0, plot * f.pop)
        parts.append(f'<rect class="bar" x="{cx - 9:.1f}" y="{top + plot - tall:.1f}" width="18" height="{tall:.1f}" rx="4"/>')
        parts.append(f'<text class="val" x="{cx:.1f}" y="{top + plot - tall - 4:.1f}" text-anchor="middle">{f.pop:.0%}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle">{report._label(f.day)}</text></g>')
    parts.append("</svg>")
    return '<div class="scroll">' + "".join(parts) + "</div>"


def _table(rows: list[list[str]]) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in rows[0])
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows[1:])
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else ""


def _surface_rows(forecast) -> list[list[str]]:
    rows = [["日期", "天氣", "高溫 °C(10–90%)", "低溫 °C(10–90%)", "降雨機率", "雨量 mm 中位/偏多", "最大風 km/h", "信心"]]
    for f in forecast:
        wind = "–" if not f.wind else f"{f.wind.p50:.0f}" + (f"(陣風 {f.gust.p50:.0f})" if f.gust else "")
        rows.append(
            [report._label(f.day), f.sky, report._range(f.tmax), report._range(f.tmin), f"{f.pop:.0%}",
             f"{f.precip.p50:.0f} / {f.precip.p90:.0f}", wind, f"{f.confidence_label} {f.confidence}"]
        )
    return rows


def _upper_rows(rows) -> list[list[str]]:
    out = [["日期", "250 風", "噴流軸", "輻散", "500 槽脊", "高度 m", "700 濕度", "850 溫/濕/風", "平流", "925 風", "K"]]
    for r in rows:
        low = "–" if r.t850 is None or r.rh850 is None else f"{r.t850:.0f}°C {r.rh850:.0f}% {report._wind(r.dir850, r.wind850)}"
        out.append(
            [report._label(r.day), report._wind(r.dir250, r.wind250), report._jet(r), report._signed(r.div250), report._flow(r),
             "–" if r.z500 is None else f"{r.z500:.0f}" + (" 副高" if r.subtropical_high else ""),
             "–" if r.rh700 is None else f"{r.rh700:.0f}%", low, report._signed(r.tadv850),
             report._wind(r.dir925, r.wind925), "–" if r.k is None else f"{r.k:.0f}"]
        )
    return out


def build_place(name: str, raw: dict, upper_raw: dict | None, days: int = 7) -> tuple[str, dict]:
    """由原始資料產生一個地點的 HTML 區塊與 JSON 資料。"""
    ens = analysis.parse(raw)
    forecast = analysis.daily_forecast(ens, days)
    if not forecast:
        raise ValueError("沒有可用的預報日")
    signal_list = analysis.signals(ens, days)
    upper_days = None
    if upper_raw:
        try:
            upper_days = upper.analyse(upper_raw, ens.meta.get("latitude") or 0.0, days)
        except (KeyError, IndexError, TypeError, ValueError):
            upper_days = None
    lat, lon = ens.meta.get("latitude") or 0.0, ens.meta.get("longitude") or 0.0
    counts = " + ".join(f"{s} {ens.member_count(s)}" for s in ens.systems)
    cards = "".join(
        f'<div class="day"><b>{esc(report._label(f.day))}</b><div class="sky">{esc(f.sky)}</div>'
        f'<div class="t">{f.tmax.p50:.0f}° <small>/ {f.tmin.p50:.0f}°</small></div>'
        f'<div class="p">降雨 {f.pop:.0%}</div><span class="conf {f.confidence_label}">信心{f.confidence_label}</span></div>'
        for f in forecast
    )
    parts = [
        f'<p class="sub">{abs(lat):.2f}°{"N" if lat >= 0 else "S"} {abs(lon):.2f}°{"E" if lon >= 0 else "W"} · '
        f"{esc(counts)} = {sum(ens.member_count(s) for s in ens.systems)} 個系集成員</p>",
        f'<div class="days">{cards}</div>',
        "<h2>重點</h2>" + _list(report.highlights(forecast)),
        '<h2>氣溫</h2><div class="card"><p class="sub">深色:低溫到高溫的中位數;淺色:10–90% 的可能範圍(°C)</p>'
        + _temperature_chart(forecast) + "</div>",
        '<h2>降雨機率</h2><div class="card"><p class="sub">日雨量達 1 mm 的系集成員比例</p>' + _rain_chart(forecast) + "</div>",
        '<h2>逐日預報</h2><div class="card">' + _table(_surface_rows(forecast)) + "</div>",
    ]
    if upper_days:
        parts += [
            "<h2>高空對地面的影響</h2>" + _list(report.upper_impact(upper_days, forecast, lat < 0)),
            '<h2>高空分析</h2><div class="card"><p class="sub">ECMWF 與 AIFS 系集中位數;風速 m/s,輻散 10⁻⁵/s,平流 °C/日</p>'
            + _table(_upper_rows(upper_days)) + "</div>",
        ]
    if signal_list:
        by_day: dict[str, list[str]] = {}
        for s in signal_list:
            by_day.setdefault(s.day, []).append(s.text)
        parts.append("<h2>天氣型態變化</h2>" + _list([f"{report._label(d)}:" + ";".join(t) + "。" for d, t in by_day.items()]))
    notes = report.uncertainty(forecast)
    if notes:
        parts.append("<h2>不確定性</h2>" + _list(notes))
    return "".join(parts), report.to_json(name, ens, forecast, signal_list, None, upper_days)


def build_page(sections: list[tuple[str, str]], generated: datetime, failed: list[str]) -> str:
    stamp = generated.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tabs = "".join(f'<button role="tab">{esc(name)}</button>' for name, _ in sections)
    body = "".join(f'<section class="place"><h1>{esc(name)} 一週天氣預報</h1>{block}</section>' for name, block in sections)
    missing = f'<p class="sub">本次未能更新:{esc("、".join(failed))}</p>' if failed else ""
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>一週天氣預報</title>'
        f"<style>{CSS}</style></head><body><main>"
        f'<nav role="tablist">{tabs}</nav>{body}{missing}'
        f'<footer>更新時間 {stamp} · 資料:Open-Meteo Ensemble API(ECMWF、GEFS、AIFS)· '
        "數值為系集中位數;信心與高空研判是經驗法則,不是官方預報,颱風與災害天氣請以中央氣象署為準 · "
        f'<a href="data.json">data.json</a> · <a href="{REPO_URL}">原始碼與說明</a></footer>'
        f'</main><div id="tip"></div><script>{JS}</script></body></html>'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekcast.site", description="產生靜態預報網頁")
    parser.add_argument("places", nargs="*", default=list(DEFAULT_PLACES), help="地名(預設:台北 台中 高雄 花蓮)")
    parser.add_argument("--out", type=Path, default=Path("_site"), help="輸出資料夾")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--from-file", metavar="PATH", help="改讀存下的原始資料(只產生一個地點,不連網)")
    args = parser.parse_args(argv)

    sections, data, failed = [], {}, []
    for name in args.places[:1] if args.from_file else args.places:
        try:
            if args.from_file:
                raw, upper_raw = client.split_bundle(client.load_raw(args.from_file))
            else:
                place = client.geocode(name)
                raw = client.fetch_ensemble(place.latitude, place.longitude, args.days)
                try:
                    upper_raw = client.fetch_upper(place.latitude, place.longitude, args.days, raw.get("timezone") or "GMT")
                except client.FetchError as err:
                    print(f"{name}:沒有高空資料({err})", file=sys.stderr)
                    upper_raw = None
            block, payload = build_place(name, raw, upper_raw, args.days)
        except (client.FetchError, ValueError, OSError) as err:
            print(f"{name}:略過({err})", file=sys.stderr)
            failed.append(name)
            continue
        sections.append((name, block))
        data[name] = payload
    if not sections:
        print("沒有任何地點成功,不產生網頁", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(build_page(sections, now, failed), encoding="utf-8")
    (args.out / "data.json").write_text(
        json.dumps({"generated": now.isoformat(), "places": data}, ensure_ascii=False), encoding="utf-8"
    )
    print(f"已產生 {args.out}/index.html({len(sections)} 個地點)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
