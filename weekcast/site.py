"""產生靜態預報網頁:python -m weekcast.site --out _site 台北 台中 高雄

輸出 index.html(所有地點在同一頁,以分頁切換)與 data.json(同一份資料的
機器可讀版本)。由 .github/workflows/pages.yml 定時執行並發布到 GitHub Pages。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import analysis, client, periods, report, upper

DEFAULT_PLACES = ("枋寮", "台北", "台中", "高雄", "花蓮")
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
nav .x{margin-left:4px;opacity:.6}nav .x:hover{opacity:1}
#find{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}
#find input{font:inherit;flex:1 1 220px;max-width:340px;padding:7px 12px;border:1px solid var(--axis);border-radius:8px;background:var(--surface);color:var(--ink)}
#find button,#hits button{font:inherit;padding:6px 14px;border:1px solid var(--axis);border-radius:8px;background:var(--surface);color:var(--ink);cursor:pointer}
#hits{display:flex;flex-wrap:wrap;gap:6px;flex-basis:100%}#fmsg{font-size:13px;color:var(--ink2)}
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
.grid td.cell{position:relative;min-width:120px}
.grid td.cell b{font-size:15px}.grid td.cell span{color:var(--ink2);margin-left:6px}
.grid td.cell i{position:absolute;left:0;bottom:0;height:3px;background:var(--series);border-radius:2px}
#radar .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
#radar button{font:inherit;font-size:13px;padding:4px 12px;border:1px solid var(--axis);border-radius:999px;background:var(--surface);color:var(--ink);cursor:pointer}
#radar button[aria-pressed=true]{background:var(--ink);color:var(--page);border-color:var(--ink)}
#rtime{font-size:13px;color:var(--ink2);margin-left:auto}
#map{height:440px;border-radius:8px;background:var(--grid)}
#cwa{display:none;max-width:100%;border-radius:8px}
footer{color:var(--ink2);font-size:12px;margin-top:32px}a{color:var(--series)}
"""

JS = """
const nav=document.querySelector('nav'),main=document.querySelector('main'),tip=document.getElementById('tip');
const tabs=()=>[...nav.querySelectorAll('button[role=tab]')],secs=()=>[...document.querySelectorAll('section.place')];
function show(i){tabs().forEach((b,j)=>b.setAttribute('aria-selected',i===j));secs().forEach((s,j)=>s.classList.toggle('on',i===j));
try{localStorage.setItem('place',i)}catch(e){}if(window.radarMove)radarMove();
const s=secs()[i];if(s&&s.dataset.pending)compute(s)}
nav.addEventListener('click',e=>{const x=e.target.closest('.x');if(x){drop(x.parentElement);return}
const b=e.target.closest('button[role=tab]');if(b)show(tabs().indexOf(b))});
document.addEventListener('mousemove',e=>{const el=e.target.closest&&e.target.closest('[data-tip]');
if(!el){tip.style.display='none';return}tip.textContent=el.dataset.tip;tip.style.display='block';
tip.style.left=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=(e.clientY+14)+'px'});

// ---- 自動更新:定時檢查有沒有新的預報,有就重新載入,不必手動重新整理 ----
const fresh=document.getElementById('fresh'),built=fresh.dataset.generated;
const local=t=>new Date(t).toLocaleString([], {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
function stampText(extra){fresh.textContent='資料更新時間 '+local(built)+'。本頁每 10 分鐘自動檢查,有新預報會自動更新,不必手動重新整理'+(extra||'。')}
stampText();
async function refresh(){try{const d=await(await fetch('data.json?t='+Date.now(),{cache:'no-store'})).json();
 if(d.generated&&d.generated!==built&&new Date(d.generated)>new Date(built)){location.replace(location.pathname+'?v='+Date.parse(d.generated));return}
 stampText('(上次檢查 '+local(Date.now())+')。')}catch(e){stampText('(剛才檢查失敗,稍後會再試;也可以手動重新整理)。')}}
setInterval(refresh,6e5);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});

// ---- 搜尋任意地點:在瀏覽器裡執行同一份 Python 分析程式 ----
const form=document.getElementById('find'),box=document.getElementById('q'),hits=document.getElementById('hits'),msg=document.getElementById('fmsg');
const esc=t=>t.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let py=null;
async function engine(){if(py)return py;py=(async()=>{
 const base='https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';
 const {loadPyodide}=await import(base+'pyodide.mjs');const p=await loadPyodide({indexURL:base});
 p.unpackArchive(await(await fetch('weekcast.zip')).arrayBuffer(),'zip');
 p.runPython('from weekcast.browser import forecast');return p})();
 py.catch(()=>{py=null});return py}
function saved(){try{return JSON.parse(localStorage.getItem('custom')||'[]')}catch(e){return []}}
function keep(list){try{localStorage.setItem('custom',JSON.stringify(list))}catch(e){}}
function addTab(name,lat,lon){
 const b=document.createElement('button');b.setAttribute('role','tab');b.dataset.custom='1';
 b.innerHTML=esc(name)+' <span class="x" title="移除" aria-label="移除">×</span>';nav.appendChild(b);
 const s=document.createElement('section');s.className='place';s.dataset.lat=lat;s.dataset.lon=lon;s.dataset.name=name;s.dataset.pending='1';
 s.innerHTML='<h1>'+esc(name)+' 一週天氣預報</h1><p class="sub wait"></p>';
 const last=secs().pop();last.after(s);return tabs().length-1}
function drop(b){const i=tabs().indexOf(b),s=secs()[i];keep(saved().filter(p=>!(p.name===s.dataset.name&&p.lat==s.dataset.lat)));
 b.remove();s.remove();show(0)}
async function compute(s){delete s.dataset.pending;const w=s.querySelector('.wait');
 try{w.textContent='正在載入分析程式(第一次約需 10–20 秒)…';const p=await engine();
  w.textContent='正在抓取 '+s.dataset.name+' 的系集資料並分析…';
  const html=await p.globals.get('forecast')(s.dataset.name,+s.dataset.lat,+s.dataset.lon);
  s.innerHTML='<h1>'+esc(s.dataset.name)+' 一週天氣預報</h1>'+html+'<p class="sub">這個地點是在你的瀏覽器裡即時計算的,重新整理頁面後點選分頁會再算一次。</p>'}
 catch(e){s.dataset.pending='1';w.textContent='無法取得預報:'+String(e.message||e).split('\\n').pop()+'。稍等一分鐘後再點一次分頁重試。'}}
function open(name,lat,lon){hits.innerHTML='';msg.textContent='';box.value='';
 const list=saved();if(!list.some(p=>p.name===name&&p.lat==lat)){list.push({name,lat,lon});keep(list)}
 show(addTab(name,lat,lon))}
form.addEventListener('submit',async e=>{e.preventDefault();const q=box.value.trim();hits.innerHTML='';if(!q)return;
 const m=q.match(/^(-?\\d+(?:\\.\\d+)?)\\s*[,,\\s]\\s*(-?\\d+(?:\\.\\d+)?)$/);
 if(m&&Math.abs(+m[1])<=90&&Math.abs(+m[2])<=180){open((+m[1]).toFixed(2)+', '+(+m[2]).toFixed(2),+m[1],+m[2]);return}
 msg.textContent='搜尋中…';
 try{const d=await(await fetch('https://photon.komoot.io/api/?limit=8&lat=23.7&lon=121&q='+encodeURIComponent(q))).json();
  const seen=new Set(),out=[];
  for(const f of d.features||[]){const p=f.properties,[lon,lat]=f.geometry.coordinates;
   const where=[p.city,p.county,p.state,p.country].filter((v,i,a)=>v&&v!==p.name&&a.indexOf(v)===i).join(' · ');
   const key=lat.toFixed(1)+','+lon.toFixed(1);if(seen.has(key))continue;seen.add(key);out.push({name:p.name||q,where,lat,lon})}
  if(!out.length){msg.textContent='找不到「'+q+'」。可以改輸入座標,例如 22.37,120.59';return}
  msg.textContent='請選擇:';
  out.slice(0,6).forEach(o=>{const b=document.createElement('button');b.type='button';
   b.textContent=o.name+(o.where?'('+o.where+')':'');b.addEventListener('click',()=>open(o.name,+o.lat.toFixed(4),+o.lon.toFixed(4)));hits.appendChild(b)})}
 catch(err){msg.textContent='地名搜尋服務暫時無法使用。可以改輸入座標,例如 22.37,120.59'}});
saved().forEach(p=>addTab(p.name,p.lat,p.lon));
let start=0;try{start=+localStorage.getItem('place')||0}catch(e){}show(start<tabs().length?start:0);
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


def _period_grid(rows) -> str:
    """每天一列、四個時段各一格;底部的橫條長度是降雨機率。"""
    head = "".join(f"<th>{name} {start:02d}–{end:02d}</th>" for name, start, end in periods.PERIODS)
    body = []
    for day, cells in periods.by_day(rows).items():
        tds = [f"<td>{esc(report._label(day))}</td>"]
        for name, _, _ in periods.PERIODS:
            c = cells.get(name)
            if c is None or c.temp is None or c.pop is None:
                tds.append("<td>–</td>")
                continue
            tip = (
                f"{report._label(day)} {name}\n氣溫 {c.temp.p50:.0f}°C(範圍 {c.temp.p10:.0f}–{c.temp.p90:.0f})\n"
                f"降雨機率 {c.pop:.0%}\n雨量中位數 {c.precip.p50:.1f} mm,偏多時 {c.precip.p90:.1f} mm"
            )
            tds.append(
                f'<td class="cell" data-tip="{esc(tip)}"><b>{c.temp.p50:.0f}°</b><span>{esc(c.sky)} {c.pop:.0%}</span>'
                f'<i style="width:{c.pop * 100:.0f}%"></i></td>'
            )
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="scroll"><table class="grid"><thead><tr><th>日期</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


SEARCH_HTML = """<form id="find" role="search"><input id="q" type="search" autocomplete="off"
placeholder="搜尋其他地點,例如 恆春、Tokyo 或 22.37,120.59" aria-label="搜尋地點"><button>查詢</button>
<span id="fmsg"></span><div id="hits"></div></form>"""

NOTES_HTML = """<section id="notes"><h2>注意事項</h2><div class="card"><ul>
<li><b>這不是官方預報。</b>颱風、豪雨、寒流等災害性天氣,請以中央氣象署的警特報為準。</li>
<li><b>越後面的日子越不準。</b>每天的「信心」是依各模式是否一致算出來的經驗分數,不是命中率;信心低代表模式之間看法分歧。</li>
<li><b>時段預報比整天的預報更容易錯開。</b>鋒面晚到半天,整天的預報仍然對,上午、下午卻會整個對調。三天以後的時段只當參考。</li>
<li><b>降雨機率高不代表雨大。</b>請一起看雨量,以及「高空對地面的影響」裡是否寫著淺層降雨。</li>
<li><b>解析度約 25 公里。</b>相鄰鄉鎮的結果會很接近;落山風、山區午後雷陣雨、海陸風這類局部現象,模式解析不出來。</li>
<li><b>氣溫沒有用測站觀測校正。</b>山區與海邊的誤差會比平地大。</li>
<li><b>接下來 1–3 小時會不會下雨,請看上方的即時雷達回波</b>,它比預報準;雷達圖來自外部服務,偶爾可能載入不到。</li>
<li><b>預設城市每 6 小時更新一次</b>(約台灣時間 03、09、15、21 時之後),有時會延後幾十分鐘。</li>
<li><b>用搜尋欄查的地點不會自動更新</b>,每次點開分頁時才重新計算,第一次約需 10–20 秒。</li>
</ul></div></section>"""

RADAR_HTML = """<section id="radar"><h2>即時雷達回波</h2><div class="card">
<div class="bar"><button data-src="rv" aria-pressed="true">全球合成</button><button data-src="jma">日本氣象廳</button>
<button data-src="cwa">中央氣象署</button><button id="rplay">暫停</button><span id="rtime"></span></div>
<div id="map"></div><img id="cwa" alt="中央氣象署雷達合成回波圖">
<p class="sub" id="rnote">最近約 2 小時的回波動畫,每 5 分鐘自動更新。雷達只能看出現在雨在哪裡、往哪裡移動,適合判斷未來 1–3 小時;更久之後請看上面的預報。</p>
<p class="sub">來源:全球合成為 <a href="https://www.rainviewer.com/">RainViewer</a>(彙整各國氣象單位的雷達);
<a href="https://www.jma.go.jp/bosai/nowc/">日本氣象廳</a>降水即時預報(涵蓋日本周邊,台灣只到東北部外海);
<a href="https://www.cwa.gov.tw/V8/C/W/OBS_Radar.html">中央氣象署</a>雷達合成回波圖。底圖 © OpenStreetMap。</p></div></section>"""

RADAR_JS = """
(function(){
const el=document.getElementById('map'),img=document.getElementById('cwa'),label=document.getElementById('rtime'),
note=document.getElementById('rnote'),play=document.getElementById('rplay');
if(!el)return;
if(!window.L){el.textContent='地圖元件載入失敗';return}
const here=()=>{const s=document.querySelector('section.place.on');return [+s.dataset.lat,+s.dataset.lon]};
const map=L.map(el,{scrollWheelZoom:false}).setView(here(),7);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:10,attribution:'&copy; OpenStreetMap'}).addTo(map);
const pin=L.circleMarker(here(),{radius:6,weight:2,color:'#0b0b0b',fillColor:'#fff',fillOpacity:1}).addTo(map);
window.radarMove=()=>{map.setView(here(),map.getZoom());pin.setLatLng(here())};
let src='rv',frames=[],layers=[],at=0,running=true,timer=null;
const sources={
 rv:async()=>{const d=await(await fetch('https://api.rainviewer.com/public/weather-maps.json')).json();
  return d.radar.past.map(f=>({t:f.time*1000,url:d.host+f.path+'/256/{z}/{x}/{y}/2/1_1.png',opt:{maxNativeZoom:7}}))},
 jma:async()=>{const d=await(await fetch('https://www.jma.go.jp/bosai/jmatile/data/nowc/targetTimes_N1.json')).json();
  const ts=s=>Date.UTC(+s.slice(0,4),+s.slice(4,6)-1,+s.slice(6,8),+s.slice(8,10),+s.slice(10,12));
  return d.filter((f,i)=>i%2===0).slice(0,13).reverse().map(f=>({t:ts(f.validtime),
   url:'https://www.jma.go.jp/bosai/jmatile/data/nowc/'+f.basetime+'/none/'+f.validtime+'/surf/hrpns/{z}/{x}/{y}.png',
   opt:{minNativeZoom:4,maxNativeZoom:10}}))}};
function draw(){layers.forEach((l,i)=>l.setOpacity(i===at?0.7:0));
 if(frames[at])label.textContent=new Date(frames[at].t).toLocaleString([], {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}
function tick(){if(!frames.length)return;at=(at+1)%frames.length;draw();
 timer=setTimeout(tick,at===frames.length-1?1800:450)}
function stop(){clearTimeout(timer);timer=null}
async function load(){stop();layers.forEach(l=>map.removeLayer(l));layers=[];frames=[];
 if(src==='cwa'){el.style.display='none';img.style.display='block';play.style.display='none';
  img.src='https://www.cwa.gov.tw/Data/radar/CV1_3600.png?t='+Math.floor(Date.now()/3e5);label.textContent='';return}
 el.style.display='';img.style.display='none';play.style.display='';map.invalidateSize();
 try{frames=await sources[src]()}catch(e){label.textContent='雷達資料暫時無法取得';return}
 layers=frames.map(f=>L.tileLayer(f.url,Object.assign({opacity:0,maxZoom:10,zIndex:5},f.opt)).addTo(map));
 at=frames.length-1;draw();if(running)timer=setTimeout(tick,1800)}
img.onerror=()=>{img.style.display='none';label.textContent='無法直接載入中央氣象署的圖,請點下方連結到官網查看'};
document.querySelectorAll('#radar button[data-src]').forEach(b=>b.addEventListener('click',()=>{src=b.dataset.src;
 document.querySelectorAll('#radar button[data-src]').forEach(x=>x.setAttribute('aria-pressed',x===b));load()}));
play.addEventListener('click',()=>{running=!running;play.textContent=running?'暫停':'播放';stop();if(running)tick()});
load();setInterval(load,3e5);
})();
"""


def build_place(name: str, raw: dict, upper_raw: dict | None, days: int = 7, period_raw: dict | None = None) -> tuple[str, dict]:
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
    period_list = None
    if period_raw:
        try:
            period_list = periods.period_forecast(period_raw, days)
        except (KeyError, IndexError, TypeError, ValueError):
            period_list = None
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
        (
            '<h2>時段預報</h2><div class="card"><p class="sub">每格是該時段的氣溫、天氣與 6 小時內降雨機率,'
            "底部橫條越長降雨機率越高;越後面的日子,時段的可信度越低</p>" + _period_grid(period_list) + "</div>"
        )
        if period_list
        else "",
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
    data = report.to_json(name, ens, forecast, signal_list, None, upper_days, period_list)
    return "".join(parts), data


def build_page(sections: list[tuple], generated: datetime, failed: list[str]) -> str:
    """sections 的每一項是 (地名, HTML 區塊) 或 (地名, HTML 區塊, 緯度, 經度);有座標才會附雷達圖。"""
    sections = [(s[0], s[1], *(s[2:4] if len(s) >= 4 else (None, None))) for s in sections]
    radar = all(lat is not None for _, _, lat, _ in sections)
    stamp = generated.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    iso = generated.astimezone(timezone.utc).isoformat()
    tabs = "".join(f'<button role="tab">{esc(s[0])}</button>' for s in sections)
    body = "".join(
        f'<section class="place"' + (f' data-lat="{lat}" data-lon="{lon}"' if radar else "") + f"><h1>{esc(name)} 一週天氣預報</h1>{block}</section>"
        for name, block, lat, lon in sections
    )
    leaflet = "https://unpkg.com/leaflet@1.9.4/dist/leaflet"
    missing = f'<p class="sub">本次未能更新:{esc("、".join(failed))}</p>' if failed else ""
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>一週天氣預報</title>'
        + (f'<link rel="stylesheet" href="{leaflet}.css">' if radar else "")
        + f"<style>{CSS}</style></head><body><main>"
        f'<nav role="tablist">{tabs}</nav>{SEARCH_HTML}<p class="sub" id="fresh" data-generated="{iso}">資料更新時間 {stamp}</p>{body}{RADAR_HTML if radar else ""}{NOTES_HTML}{missing}'
        f'<footer>資料:Open-Meteo Ensemble API(ECMWF、GEFS、AIFS)· '
        "數值為系集中位數;信心與高空研判是經驗法則,不是官方預報,颱風與災害天氣請以中央氣象署為準 · "
        f'<a href="data.json">data.json</a> · <a href="{REPO_URL}">原始碼與說明</a></footer>'
        '</main><div id="tip"></div>'
        + (f'<script src="{leaflet}.js"></script>' if radar else "")
        + f"<script>{JS}{RADAR_JS if radar else ''}</script></body></html>"
    )


def write_engine(path: Path) -> None:
    """把分析程式打包,讓網頁的搜尋功能在瀏覽器裡執行同一份程式。"""
    source = Path(__file__).parent
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(source.glob("*.py")):
            archive.write(file, f"weekcast/{file.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekcast.site", description="產生靜態預報網頁")
    parser.add_argument("places", nargs="*", default=list(DEFAULT_PLACES), help="地名(預設:枋寮 台北 台中 高雄 花蓮)")
    parser.add_argument("--out", type=Path, default=Path("_site"), help="輸出資料夾")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--from-file", metavar="PATH", help="改讀存下的原始資料(只產生一個地點,不連網)")
    args = parser.parse_args(argv)

    sections, data, failed = [], {}, []
    for name in args.places[:1] if args.from_file else args.places:
        try:
            if args.from_file:
                bundle = client.load_raw(args.from_file)
                raw, upper_raw = client.split_bundle(bundle)
                period_raw = bundle.get("periods")
            else:
                place = client.geocode(name)
                raw = client.fetch_ensemble(place.latitude, place.longitude, args.days)
                try:
                    upper_raw = client.fetch_upper(place.latitude, place.longitude, args.days, raw.get("timezone") or "GMT")
                except client.FetchError as err:
                    print(f"{name}:沒有高空資料({err})", file=sys.stderr)
                    upper_raw = None
                try:
                    period_raw = client.fetch_periods(place.latitude, place.longitude, args.days, raw.get("timezone") or "GMT")
                except client.FetchError as err:
                    print(f"{name}:沒有時段資料({err})", file=sys.stderr)
                    period_raw = None
            block, payload = build_place(name, raw, upper_raw, args.days, period_raw)
        except (client.FetchError, ValueError, OSError) as err:
            print(f"{name}:略過({err})", file=sys.stderr)
            failed.append(name)
            continue
        sections.append((name, block, payload["location"].get("latitude") or 0.0, payload["location"].get("longitude") or 0.0))
        data[name] = payload
    if not sections:
        print("沒有任何地點成功,不產生網頁", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(build_page(sections, now, failed), encoding="utf-8")
    write_engine(args.out / "weekcast.zip")
    (args.out / "data.json").write_text(
        json.dumps({"generated": now.isoformat(), "places": data}, ensure_ascii=False), encoding="utf-8"
    )
    print(f"已產生 {args.out}/index.html({len(sections)} 個地點)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
