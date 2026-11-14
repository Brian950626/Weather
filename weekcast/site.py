"""產生靜態預報網頁:python -m weekcast.site --out _site 台北 台中 高雄

輸出 index.html、data.json(同一份資料的機器可讀版本)與 weekcast.zip(讓搜尋功能在
瀏覽器裡執行同一份分析程式)。由 .github/workflows/pages.yml 定時執行並發布到
GitHub Pages。

網頁只有一個檔案,但用網址(#/overview、#/place/0/summary …)切換成幾個獨立的頁面:

- 總覽:先講結論,加上所有地點的逐日預報表
- 地點預報:上方是一週的時間軸,下面依「讀者想問的問題」分成六個分頁,順序就是
  課堂預報報告的章節(摘要 → 逐日 → 綜觀分析 → 模式比較 → 不確定性 → 校驗)
- 即時雷達
- 說明:怎麼閱讀、資料來源、名詞解釋、注意事項

data.json 另外帶著最近幾次執行的預報摘要(runs)。下次執行時用 --previous 把上一次
發布的 data.json 讀回來,用來比較預報趨勢,以及校驗已經過去的日子。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import analysis, briefing, client, history, periods, plain, report, upper, verify
from .towns import TOWNS

DEFAULT_PLACES = ("枋寮", "台北", "台中", "高雄", "花蓮")
KEEP_RUNS = 40  # data.json 裡每個地點保留幾次執行的摘要(每 6 小時一次,約 10 天,校驗要用)
REPO_URL = "https://github.com/Brian950626/Weather"
TYPHOON_URL = "https://lkt0713.github.io/Typhoon-Forecast/"
ORDINALS = "一二三四五六七八九"
TABS = (
    ("summary", "摘要"),
    ("daily", "逐日與時段"),
    ("synoptic", "綜觀分析"),
    ("models", "模式與系集"),
    ("risk", "不確定性"),
    ("verify", "校驗"),
    ("plain", "白話總結"),
)
FONTS = "https://fonts.googleapis.com/css2?family=LXGW+WenKai+TC:wght@400;700&display=swap"

# 配色取自手繪天氣圖的習慣:降雨區塗綠、暖色用紅、冷色用藍、槽線用褐色。
CSS = """
:root{--paper:#f1f4f1;--sheet:#fcfdfb;--ink:#17201c;--ink2:#44524b;--muted:#69766e;--line:#d3dbd5;
--sun:#e39a0c;--cloud:#a9b6af;--cloud-dark:#6d7a73;--rain:#2c7a57;--rain-soft:#d2e7da;--warm:#bf4226;--warm-soft:#f3d8ce;--cold:#2a5ca6;--cold-soft:#d5e0f1;--ochre:#8a5f12;--ochre-soft:#f0e4c6;
--kai:"LXGW WenKai TC","BiauKai","DFKai-SB","Kaiti TC",KaiTi,serif;
--sans:system-ui,-apple-system,"PingFang TC","Microsoft JhengHei","Noto Sans TC",sans-serif}
@media (prefers-color-scheme:dark){:root{--paper:#121815;--sheet:#1a221e;--ink:#e8eee9;--ink2:#b9c5bd;--muted:#93a198;--line:#2f3a34;
--sun:#f3c04a;--cloud:#a3b1a9;--cloud-dark:#70807a;--rain:#5dbf93;--rain-soft:#1f4a37;--warm:#f08462;--warm-soft:#5a2a1d;--cold:#7fa8ea;--cold-soft:#22375c;--ochre:#d8ae55;--ochre-soft:#4a3a16}}
*{box-sizing:border-box}
html{scroll-padding-top:60px}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.7 var(--sans);font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration-color:var(--rain);text-decoration-thickness:2px;text-underline-offset:3px}
a:hover{color:var(--rain)}
:focus-visible{outline:2px solid var(--cold);outline-offset:2px}
h1,h2,h3,.lede,.brand{font-family:var(--kai);font-weight:700;letter-spacing:.02em}
h1{font-size:34px;line-height:1.25;margin:0 0 6px}
h2{font-size:23px;line-height:1.35;margin:40px 0 10px}
h3{font-size:18px;line-height:1.4;margin:22px 0 6px}
p{margin:8px 0}
.lede{font-size:21px;line-height:1.65;max-width:38em;margin:10px 0 18px}
.sub{color:var(--ink2);font-size:14px;margin:4px 0;max-width:46em}
ul{margin:8px 0;padding-left:22px;max-width:46em}li{margin:5px 0}
.top{background:var(--sheet);border-bottom:2px solid var(--ink)}
.top .in{max-width:1120px;margin:0 auto;padding:0 20px;display:flex;align-items:baseline;column-gap:28px;flex-wrap:wrap}
.brand{font-size:22px;padding:13px 0 11px;text-decoration:none}
.nav{display:flex;gap:2px}
.nav a{padding:15px 12px 11px;text-decoration:none;border-bottom:4px solid transparent;color:var(--ink2);white-space:nowrap}
.nav a[aria-current=page]{color:var(--ink);border-bottom-color:var(--rain);font-weight:600}
#fresh{margin:0 0 0 auto;padding:6px 0;font-size:13px;color:var(--muted);max-width:none}
main{max-width:1120px;margin:0 auto;padding:30px 20px 72px}
.js .page:not(.on),.js article.place:not(.on),.js .view:not(.on){display:none}
.sheet{background:var(--sheet);border:1px solid var(--line);border-radius:4px;padding:16px 18px;margin:10px 0}
.week{background:var(--sheet);border:1px solid var(--line);border-left:6px solid var(--rain);border-radius:4px;padding:14px 20px 12px;margin:6px 0 22px;max-width:52em}
.week .wk{font-size:13px;color:var(--muted);margin:0;letter-spacing:.08em}
.week .wh{font-family:var(--kai);font-weight:700;font-size:24px;line-height:1.45;margin:2px 0 8px}
.week ul{margin:6px 0 10px}
.wkd{margin:8px 0}.wkd>summary{cursor:pointer;font-weight:600;padding:6px 0}.wkd[open]>summary{color:var(--muted);font-weight:400}
.wkd .week{margin:4px 0 10px}
.pd{border-top:1px solid var(--line);padding:10px 0}.pd:first-child{border-top:0}.pd h3{margin:0 0 4px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.55}
th,td{padding:9px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
thead th{font-weight:600;color:var(--ink2);border-bottom:2px solid var(--ink);white-space:nowrap}
tbody th{font-weight:600;white-space:nowrap}
tbody tr:last-child>*{border-bottom:0}
table.tight td,table.tight th,table.models td:not(:last-child){white-space:nowrap}
table.models td:last-child{min-width:16em}
table.zone td{white-space:nowrap}table.zone td:nth-child(2){white-space:normal;min-width:8em}
td small,th small{color:var(--ink2);font-size:13px}
caption{caption-side:bottom;text-align:left;color:var(--ink2);font-size:13px;padding:8px 12px 0}
.wx{font-family:var(--kai);font-weight:700;font-size:16px}
.ribwrap{overflow-x:auto;margin:6px 0 4px}
.ribbon{display:grid;grid-template-columns:repeat(var(--n),minmax(94px,1fr));min-width:calc(var(--n)*94px);background:var(--sheet);border:1px solid var(--line);border-radius:4px}
.stage{padding:7px 12px 9px;border-top:5px solid var(--ochre);border-bottom:1px solid var(--line);font-family:var(--kai);font-weight:700;line-height:1.4;background:var(--ochre-soft)}
.stage.wet{border-top-color:var(--rain);background:var(--rain-soft)}
.stage small{display:block;font:400 12px/1.5 var(--sans);color:var(--ink2)}
.stage small.say{font-size:13px;margin-top:2px}
.zone small{display:block;font-size:12px;line-height:1.45;color:var(--muted);font-weight:400;max-width:17em;white-space:normal}
.swipe{display:none;font-size:13px;color:var(--muted);margin:4px 0}
.rel{display:inline-block;font:600 12px/1.5 var(--sans);font-style:normal;color:var(--sheet);background:var(--ink);border-radius:3px;padding:0 5px;margin-left:6px;vertical-align:middle}
.past{opacity:.5}.past .rel{background:var(--muted)}
#fresh.stale{color:var(--warm);font-weight:600}
details.nums dl{margin:6px 0;display:grid;grid-template-columns:max-content 1fr;gap:4px 14px}
details.nums dt{font-weight:600;color:var(--ink);white-space:nowrap}details.nums dd{margin:0}
.legend .lg{display:inline-block;width:92px;height:10px;border-radius:2px;margin-right:8px;vertical-align:middle;background:linear-gradient(90deg,#a9e3ff,#1877d2 45%,#f2d53c 72%,#e03a2c)}
.stage:not(.s1),.d:not(.d1){border-left:1px solid var(--line)}
.d{padding:10px 6px 12px;display:flex;flex-direction:column;align-items:center;gap:5px;text-align:center}
.d b{font-size:15px}.d b span{color:var(--muted);font-weight:400;margin-left:4px}
.d .wx{min-height:1.7em}
.tcol{height:104px;display:flex;flex-direction:column;align-items:center;font-size:14px;line-height:1.5}
.tcol s{display:block;min-height:0}
.tcol i{display:block;width:12px;min-height:6px;border-radius:2px;background:linear-gradient(var(--warm),var(--cold))}
.rcol{width:54px;height:38px;display:flex;align-items:flex-end;border-bottom:1px solid var(--ink2)}
.rcol i{display:block;width:100%;background:var(--rain)}
.pop{font-size:14px;font-weight:600}
.conf{display:inline-flex;align-items:center;gap:2px;font-size:13px;color:var(--ink2);white-space:nowrap}
.conf i{width:6px;height:11px;background:var(--line)}
.conf.c1 i:nth-child(-n+1),.conf.c2 i:nth-child(-n+2),.conf.c3 i:nth-child(-n+3),.conf.c4 i:nth-child(-n+4),.conf.c5 i{background:var(--ink)}
.conf em{font-style:normal;margin-left:5px}
.d .conf{flex-wrap:wrap;justify-content:center;row-gap:3px;white-space:normal}.d .conf em{flex-basis:100%;margin-left:0}
.key{font-size:13px;color:var(--ink2);margin:6px 0 0}
.tabs{position:sticky;top:0;z-index:5;background:var(--paper);display:flex;gap:2px;overflow-x:auto;border-bottom:1px solid var(--ink);margin:22px 0 0}
.tabs button{font:inherit;background:none;border:0;border-bottom:4px solid transparent;padding:12px 14px 8px;color:var(--ink2);cursor:pointer;white-space:nowrap}
.tabs button[aria-selected=true]{color:var(--ink);font-weight:600;border-bottom-color:var(--ink)}
.view>h2:first-child{margin-top:26px}
#places{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
#places button[role=tab],#hits button,#radar button{font:inherit;font-size:15px;padding:5px 13px;border:1px solid var(--ink2);border-radius:4px;background:transparent;color:var(--ink);cursor:pointer}
#places button[aria-selected=true],#radar button[aria-pressed=true]{background:var(--ink);color:var(--sheet);border-color:var(--ink)}
#places .x{margin-left:6px;opacity:.7}#places .x:hover{opacity:1}
#find{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0 22px}
#q{font:inherit;font-size:15px;padding:6px 10px;border:1px solid var(--ink2);border-radius:4px;background:var(--sheet);color:var(--ink);width:min(330px,100%)}
#find>button{font:inherit;font-size:15px;padding:6px 14px;border:1px solid var(--ink);border-radius:4px;background:var(--ink);color:var(--sheet);cursor:pointer}
#fmsg{font-size:14px;color:var(--ink2)}#hits{display:flex;flex-wrap:wrap;gap:6px;flex-basis:100%}
.stages{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));background:var(--sheet);border:1px solid var(--line);border-radius:4px;margin:10px 0}
.stg{padding:14px 18px 16px;border-top:5px solid var(--ochre)}
.stg.wet{border-top-color:var(--rain)}
.stg .when{font-size:13px;color:var(--ink2);margin:0}
.stg h3{margin:2px 0 8px;font-size:20px}
.stg p{margin:6px 0;font-size:15px}
.stg .why{font-size:13px;color:var(--ink2)}
.reason{margin:14px 0 22px;padding-left:16px;border-left:5px solid var(--ochre)}
.reason.wet{border-left-color:var(--rain)}
.reason h3{margin-top:0}
.reason dl{margin:6px 0;display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;font-size:15px;max-width:60em}
.reason dt{font-weight:600;white-space:nowrap}.reason dd{margin:0}
details.how{font-size:14px;color:var(--ink2);margin:8px 0;max-width:46em}
details.how summary{cursor:pointer;color:var(--ink)}
.verdict-ok{color:var(--rain);font-weight:600}.verdict-miss{color:var(--warm);font-weight:600}.verdict-fa{color:var(--ochre);font-weight:600}
svg{display:block;min-width:620px;width:100%}
svg.sprite{position:absolute;min-width:0;width:0;height:0}
svg.ico{display:inline-block;min-width:0;width:1.7em;height:1.7em;vertical-align:-.5em;margin-right:.25em;flex:none;overflow:visible}
.d .wx .ico{display:block;width:44px;height:44px;margin:0 auto 2px}
.zone .wx .ico{width:30px;height:30px;vertical-align:-9px}
.grid td.cell .ico{width:24px;height:24px;vertical-align:-7px}
.pd h3 .ico{width:30px;height:30px;vertical-align:-8px}
.icons{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px 18px;max-width:60em;margin:10px 0}
.icons div{display:grid;grid-template-columns:44px 1fr;column-gap:10px;align-items:center}
.icons .ico{grid-row:span 2;width:44px;height:44px;margin:0}.icons span{font-size:13px;color:var(--ink2)}
#map svg{display:inline;min-width:0;width:auto}
svg text{font:12px var(--sans);fill:var(--muted)}
svg .val{fill:var(--ink2)}svg .gridline{stroke:var(--line)}
svg .hi{fill:var(--warm-soft);stroke:var(--warm)}svg .lo{fill:var(--cold-soft);stroke:var(--cold)}svg .rn{fill:var(--rain-soft);stroke:var(--rain)}
svg .whi{stroke:var(--warm)}svg .wlo{stroke:var(--cold)}svg .wrn{stroke:var(--rain)}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--sheet);padding:6px 10px;border-radius:4px;font-size:13px;line-height:1.5;white-space:pre;display:none;z-index:9}
.grid td.cell{position:relative;min-width:128px;white-space:nowrap}
.grid td.cell b{font-size:15px}.grid td.cell span{color:var(--ink2);margin-left:6px}
.grid td.cell i{position:absolute;left:0;bottom:0;height:3px;background:var(--rain)}
#radar .bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px}
#rtime{font-size:14px;color:var(--ink2);margin-left:auto}
#map{height:min(64vh,520px);min-height:340px;border-radius:4px;background:var(--line)}
#cwa{display:none;max-width:100%;border-radius:4px}
.wait{font-size:16px;color:var(--ink2);margin:30px 0}
footer{color:var(--ink2);font-size:13px;margin-top:56px;padding-top:14px;border-top:1px solid var(--line);max-width:60em}
@media (max-width:760px){
h1{font-size:28px}.lede{font-size:19px}h2{font-size:21px}
.top .in{padding:0 14px}main{padding:22px 14px 104px}
#fresh{margin:0;padding:0 0 10px;flex-basis:100%}
.nav{position:fixed;left:0;right:0;bottom:0;z-index:8;background:var(--sheet);border-top:2px solid var(--ink);justify-content:space-around;padding-bottom:env(safe-area-inset-bottom)}
.nav a{flex:1;text-align:center;padding:9px 4px 11px;border-bottom:0;border-top:4px solid transparent;margin-top:-2px;font-size:15px}
.nav a[aria-current=page]{border-top-color:var(--rain)}
.reason dl{grid-template-columns:1fr}.reason dt{margin-top:6px}
details.nums dl{grid-template-columns:1fr}details.nums dt{margin-top:6px}
/* 手機:分頁列換行,不再往右捲到看不見;時間軸改成每天一列 */
.tabs{position:static;flex-wrap:wrap;overflow:visible}.tabs button{padding:9px 10px 5px;font-size:15px}
.ribbon{grid-template-columns:1fr;min-width:0}
.stage{grid-column:auto!important;order:var(--o);border-left:0!important}
.d{order:var(--o);display:grid;grid-template-columns:auto 1fr auto;grid-template-areas:"date wx temp" "pop pop conf";align-items:center;gap:2px 12px;text-align:left;padding:9px 12px;border-left:0!important;border-bottom:1px solid var(--line)}
.d b{grid-area:date}.d .wx{grid-area:wx;min-height:0;font-weight:600;white-space:nowrap}
.d .wx .ico{display:inline-block;width:30px;height:30px;margin:0 6px 0 0;vertical-align:-9px}.d .pop{grid-area:pop}
.tcol{grid-area:temp;height:auto;flex-direction:row;gap:3px;font-size:15px}.tcol s,.tcol i,.rcol{display:none}
.tcol span:last-of-type::before{content:"/ ";color:var(--muted)}
.d .conf{grid-area:conf;flex-wrap:nowrap;justify-self:end}.d .conf em{flex-basis:auto;margin-left:5px}
.swipe{display:block}.zone th:nth-child(2),.zone td:nth-child(2),.wide{display:none}
.week .wh{font-size:21px}
}
@media (min-width:761px){.stg+.stg{border-left:1px solid var(--line)}}
"""

JS = """
const pages=[...document.querySelectorAll('.page')],links=[...document.querySelectorAll('.nav a')],chips=document.getElementById('places'),tip=document.getElementById('tip');
const TABS=__TABS__,TITLES={overview:'總覽',place:'地點預報',radar:'即時雷達',about:'說明'};
const tabs=()=>[...chips.querySelectorAll('button[role=tab]')],secs=()=>[...document.querySelectorAll('article.place')];
let cur=0,tab=TABS[0],lastPage=null;
try{cur=+localStorage.getItem('place')||0}catch(e){}
function parse(){const p=location.hash.replace(/^#\\/?/,'').split('/');return {page:TITLES[p[0]]?p[0]:'overview',i:p[1],tab:p[2]}}
function go(page,i,t){const h='#/'+page+(page==='place'?'/'+i+'/'+t:'');if(location.hash===h)route();else location.hash=h}
// 只顯示目前的地點與分頁;用搜尋加入、還沒算過的地點在這時才計算
function paint(){const list=secs();if(cur>=list.length||cur<0)cur=0;
 tabs().forEach((b,j)=>b.setAttribute('aria-selected',cur===j));
 list.forEach((s,j)=>{s.classList.toggle('on',cur===j);
  s.querySelectorAll('.view').forEach(v=>v.classList.toggle('on',v.dataset.view===tab));
  s.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-selected',b.dataset.tab===tab))});
 try{localStorage.setItem('place',cur)}catch(e){}
 links.forEach(a=>{if(a.dataset.page==='place')a.href='#/place/'+cur+'/'+tab});
 const s=list[cur];if(s&&s.dataset.pending&&parse().page==='place')compute(s)}
function route(){const r=parse();
 pages.forEach(s=>s.classList.toggle('on',s.dataset.page===r.page));
 links.forEach(a=>{if(a.dataset.page===r.page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')});
 if(r.page==='place'){if(r.i!==undefined&&r.i!=='')cur=+r.i||0;tab=TABS.includes(r.tab)?r.tab:TABS[0]}
 paint();
 const here=secs()[cur];
 document.title=(r.page==='place'&&here?here.dataset.name+' ':'')+TITLES[r.page]+'|一週天氣預報';
 if(r.page==='radar'&&window.radarShow)radarShow();
 if(lastPage!==null&&lastPage!==r.page)window.scrollTo(0,0);lastPage=r.page}
window.addEventListener('hashchange',route);
chips.addEventListener('click',e=>{const x=e.target.closest('.x');if(x){drop(x.parentElement);return}
 const b=e.target.closest('button[role=tab]');if(b)go('place',tabs().indexOf(b),tab)});
document.addEventListener('click',e=>{const b=e.target.closest&&e.target.closest('.tabs button');if(b)go('place',cur,b.dataset.tab)});
document.addEventListener('mousemove',e=>{const el=e.target.closest&&e.target.closest('[data-tip]');
if(!el){tip.style.display='none';return}tip.textContent=el.dataset.tip;tip.style.display='block';
tip.style.left=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=(e.clientY+14)+'px'});

// ---- 自動更新:定時檢查有沒有新的預報,有就重新載入,不必手動重新整理 ----
const fresh=document.getElementById('fresh'),built=fresh.dataset.generated;
const local=t=>new Date(t).toLocaleString('zh-TW',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
// 寫出資料是多久以前的;排程偶爾會延後或跳過,超過 9 小時就提醒內容可能過時
function stampText(extra){const h=(Date.now()-new Date(built))/36e5,old=h>=9;
 const ago=h<1?'不到 1 小時前':'約 '+Math.round(h)+' 小時前';
 fresh.classList.toggle('stale',old);
 fresh.textContent=(old?'資料已 '+Math.round(h)+' 小時沒有更新,內容可能過時。':'')+'更新於 '+local(built)+'('+ago+')'+(extra||'')}
stampText();
// 依讀者裝置的日期標出今天、明天,已經過去的日子變淡
function markDays(){const iso=d=>d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
 const now=new Date(),today=iso(now),next=iso(new Date(now.getFullYear(),now.getMonth(),now.getDate()+1));
 document.querySelectorAll('[data-day]').forEach(el=>{const d=el.dataset.day,word=d===today?'今天':d===next?'明天':d<today?'已過':'';
  el.classList.toggle('past',d<today);el.classList.toggle('today',d===today);
  const at=el.matches('[data-rel]')?el:el.querySelector('[data-rel]');if(!at)return;
  let tag=at.querySelector('.rel');if(!word){if(tag)tag.remove();return}
  if(!tag){tag=document.createElement('em');tag.className='rel';at.appendChild(tag)}tag.textContent=word})}
markDays();setInterval(markDays,6e5);
async function refresh(){try{const d=await(await fetch('data.json?t='+Date.now(),{cache:'no-store'})).json();
 if(d.generated&&d.generated!==built&&new Date(d.generated)>new Date(built)){location.replace(location.pathname+'?v='+Date.parse(d.generated)+location.hash);return}
 stampText(',上次檢查 '+local(Date.now()))}catch(e){stampText(',剛才檢查失敗,稍後再試')}}
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
 b.innerHTML=esc(name)+' <span class="x" title="移除" aria-label="移除">×</span>';chips.appendChild(b);
 const s=document.createElement('article');s.className='place';s.dataset.lat=lat;s.dataset.lon=lon;s.dataset.name=name;s.dataset.pending='1';
 s.innerHTML='<h1>'+esc(name)+'</h1><p class="wait"></p>';
 secs().pop().after(s);return tabs().length-1}
function drop(b){const i=tabs().indexOf(b),s=secs()[i];keep(saved().filter(p=>!(p.name===s.dataset.name&&p.lat==s.dataset.lat)));
 b.remove();s.remove();go('place',0,TABS[0])}
async function compute(s){delete s.dataset.pending;const w=s.querySelector('.wait');
 try{w.textContent='正在載入分析程式,第一次約需 10–20 秒。';const p=await engine();
  w.textContent='正在抓取 '+s.dataset.name+' 的系集資料並分析。';
  const html=await p.globals.get('forecast')(s.dataset.name,+s.dataset.lat,+s.dataset.lon);
  s.innerHTML='<h1>'+esc(s.dataset.name)+'</h1>'+html;paint();markDays()}
 catch(e){s.dataset.pending='1';w.textContent='無法取得預報:'+explain(e)+'請再點一次這個地點重試。'}}
// 把程式或瀏覽器的英文錯誤訊息換成看得懂的中文
function explain(e){const t=String(e&&e.message||e);
 if(/minutely|hourly/i.test(t)&&/limit/i.test(t))return '氣象資料來源暫時忙線(短時間內查詢太多次),等一分鐘。';
 if(/daily/i.test(t)&&/limit/i.test(t))return '今天向氣象資料來源查詢的次數已達上限,明天才能再查。';
 if(/failed to fetch|networkerror|load failed|network/i.test(t))return '連不上網路或資料來源,請確認網路連線。';
 if(/pyodide|import|module|wasm/i.test(t))return '分析程式沒有載入成功,可能是網路較慢。';
 if(/沒有可用|格式|回應/.test(t))return '這個地點沒有可用的預報資料。';
 return '發生預期外的狀況。'}
function open(name,lat,lon){hits.innerHTML='';msg.textContent='';box.value='';
 const list=saved();if(!list.some(p=>p.name===name&&p.lat==lat)){list.push({name,lat,lon});keep(list)}
 go('place',addTab(name,lat,lon),TABS[0])}
// 先查內建的臺灣鄉鎮市區清單(不連網、一定是中文);查不到才問外部的地名服務
const TOWNS=__TOWNS__,norm=t=>t.trim().replace(/臺/g,'台').replace(/\\s+/g,'');
function localHits(text){const q=norm(text);if(!q)return [];const out=[];
 for(const [county,town,lat,lon] of TOWNS){const base=town.slice(0,-1),c=county.slice(0,-1);
  const forms=[town,base,county+town,county+base,c+town,c+base];
  const rank=forms.includes(q)?0:(town.startsWith(q)||(county+town).startsWith(q))?1:(county+town).includes(q)?2:-1;
  if(rank>=0)out.push({rank,name:county+town,where:'',lat,lon})}
 return out.sort((a,b)=>a.rank-b.rank).slice(0,8)}
function offer(list,note){hits.innerHTML='';msg.textContent=note;
 list.forEach(o=>{const b=document.createElement('button');b.type='button';
  b.textContent=o.name+(o.where?'('+o.where+')':'');b.addEventListener('click',()=>open(o.name,+(+o.lat).toFixed(4),+(+o.lon).toFixed(4)));hits.appendChild(b)})}
// 邊打邊列出符合的鄉鎮;注音或倉頡還在選字時先不動
box.addEventListener('input',e=>{if(e.isComposing)return;const found=localHits(box.value);
 if(found.length)offer(found,'請選擇:');else{hits.innerHTML='';msg.textContent=''}});
box.addEventListener('compositionend',()=>{const found=localHits(box.value);if(found.length)offer(found,'請選擇:')});
form.addEventListener('submit',async e=>{e.preventDefault();const q=box.value.trim();hits.innerHTML='';if(!q)return;
 const m=q.match(/^(-?\\d+(?:\\.\\d+)?)\\s*[,,\\s]\\s*(-?\\d+(?:\\.\\d+)?)$/);
 if(m&&Math.abs(+m[1])<=90&&Math.abs(+m[2])<=180){open((+m[1]).toFixed(2)+', '+(+m[2]).toFixed(2),+m[1],+m[2]);return}
 const found=localHits(q);
 if(found.length===1){open(found[0].name,found[0].lat,found[0].lon);return}
 if(found.length){offer(found,'請選擇:');return}
 msg.textContent='搜尋中';
 try{const url='https://photon.komoot.io/api/?limit=8&lat=23.7&lon=121&q='+encodeURIComponent(q);
  // lang=default 讓地名用當地的文字,不跟著瀏覽器的語言變成英文;服務不接受這個參數時退回原本的查法
  let r=await fetch(url+'&lang=default');if(!r.ok)r=await fetch(url);
  if(!r.ok)throw new Error(r.status);const d=await r.json();
  const seen=new Set(),out=[];
  for(const f of d.features||[]){const p=f.properties,[lon,lat]=f.geometry.coordinates;
   const where=[p.city,p.county,p.state,p.country].filter((v,i,a)=>v&&v!==p.name&&a.indexOf(v)===i).join('、');
   const key=lat.toFixed(1)+','+lon.toFixed(1);if(seen.has(key))continue;seen.add(key);out.push({name:p.name||q,where,lat,lon})}
  if(!out.length){msg.textContent='找不到「'+q+'」。臺灣的地點請輸入鄉鎮市區的名稱(例如 東港、恆春鎮、屏東縣枋山鄉),或輸入座標,例如 22.37,120.59';return}
  offer(out.slice(0,6),'請選擇:')}
 catch(err){msg.textContent='找不到「'+q+'」。臺灣的地點請輸入鄉鎮市區的名稱(例如 東港、恆春鎮);國外地名的查詢服務目前連不上,可以改輸入座標,例如 35.68,139.69'}});
saved().forEach(p=>addTab(p.name,p.lat,p.lon));
route();
"""

esc = html.escape


# --------------------------------------------------------------------------- #
# 小零件
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 天氣圖示
# --------------------------------------------------------------------------- #

# 像電視氣象的圖示:太陽、雲、雨滴。每種天氣用語對應一個圖示,文字仍然保留在旁邊,
# 所以圖示只是輔助(讀屏軟體會略過)。雨滴用綠色,和全站「綠色代表降雨」一致。
_CLOUD = "M13 34a8 8 0 0 1-.6-16A11 11 0 0 1 33.5 15 9.5 9.5 0 0 1 35 34z"
_RAYS = "".join(
    f'<line x1="24" y1="5" x2="24" y2="9.5" transform="rotate({angle} 24 24)"/>' for angle in range(0, 360, 45)
)
# 顏色直接寫在圖形的 style 上、用 CSS 變數:<use> 複製出來的圖形吃不到外部樣式表裡
# 帶祖先條件的選擇器,但行內樣式會跟著複製,變數則依使用處解析,深淺色模式都正確。
_SUN = f'<g style="fill:var(--sun);stroke:var(--sun);stroke-width:3;stroke-linecap:round"><circle cx="24" cy="24" r="8"/>{_RAYS}</g>'


def _drops(xs: list[float], length: float = 7, top: float = 33) -> str:
    return '<g style="stroke:var(--rain);stroke-width:3.2;stroke-linecap:round">' + "".join(
        f'<line x1="{x}" y1="{top}" x2="{x - length * 0.35:.1f}" y2="{top + length}"/>' for x in xs
    ) + "</g>"


_CLOUD_STYLE = {"cl": "fill:var(--cloud)", "cl2": "fill:var(--cloud);opacity:.55", "dk": "fill:var(--cloud-dark)"}


def _cloud(kind: str, scale: float = 1.0, dx: float = 0, dy: float = 0) -> str:
    return f'<path style="{_CLOUD_STYLE[kind]}" d="{_CLOUD}" transform="translate({dx} {dy}) scale({scale})"/>'


_SMALL_SUN = f'<g transform="translate(-1.5 -2.5) scale(0.7)">{_SUN}</g>'
ICON_SHAPES = {
    "sun": _SUN,
    "sun-cloud": _SMALL_SUN + _cloud("cl", 0.8, 9, 10),
    "cloud": _cloud("cl2", 0.7, 16, -2) + _cloud("cl", 0.9, 0, 6),
    "overcast": _cloud("cl", 0.7, 16, -2) + _cloud("dk", 0.9, 0, 6),
    "sun-shower": _SMALL_SUN + _cloud("cl", 0.8, 9, 4) + _drops([24, 33], 6, 35),
    "cloud-shower": _cloud("cl", 0.95, 1, -5) + _drops([19, 30], 6),
    "rain-maybe": _cloud("dk", 0.95, 1, -5) + _drops([19, 30], 7),
    "rain": _cloud("dk", 0.95, 1, -5) + _drops([15, 24, 33], 8),
    "heavy-rain": _cloud("dk", 0.95, 1, -5) + _drops([12, 19, 26, 33, 40], 10),
}
# 天氣用語(analysis.describe_sky 的輸出)→ 圖示
ICON_FOR = {
    "晴": "sun",
    "晴時多雲": "sun-cloud",
    "多雲": "cloud",
    "陰": "overcast",
    "晴偶陣雨": "sun-shower",
    "多雲偶雨": "cloud-shower",
    "可能有雨": "rain-maybe",
    "有雨": "rain",
    "明顯降雨": "heavy-rain",
    "大雨": "heavy-rain",
}
ICON_SPRITE = (
    '<svg class="sprite" aria-hidden="true" width="0" height="0"><defs>'
    + "".join(f'<symbol id="i-{name}" viewBox="0 0 48 48">{shape}</symbol>' for name, shape in ICON_SHAPES.items())
    + "</defs></svg>"
)


def _icon(sky: str) -> str:
    """天氣用語對應的圖示;沒有對應的用語就不畫。"""
    name = ICON_FOR.get(sky)
    return f'<svg class="ico" aria-hidden="true" viewBox="0 0 48 48"><use href="#i-{name}"/></svg>' if name else ""


def _table(rows: list[list[str]], cls: str = "", caption: str = "", head_col: bool = True) -> str:
    """文字表格;第一列是標題,第一欄是列標題。"""
    head = "".join(f"<th>{esc(c)}</th>" for c in rows[0])
    body = ""
    for row in rows[1:]:
        cells = "".join(f"<th>{esc(c)}</th>" if i == 0 and head_col else f"<td>{esc(c)}</td>" for i, c in enumerate(row))
        body += f"<tr>{cells}</tr>"
    note = f"<caption>{esc(caption)}</caption>" if caption else ""
    return f'<div class="scroll"><table class="{cls}">{note}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else ""


def _how(text: str, title: str = "怎麼看這張表") -> str:
    return f'<details class="how"><summary>{esc(title)}</summary><p>{esc(text)}</p></details>'


def _meter(label: str) -> str:
    """五格的信心度:格子越多越有把握,旁邊同時寫出文字。"""
    level = {"高": 5, "中高": 4, "中": 3, "中低": 2, "低": 1}.get(label, 3)
    return f'<span class="conf c{level}">{"<i></i>" * 5}<em>信心度{esc(label)}</em></span>'


def _day(day: str) -> str:
    return briefing.label(day)


# --------------------------------------------------------------------------- #
# 一週時間軸
# --------------------------------------------------------------------------- #


def _ribbon(forecast, brief: briefing.Briefing) -> str:
    """地點頁最上面的時間軸:上排是時段與主要影響系統,下面每天一欄。"""
    low = min(f.tmin.p50 for f in forecast)
    high = max(f.tmax.p50 for f in forecast)
    parts = [f'<div class="ribwrap"><div class="ribbon" style="--n:{len(forecast)}">']
    index = {f.day: i for i, f in enumerate(forecast, 1)}
    for stage in brief.stages:
        # --o 是手機版(每天一列)的排列順序:時段標題排在它的第一天前面
        order = index.get(stage.days[0], 1) * 10 - 5
        parts.append(
            f'<div class="stage s{stage.index}{"" if stage.stable else " wet"}" style="grid-column:span {len(stage.days)};--o:{order}">'
            f"<small>時段{ORDINALS[stage.index - 1]}</small>{esc(stage.headline)}"
            f'<small class="say">{esc(plain.system_words(stage.headline))}</small></div>'
        )
    for i, f in enumerate(forecast, 1):
        d = datetime.fromisoformat(f.day)
        tip = (
            f"{_day(f.day)}\n高溫 {f.tmax.p50:.0f}°C(10–90% 範圍 {f.tmax.p10:.0f}–{f.tmax.p90:.0f})\n"
            f"低溫 {f.tmin.p50:.0f}°C(範圍 {f.tmin.p10:.0f}–{f.tmin.p90:.0f})\n"
            f"降雨機率 {f.pop:.0%},雨量中位數 {f.precip.p50:.0f} mm、偏多時 {f.precip.p90:.0f} mm"
        )
        parts.append(
            f'<div class="d d{i}" style="--o:{i * 10}" data-day="{f.day}" data-tip="{esc(tip)}">'
            f'<b data-rel>{d.month}/{d.day}<span>{"一二三四五六日"[d.weekday()]}</span></b>'
            f'<span class="wx">{_icon(f.sky)}{esc(f.sky)}</span>'
            f'<div class="tcol"><s style="flex:{high - f.tmax.p50:.2f}"></s><span>{f.tmax.p50:.0f}°</span>'
            f'<i style="flex:{max(f.tmax.p50 - f.tmin.p50, 0.1):.2f}"></i><span>{f.tmin.p50:.0f}°</span><s style="flex:{f.tmin.p50 - low:.2f}"></s></div>'
            f'<div class="rcol"><i style="height:{f.pop * 100:.0f}%"></i></div><span class="pop">降雨 {f.pop:.0%}</span>'
            f"{_meter(f.confidence_label)}</div>"
        )
    parts.append("</div></div>")
    parts.append(
        '<p class="key">直條的上端是高溫、下端是低溫,各天畫在同一個刻度上;綠色柱是降雨機率;方格越多,信心度越高。'
        "綠色的時段有天氣系統影響;時段名稱下方的小字是白話說明。</p>"
    )
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 圖
# --------------------------------------------------------------------------- #


def _box_chart(forecast) -> str:
    """系集氣象圖:方框是 25–75%、細線是 10–90%、橫線是中位數。紅色高溫、藍色低溫。"""
    width, height, left, top, bottom = 760, 230, 44, 14, 30
    lo = min(f.tmin.p10 for f in forecast)
    hi = max(f.tmax.p90 for f in forecast)
    lo, hi = 2 * (int(lo) // 2) - 2, 2 * (int(hi) // 2) + 4
    plot = height - top - bottom
    y = lambda value: top + plot * (hi - value) / (hi - lo)
    slot = (width - left) / len(forecast)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="每日高低溫的系集分布">']
    for tick in range(lo, hi + 1, 2 if hi - lo <= 16 else 4):
        parts.append(f'<line class="gridline" x1="{left}" x2="{width}" y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" text-anchor="end">{tick}°</text>')
    for i, f in enumerate(forecast):
        cx = left + slot * (i + 0.5)
        tip = (
            f"{_day(f.day)}\n高溫:中位數 {f.tmax.p50:.1f}°C,一半成員在 {f.tmax.p25:.1f}–{f.tmax.p75:.1f},八成在 {f.tmax.p10:.1f}–{f.tmax.p90:.1f}\n"
            f"低溫:中位數 {f.tmin.p50:.1f}°C,一半成員在 {f.tmin.p25:.1f}–{f.tmin.p75:.1f},八成在 {f.tmin.p10:.1f}–{f.tmin.p90:.1f}"
        )
        parts.append(f'<g data-tip="{esc(tip)}"><rect x="{cx - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{plot}" fill="transparent"/>')
        for spread, cls, dx in ((f.tmax, "hi", -11), (f.tmin, "lo", 11)):
            x = cx + dx
            parts.append(f'<line class="w{cls}" x1="{x:.1f}" x2="{x:.1f}" y1="{y(spread.p90):.1f}" y2="{y(spread.p10):.1f}"/>')
            parts.append(
                f'<rect class="{cls}" x="{x - 8:.1f}" y="{y(spread.p75):.1f}" width="16" height="{max(2.0, y(spread.p25) - y(spread.p75)):.1f}"/>'
            )
            parts.append(f'<line class="w{cls}" stroke-width="2.5" x1="{x - 8:.1f}" x2="{x + 8:.1f}" y1="{y(spread.p50):.1f}" y2="{y(spread.p50):.1f}"/>')
        parts.append(f'<text x="{cx:.1f}" y="{height - 8}" text-anchor="middle">{esc(report._label(f.day))}</text></g>')
    parts.append("</svg>")
    return '<div class="scroll">' + "".join(parts) + "</div>"


def _rain_chart(forecast) -> str:
    """日雨量的系集分布(方框 25–75%、細線到 90%),上方標出降雨機率。"""
    width, height, left, top, bottom = 760, 170, 44, 24, 30
    plot = height - top - bottom
    peak = max(5.0, max(f.precip.p90 for f in forecast))
    peak = 5 * (int(peak) // 5 + 1)
    y = lambda value: top + plot * (1 - min(value, peak) / peak)
    slot = (width - left) / len(forecast)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="每日雨量的系集分布與降雨機率">']
    for tick in (0, peak / 2, peak):
        parts.append(f'<line class="gridline" x1="{left}" x2="{width}" y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" text-anchor="end">{tick:.0f}</text>')
    for i, f in enumerate(forecast):
        cx = left + slot * (i + 0.5)
        tip = f"{_day(f.day)}\n降雨機率 {f.pop:.0%}(日雨量達 1 mm 的成員比例)\n雨量中位數 {f.precip.p50:.1f} mm,一半成員在 {f.precip.p25:.1f}–{f.precip.p75:.1f},偏多時 {f.precip.p90:.1f}"
        parts.append(f'<g data-tip="{esc(tip)}"><rect x="{cx - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{plot}" fill="transparent"/>')
        parts.append(f'<line class="wrn" x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(f.precip.p90):.1f}" y2="{y(f.precip.p10):.1f}"/>')
        parts.append(f'<rect class="rn" x="{cx - 10:.1f}" y="{y(f.precip.p75):.1f}" width="20" height="{max(2.0, y(f.precip.p25) - y(f.precip.p75)):.1f}"/>')
        parts.append(f'<line class="wrn" stroke-width="2.5" x1="{cx - 10:.1f}" x2="{cx + 10:.1f}" y1="{y(f.precip.p50):.1f}" y2="{y(f.precip.p50):.1f}"/>')
        parts.append(f'<text class="val" x="{cx:.1f}" y="{top - 8}" text-anchor="middle">{f.pop:.0%}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height - 8}" text-anchor="middle">{esc(report._label(f.day))}</text></g>')
    parts.append("</svg>")
    return '<div class="scroll">' + "".join(parts) + "</div>"


def _period_grid(rows) -> str:
    """每天一列、四個時段各一格;底部的橫條長度是降雨機率。"""
    head = "".join(f"<th>{name} {start:02d}–{end:02d}</th>" for name, start, end in periods.PERIODS)
    body = []
    for day, cells in periods.by_day(rows).items():
        tds = [f"<th>{esc(report._label(day))}</th>"]
        for name, _, _ in periods.PERIODS:
            c = cells.get(name)
            if c is None or c.temp is None:
                tds.append("<td>–</td>")
                continue
            if c.pop is None or c.precip is None:  # 資料沒有涵蓋到這個時段結束,雨量不完整
                tds.append(f'<td class="cell"><b>{c.temp.p50:.0f}°</b></td>')
                continue
            tip = (
                f"{report._label(day)} {name}\n氣溫 {c.temp.p50:.0f}°C(範圍 {c.temp.p10:.0f}–{c.temp.p90:.0f})\n"
                f"降雨機率 {c.pop:.0%}\n雨量中位數 {c.precip.p50:.1f} mm,偏多時 {c.precip.p90:.1f} mm"
            )
            tds.append(
                f'<td class="cell" data-tip="{esc(tip)}">{_icon(c.sky)}<b>{c.temp.p50:.0f}°</b><span>{esc(c.sky)} {c.pop:.0%}</span>'
                f'<i style="width:{c.pop * 100:.0f}%"></i></td>'
            )
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="scroll"><table class="grid"><thead><tr><th>日期</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


# --------------------------------------------------------------------------- #
# 地點頁的六個分頁
# --------------------------------------------------------------------------- #


def _summary_view(forecast, brief, comparison) -> str:
    blocks = "".join(
        f'<div class="stg{"" if s.stable else " wet"}"><p class="when">時段{ORDINALS[s.index - 1]} {esc(s.span)}</p><h3>{esc(s.headline)}</h3>'
        f"<p>{esc(s.weather)},高溫 {esc(briefing._range(s.tmax, '°C'))}、低溫 {esc(briefing._range(s.tmin, '°C'))},"
        f"降雨機率 {esc(briefing._range([p * 100 for p in s.pop], '%'))}。</p>"
        f'<p>{_meter(s.confidence_label)}</p><p class="why">{esc(";".join(s.reasons))}</p></div>'
        for s in brief.stages
    )
    parts = ["<h2>這一週怎麼分段</h2>", f'<div class="stages">{blocks}</div>']
    parts.append("<h2>注意事項與劇烈天氣</h2>")
    if brief.hazards:
        parts.append(_table([["危害", "時間", "說明"], *([a, b, c] for a, b, c in brief.hazards)]))
    else:
        parts.append('<p class="sub">這一週沒有大雨、強風或明顯降溫的訊號。</p>')
    parts.append("<h2>重點</h2>" + _list(report.highlights(forecast)))
    if comparison:
        parts.append(f"<h2>和 {comparison.age_hours:.0f} 小時前的預報比</h2>" + _list(report.comparison_lines(comparison)))
    return "".join(parts)


def _daily_view(forecast, brief, period_list) -> str:
    rows = [["日期", "主要影響系統", "天氣", "高溫 °C", "低溫 °C", "降雨機率", "雨量 mm", "最大風 km/h", "信心度"]]
    for f, role in zip(forecast, brief.roles):
        wind = "–" if not f.wind else f"{f.wind.p50:.0f}" + (f"(陣風 {f.gust.p50:.0f})" if f.gust else "")
        rows.append(
            [report._label(f.day), role.system, f.sky, report._range(f.tmax), report._range(f.tmin), f"{f.pop:.0%}",
             f"{f.precip.p50:.0f} / {f.precip.p90:.0f}", wind, f"{f.confidence_label}({f.confidence})"]
        )
    parts = [
        "<h2>逐日預報</h2>",
        _table(rows, caption="氣溫是所有成員的中位數,括號是 10–90% 的範圍;雨量是中位數/偏多時(90 百分位);降雨機率是日雨量達 1 mm 的成員比例。"),
    ]
    if period_list:
        parts += [
            "<h2>一天之內:凌晨、上午、下午、晚上</h2>",
            '<p class="sub">每格是該時段的氣溫、天氣與 6 小時內的降雨機率,底部橫條越長越容易下雨。鋒面晚到半天,整天的預報仍然對,'
            "時段卻會整個錯開,所以三天以後的時段只當參考。</p>",
            _period_grid(period_list),
        ]
    return "".join(parts)


def _synoptic_view(forecast, brief, upper_days, signal_list, southern: bool) -> str:
    parts = ["<h2>現況:由地面往上看</h2>"]
    if brief.current:
        parts.append(_table([["層次", "觀察重點", "對預報的意義"], *([a, b, c] for a, b, c in brief.current)],
                            caption=f"{_day(forecast[0].day)} 的日平均,系集中位數。"))
    if not upper_days:
        parts.append('<p class="sub">這次沒有取得高空資料,所以只有地面的判斷,沒有各層的分析。</p>')
    else:
        parts.append("<h2>各時段的預報理由</h2>")
        parts.append('<p class="sub">每個時段依 500 hPa 槽脊、850 與 925 hPa、250 hPa 噴流、水氣的順序看,最後檢查高低層是否配合。</p>')
        for s in brief.stages:
            items = "".join(f"<dt>{esc(level)}</dt><dd>{esc(text)}</dd>" for level, text in s.rationale)
            parts.append(
                f'<div class="reason{"" if s.stable else " wet"}"><h3>時段{ORDINALS[s.index - 1]} {esc(s.span)} {esc(s.headline)}</h3>'
                f"<dl>{items}<dt>垂直結構檢查</dt><dd>{esc(s.coupling)}</dd></dl></div>"
            )
        parts += [
            "<h2>逐日高空分析</h2>",
            _table(report.upper_rows(upper_days), "tight"),
            _how(
                "ECMWF 與 AIFS 系集的中位數。風速 m/s,輻散 10⁻⁵/s(正值是高層空氣流出,有利下方上升),平流 °C/日(正值是暖平流)。"
                "噴流軸欄是 250 hPa 最大風速相對測站的位置與風速;槽脊欄的度數是槽線離測站的經度。925 風欄寫成「西→東北」代表當天低層風向轉變,"
                "也就是鋒面通過。PWAT 是整層可降水量(mm);K 指數 30 以上或 CAPE 1000 J/kg 以上容易有雷雨。"
            ),
            "<h2>高空條件和地面預報對不對得上</h2>",
            _list(report.upper_impact(upper_days, forecast, southern)),
        ]
    if signal_list:
        by_day: dict[str, list[str]] = {}
        for s in signal_list:
            by_day.setdefault(s.day, []).append(s.text)
        parts.append("<h2>天氣型態的轉變</h2>" + _list([f"{report._label(d)}:" + ";".join(t) + "。" for d, t in by_day.items()]))
    parts.append(
        '<h2>對照實際的天氣圖</h2><p class="sub">這裡的分析只用單點的數值,看不到系統的形狀。判斷前請對照'
        '<a href="https://www.cwa.gov.tw/V8/C/W/analysis.html">中央氣象署的地面天氣圖</a>、'
        '<a href="https://www.cwa.gov.tw/V8/C/W/OBS_Sat.html">衛星雲圖</a>與'
        '<a href="https://www.tropicaltidbits.com/analysis/models/">Tropical Tidbits 的模式圖</a>;'
        f'有熱帶系統時看<a href="{TYPHOON_URL}">Pillar 熱帶氣旋預報</a>。</p>'
    )
    return "".join(parts)


def _models_view(forecast, brief, ens, comparison) -> str:
    names = list(ens.systems)
    rows = [["日期", *names, "綜合", "判斷與理由"]]
    cell = lambda v: f"{v[0]:.0f}° {v[1]:.0%} {v[2]:.0f} mm"
    for m in brief.models:
        rows.append([report._label(m.day), *(cell(m.systems[n]) if n in m.systems else "–" for n in names), cell(m.blend), m.verdict])
    counts = "、".join(f"{s} {ens.member_count(s)} 個成員" for s in names)
    parts = [
        "<h2>各模式怎麼說</h2>",
        _table(rows, "models", "每格依序是高溫、降雨機率、日雨量中位數。各模式整週固定的溫度偏差已先扣掉。"),
        f'<p class="sub">{esc(counts)}。每套系集的總權重相同,成員多的不會壓過成員少的。ECMWF 與 AIFS 來自同一個中心,'
        "兩者同時出錯的機會比和 GEFS 同時出錯高。</p>",
        "<h2>系集分布</h2>",
        '<div class="sheet">' + _box_chart(forecast) + "</div>",
        '<div class="sheet">' + _rain_chart(forecast) + "</div>",
        _how("上圖紅色是高溫、藍色是低溫,下圖是日雨量(mm),上方的百分比是降雨機率。方框是中間一半的成員(25–75%),細線涵蓋八成(10–90%),"
             "粗橫線是中位數。方框越窄,成員越一致、信心越高。系集的離散度通常偏小,實際誤差會比方框寬。", "怎麼看這兩張圖"),
        f'<p class="sub">模式格點海拔 {ens.meta.get("elevation") or 0:.0f} m。和實際地點的海拔差越多,氣溫的誤差越大。</p>',
        "<h2>預報趨勢:和前幾次比</h2>",
    ]
    if comparison:
        parts.append(
            f'<p class="sub">和 {comparison.age_hours:.0f} 小時前的預報比較。連續幾次往同一個方向修正,趨勢較可信;'
            "來回擺盪代表模式還沒有定論。穩定不等於準確。</p>" + _list(report.comparison_lines(comparison))
        )
    else:
        parts.append('<p class="sub">還沒有 5 小時以前的預報可以比較。預設地點每 6 小時更新一次,之後這裡會列出每一天的預報改了多少。</p>')
    return "".join(parts)


def _risk_view(forecast, brief) -> str:
    rows = [["時段", "信心度", "理由"]]
    for s in brief.stages:
        rows.append([f"時段{ORDINALS[s.index - 1]} {s.span}", f"{s.confidence_label}({s.confidence})", ";".join(s.reasons)])
    parts = [
        "<h2>各時段的信心度</h2>",
        _table(rows),
        _how("信心度由四件事扣分:成員的高溫有多分散、各模式之間差多少、降雨機率是否接近五五波、預報時效有多遠。它是經驗分數,不是命中率;"
             "80 以上為高、65 以上中高、50 以上中、35 以上中低。", "信心度怎麼算"),
        "<h2>替代情境</h2>",
    ]
    if brief.scenarios:
        rows = [["日期", "情境", "內容", "發生條件", "可能性"]]
        for s in brief.scenarios:
            rows.append([report._label(s.day), f"{s.name}{'(主要)' if s.name == 'A' else ''}", s.content, s.condition, f"{s.likelihood}({s.share:.0%} 的成員)"])
        parts.append(_table(rows, caption="可能性是成員比例換成的定性描述,不是官方機率。"))
    else:
        parts.append('<p class="sub">這一週沒有模式分成兩派、或下不下雨五五波的日子。</p>')
    parts.append("<h2>接下來要觀察什麼</h2>")
    parts.append(_list(brief.watch) if brief.watch else '<p class="sub">目前沒有特別需要追蹤的項目。</p>')
    notes = report.uncertainty(forecast)
    if notes:
        parts.append("<h2>分歧的細節</h2>" + _list(notes))
    return "".join(parts)


def _plain_view(forecast, upper_days, period_list, southern: bool) -> str:
    """給不熟悉氣象的讀者:每天的天氣、原因與準備,不使用術語。"""
    sky = {f.day: f.sky for f in forecast}
    cards = "".join(
        f'<div class="pd" data-day="{d.day}"><h3 data-rel>{_icon(sky.get(d.day, ""))}{esc(plain._label(d.day))}</h3><p>{esc(d.weather)}</p>'
        + (f"<p><b>為什麼:</b>{esc(d.why)}</p>" if d.why else "")
        + (f'<p><b>怎麼準備:</b>{esc("、".join(d.advice))}。</p>' if d.advice else "")
        + (f'<p class="sub">{esc(d.caution)}</p>' if d.caution else "")
        + "</div>"
        for d in plain.plain_days(forecast, upper_days, period_list, southern)
    )
    parts = [
        "<h2>這一週的天氣、原因與影響</h2>",
        '<p class="sub">把前面各分頁的高空分析翻成日常用語:每天天氣怎樣、為什麼、該怎麼準備。</p>',
        f"<p>{esc(plain.overview(forecast, upper_days))}</p>",
        f'<div class="sheet">{cards}</div>',
    ]
    if upper_days:
        parts.append("<h2>其他分頁裡的名詞是什麼意思</h2>" + _table([["名詞", "白話解釋"], *([t, m] for t, m in plain.GLOSSARY)]))
    return "".join(parts)


def _week_box(week: dict | None, link: str = "") -> str:
    """最醒目的那一塊:用白話講未來一週會怎樣。week 是 data.json 裡 plain.week 的內容。"""
    if not week:
        return ""
    story = "".join(f"<li>{esc(s)}</li>" for s in week["story"])
    return (
        f'<div class="week"><p class="wk">未來一週天氣總結</p><p class="wh">{esc(week["headline"])}</p><ul>{story}</ul>'
        + (f"<p>{esc(week['cause'])}</p>" if week.get("cause") else "")
        + (f"<p><b>要注意:</b>{esc(';'.join(week['watch']))}。</p>" if week.get("watch") else "")
        + link
        + "</div>"
    )


def _numbers_help() -> str:
    """時間軸下方的「這些數字怎麼看」:降雨機率、雨量、括號、信心度、風速。"""
    rows = "".join(f"<dt>{esc(term)}</dt><dd>{esc(text)}</dd>" for term, text in plain.HOW_TO_READ)
    return f'<details class="how nums"><summary>這些數字怎麼看</summary><dl>{rows}</dl></details>'


def _verify_view(result: verify.Verification | None, stored: bool) -> str:
    head = "<h2>過去幾天報得準不準</h2>"
    if result is None:
        why = (
            "還沒有可以校驗的紀錄。網站會保存每一次的預報,等那一天過去之後,再拿來和實際狀況比較;開始保存一天後,這裡就會有結果。"
            if stored
            else "用搜尋欄查的地點沒有保存過去的預報,所以無法校驗。預設地點才有這一頁的內容。"
        )
        return head + f'<p class="sub">{why}</p>'
    tag = {"有對": "verdict-ok", "漏報": "verdict-miss", "空報": "verdict-fa"}
    score_rows = [["幾天前的預報", "樣本", "高溫平均誤差", "高溫平均絕對誤差", "低溫平均誤差", "低溫平均絕對誤差", "降雨:有對 / 漏報 / 空報"]]
    for s in result.scores:
        score_rows.append([f"{s.lead} 天前", f"{s.count} 天", f"{s.tmax_bias:+.1f}°C", f"{s.tmax_mae:.1f}°C", f"{s.tmin_bias:+.1f}°C",
                           f"{s.tmin_mae:.1f}°C", f"{s.hits} / {s.misses} / {s.false_alarms}"])
    head_cells = "".join(f"<th>{lead} 天前的預報</th>" for lead in verify.LEADS)
    body = []
    for day in sorted(result.observed, reverse=True):
        if not any(result.at(day, lead) for lead in range(1, verify.MAX_LEAD + 1)):
            continue
        seen = result.observed[day]
        tds = [f"<th>{esc(report._label(day))}</th>", f'<td>{seen["tmax"]:.0f}° / {seen["tmin"]:.0f}°<br><small>雨量 {seen["rain"]:.1f} mm</small></td>']
        for lead in verify.LEADS:
            c = result.at(day, lead)
            if c is None:
                tds.append("<td>–</td>")
                continue
            tds.append(
                f"<td>{c.tmax:.0f}° / {c.tmin:.0f}°,降雨 {c.pop:.0%}<br><small>高溫 {c.tmax_error:+.1f}、低溫 {c.tmin_error:+.1f},"
                f'降雨<span class="{tag[c.rain]}">{c.rain}</span></small></td>'
            )
        body.append("<tr>" + "".join(tds) + "</tr>")
    daily = (
        f'<div class="scroll"><table><thead><tr><th>日期</th><th>實際(高 / 低)</th>{head_cells}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )
    return (
        head
        + '<p class="sub">誤差是預報減實際,正值代表預報偏暖。降雨機率 40% 以上算「報了會下雨」,實際日雨量 1 mm 以上算「有下雨」;'
        "沒報卻下了是漏報,報了卻沒下是空報。</p>"
        + _table(score_rows)
        + "<h2>逐日對照</h2>"
        + daily
        + _how("「實際」不是測站觀測,而是 Open-Meteo 整理的分析資料(各模式最短時效的預報接起來),它本身也有誤差。樣本只有幾天,"
               "只能看出明顯的系統性偏差,例如某個地點的低溫連續幾天都報太高。時效越遠誤差越大是正常的。", "這個校驗的限制")
    )


# --------------------------------------------------------------------------- #
# 地點
# --------------------------------------------------------------------------- #

def previous_age_hours(path: Path | None, now: datetime) -> float | None:
    """上一次發布的 data.json 是幾小時前產生的;讀不到就回傳 None。"""
    if path is None:
        return None
    try:
        at = datetime.fromisoformat(json.loads(Path(path).read_text(encoding="utf-8"))["generated"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return (now - at).total_seconds() / 3600
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_previous(path: Path | None) -> dict[str, list[dict]]:
    """讀取上一次發布的 data.json,回傳 {地名: 先前各次執行的摘要}。讀不到就當作沒有。

    舊版的 data.json 沒有 runs,就用它當時的逐日預報湊出一筆,第一次也能比較。
    """
    if path is None:
        return {}
    try:
        old = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(old.get("runs"), dict):
            return {name: [r for r in rows if isinstance(r, dict)] for name, rows in old["runs"].items() if isinstance(rows, list)}
        return {
            name: [{"at": old["generated"], "days": {d["day"]: {"tmax": d["tmax"]["p50"], "tmin": d["tmin"]["p50"], "pop": d["pop"]} for d in place["days"]}}]
            for name, place in old["places"].items()
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def build_place(
    name: str,
    raw: dict,
    upper_raw: dict | None,
    days: int = 7,
    period_raw: dict | None = None,
    previous_runs: list[dict] | None = None,
    now: datetime | None = None,
    observed_raw: dict | None = None,
) -> tuple[str, dict]:
    """由原始資料產生一個地點的 HTML 區塊與 JSON 資料。

    previous_runs 是這個地點先前各次執行的摘要(None 表示這個地點不保存紀錄,例如用搜尋
    查的地點);JSON 資料的 runs 是加上這一次之後的結果。observed_raw 是過去幾天的實際
    狀況,校驗用。
    """
    now = now or datetime.now(timezone.utc)
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
    brief = briefing.build(forecast, signal_list, analysis.patterns(ens), upper_days, lat, lon)
    comparison = history.compare_runs(previous_runs or [], forecast, now)
    checked = None
    if observed_raw and previous_runs:
        try:
            checked = verify.verify(previous_runs, verify.parse_observed(observed_raw, forecast[0].day))
        except (KeyError, TypeError, ValueError):
            checked = None

    views = {
        "summary": (
            '<p class="sub">以下依「主導的天氣系統」把一週分段,用語比較專業;同樣的內容用白話寫在頁面最上方和「白話總結」分頁。</p>'
            f'<p class="lede">{esc(brief.lede)}</p>' + _summary_view(forecast, brief, comparison)
        ),
        "daily": _daily_view(forecast, brief, period_list),
        "synoptic": _synoptic_view(forecast, brief, upper_days, signal_list, lat < 0),
        "models": _models_view(forecast, brief, ens, comparison),
        "risk": _risk_view(forecast, brief),
        "verify": _verify_view(checked, previous_runs is not None),
        "plain": _plain_view(forecast, upper_days, period_list, lat < 0),
    }
    total = sum(ens.member_count(s) for s in ens.systems)
    week = plain.week_summary(forecast, upper_days)
    block = (
        _week_box(asdict(week) if week else None, '<p class="sub">每天的原因和準備建議在下方「白話總結」分頁;想看專業的分析依據,請看其他分頁。</p>')
        + _ribbon(forecast, brief)
        + _numbers_help()
        + f'<p class="sub">預報取自最接近的模式格點({abs(lat):.2f}°{"N" if lat >= 0 else "S"} {abs(lon):.2f}°{"E" if lon >= 0 else "W"},'
        f"格點間距約 25 公里,所以相鄰鄉鎮的結果會很接近),綜合 {total} 個系集成員(同一批模式算了 {total} 次)的結果。</p>"
        + '<div class="tabs" role="tablist">'
        + "".join(f'<button role="tab" data-tab="{key}">{title}</button>' for key, title in TABS)
        + "</div>"
        + "".join(f'<section class="view" data-view="{key}">{views[key]}</section>' for key, _ in TABS)
    )
    data = report.to_json(name, ens, forecast, signal_list, comparison, upper_days, period_list)
    data["briefing"] = asdict(brief)
    data["verification"] = asdict(checked) if checked else None
    data["runs"] = history.extend(previous_runs or [], forecast, now, KEEP_RUNS)
    return block, data


# --------------------------------------------------------------------------- #
# 整頁
# --------------------------------------------------------------------------- #

SEARCH_HTML = """<form id="find" role="search"><input id="q" type="search" autocomplete="off"
placeholder="查其他地點:東港、恆春鎮、Tokyo 或 22.37,120.59" aria-label="搜尋地點"><button>查詢</button>
<span id="fmsg"></span><div id="hits"></div></form>"""

RADAR_HTML = """<section class="page" data-page="radar" id="radar"><h1>即時雷達回波</h1>
<p class="lede">接下來一到三小時會不會下雨,看雷達比看預報準。</p>
<div class="bar"><button data-src="rv" aria-pressed="true">全球合成</button><button data-src="jma">日本氣象廳</button>
<button data-src="cwa">中央氣象署</button><button id="rplay">暫停</button><span id="rtime"></span></div>
<div id="map"></div><img id="cwa" alt="中央氣象署雷達合成回波圖">
<p class="sub legend"><span class="lg" aria-hidden="true"></span>顏色由淺藍、深藍到黃、紅,代表雨由小到大;沒有顏色的地方目前沒有下雨。白色圓點是「地點預報」裡目前選的地點。</p>
<p class="sub" id="rnote">最近約 2 小時的回波動畫,每 5 分鐘自動更新。雷達只能看出現在雨在哪裡、往哪裡移動;更久之後請看預報。</p>
<p class="sub">來源:全球合成為 <a href="https://www.rainviewer.com/">RainViewer</a>(彙整各國氣象單位的雷達);
<a href="https://www.jma.go.jp/bosai/nowc/">日本氣象廳</a>降水即時預報(涵蓋日本周邊,台灣只到東北部外海);
<a href="https://www.cwa.gov.tw/V8/C/W/OBS_Radar.html">中央氣象署</a>雷達合成回波圖。底圖 © OpenStreetMap。</p></section>"""

GLOSSARY = (
    ("hPa 與層次", "氣壓的單位。925、850、700、500、250 hPa 大約是 0.8、1.5、3、5.5、10.5 公里高。", "綜觀分析"),
    ("槽、脊", "高空氣流像波浪。往赤道凹下去的是槽,氣流不穩定;往極地凸出去的是脊,氣流下沉、穩定。", "綜觀分析的 500 hPa"),
    ("槽前、槽後", "槽線的東邊是槽前,吹偏南風、空氣上升,容易下雨;西邊是槽後,吹偏北風、空氣下沉。", "逐日高空分析"),
    ("副熱帶高壓、588 線", "太平洋上的暖高壓。500 hPa 高度 5880 m 的等高線常用來標示它的範圍,在線內通常穩定、偏熱。", "綜觀分析"),
    ("鋒面", "冷暖空氣的交界。通過時低層風向會突然轉變,本站用 925 hPa 風向在 12 小時內轉 80° 以上來判斷。", "時間軸、綜觀分析"),
    ("東北季風", "冬半年大陸冷高壓南下時,臺灣附近低層吹的東北風。迎風的北部、東半部多雲有雨,背風的中南部通常乾。", "主要影響系統"),
    ("噴流", "高空一條風速特別強的窄帶(本站以 250 hPa 風速 30 m/s 以上為準)。它的位置代表冷暖空氣交界在哪裡。", "綜觀分析的 250 hPa"),
    ("輻散、輻合", "高層空氣流出去叫輻散,會帶動下方空氣上升;流進來叫輻合,不利上升。", "逐日高空分析"),
    ("暖平流、冷平流", "風把較暖(或較冷)的空氣吹過來。暖平流增溫並有利上升,冷平流降溫、空氣下沉。", "綜觀分析的 850 hPa"),
    ("可降水量 PWAT", "把整層大氣的水氣全部凝結下來有多少毫米。數值越大,有雨時雨勢可能越大。", "逐日高空分析"),
    ("K 指數", "用 850、700、500 hPa 的溫度與濕度估計大氣穩不穩定。30 以上容易有雷雨。", "逐日高空分析"),
    ("CAPE", "對流可用位能,空氣塊上升時能得到多少能量。1000 J/kg 以上視為不穩定。", "逐日高空分析"),
    ("垂直結構檢查", "看低層的舉升與高層的輻散有沒有配合。配合才有大範圍的雨;只有低層,多半是迎風面的淺層降雨。", "各時段的預報理由"),
    ("決定性預報", "模式只算一次的預報。它只是一種可能的情境,不能當機率用。", "說明"),
    ("系集預報", "把初始條件稍微改一點,讓同一個模式算幾十次;每一次叫一個成員。成員散得越開,越沒把握。", "模式與系集"),
    ("降雨機率", "本站指日雨量達 1 mm 的成員比例。機率高不代表雨大,要一起看雨量。", "時間軸、逐日預報"),
    ("信心度", "依成員是否集中、各模式是否一致算出的經驗分數,分成高、中高、中、中低、低。不是命中率。", "時間軸、不確定性"),
    ("前後報", "同一天的預報,這一次和前幾次比。連續往同方向修正較可信;穩定不等於準確。", "模式與系集的預報趨勢"),
    ("分析場", "模式把各種觀測整合出來的「現在的大氣狀態」。它很接近實際,但不等於觀測。", "校驗"),
    ("漏報、空報", "沒報下雨卻下了是漏報;報了下雨卻沒下是空報。", "校驗"),
)

ABOUT_HTML = (
    '<section class="page" data-page="about" id="about"><h1>說明</h1>'
    '<p class="lede">這個網站把課堂上做一週天氣預報報告的流程,交給程式每 6 小時做一次。</p>'
    "<h2>怎麼閱讀</h2><ul>"
    "<li><b>總覽</b>先講結論:這一週分成哪幾段、各由什麼天氣系統主導,以及所有地點的逐日預報表。</li>"
    "<li><b>地點預報</b>最上面先用白話講未來一週的天氣,接著是一週的時間軸,下面有七個分頁。前六個的順序和預報報告的章節相同:"
    "摘要、逐日與時段、綜觀分析、模式與系集、不確定性、校驗;最後一個「白話總結」把每天的原因和準備建議用日常用語再講一次,"
    "不熟悉氣象的讀者可以只看最上方的總結和這個分頁。</li>"
    "<li><b>即時雷達</b>回答「接下來幾小時會不會下雨」,這件事雷達比預報準。</li>"
    "<li>有顏色的地方都有固定的意思:綠色是降雨,紅色是高溫與暖空氣,藍色是低溫與冷空氣。這是手繪天氣圖的習慣。</li></ul>"
    "<h2>分析流程</h2><ul>"
    "<li><b>先講結論。</b>把一週依主要影響系統分成最多三個時段,每段給信心度與理由。</li>"
    "<li><b>層次由上而下再回到地面。</b>先看 500 hPa 的槽脊,再看 850 與 925 hPa 的溫度、風與鋒面,最後看 250 hPa 的噴流與高層輻散,"
    "並檢查高低層有沒有配合。</li>"
    "<li><b>時效越遠看得越粗。</b>第 5 到 7 天只看趨勢,第 3 到 5 天看系統的強度與範圍,前 3 天才看一天之內的時段。</li>"
    "<li><b>不確定性用三種方式檢查。</b>比較不同模式、比較同一天的前後幾次預報、看系集成員散得多開。</li>"
    "<li><b>事後校驗。</b>過去的每一次預報都留著,那一天過去後和實際狀況比較。</li></ul>"
    "<h2>資料與模式的角色</h2>"
    + _table(
        [
            ["資料", "來源", "用途"],
            ["ECMWF IFS 系集(51 個成員)", "Open-Meteo Ensemble API", "傳統數值模式。地面預報、高空各層、可降水量與 CAPE"],
            ["ECMWF AIFS 系集(51 個成員)", "Open-Meteo Ensemble API", "AI 模式。地面預報與高空各層;系統的位置與時間通常較準,強度偏弱"],
            ["NCEP GEFS(31 個成員)", "Open-Meteo Ensemble API", "另一個中心的看法,用來檢查前兩者;只有地面預報"],
            ["過去幾天的分析資料", "Open-Meteo Forecast API", "校驗:和先前的預報比較"],
            ["雷達回波", "RainViewer、日本氣象廳、中央氣象署", "只在瀏覽器裡即時顯示,不進入預報計算"],
        ]
    )
    + "<h2>天氣圖示</h2>"
    '<p class="sub">圖示依當天的降雨機率與雲量決定,旁邊一定有文字。雨滴越多,雨越明顯。</p>'
    '<div class="icons">'
    + "".join(
        f"<div>{_icon(sky)}<b>{sky}</b><span>{note}</span></div>"
        for sky, note in (
            ("晴", "雲很少,不會下雨"),
            ("晴時多雲", "有些雲,不會下雨"),
            ("多雲", "雲多,不會下雨"),
            ("陰", "整天都是雲,不會下雨"),
            ("晴偶陣雨", "降雨機率 20–40%,雲不多"),
            ("多雲偶雨", "降雨機率 20–40%,雲多"),
            ("可能有雨", "降雨機率 40–70%"),
            ("有雨", "降雨機率 70% 以上"),
            ("明顯降雨", "會下雨,而且雨量 10 毫米以上;大雨也用這個圖示"),
        )
    )
    + "</div>"
    + "<h2>名詞解釋</h2>"
    + _table([["名詞", "簡單解釋", "在本站哪裡看到"], *([a, b, c] for a, b, c in GLOSSARY)])
    + '<section id="notes"><h2>注意事項</h2><ul>'
    "<li><b>這不是官方預報。</b>颱風、豪雨、寒流等災害性天氣,請以中央氣象署的警特報為準。</li>"
    "<li><b>程式的判斷是推論。</b>「鋒面通過」「東北季風」這些名稱是從單一地點上空的數值推出來的,沒有看天氣圖,請對照實際的天氣圖。</li>"
    "<li><b>越後面的日子越不準。</b>信心度低代表模式之間看法分歧。</li>"
    "<li><b>降雨機率高不代表雨大。</b>請一起看雨量,以及綜觀分析裡是否寫著淺層降雨。</li>"
    "<li><b>解析度約 25 公里。</b>相鄰鄉鎮的結果會很接近;落山風、山區午後雷陣雨、海陸風這類局部現象,模式解析不出來。</li>"
    "<li><b>氣溫沒有用測站觀測校正。</b>山區與海邊的誤差會比平地大。</li>"
    "<li><b>預設地點大約每 6 小時更新一次。</b>更新由 GitHub 的排程觸發,排程有時會延後甚至跳過,所以網站每小時檢查一次,"
    "資料超過 5 小時就重算。頁面右上角會寫資料是幾小時前的;超過 9 小時沒更新會出現提醒。開著的頁面每 10 分鐘檢查一次,有新預報會自動重新載入。</li>"
    "<li><b>「今天、明天」是依你裝置的日期標的。</b>已經過去的日子會變淡。</li>"
    "<li><b>用搜尋欄查的地點不會自動更新</b>,每次點開才重新計算,第一次約需 10–20 秒,也沒有預報趨勢與校驗。</li>"
    "</ul></section>"
    "<h2>相關連結</h2><ul>"
    f'<li><a href="{TYPHOON_URL}">Pillar 熱帶氣旋預報</a>:颱風路徑、侵襲機率與各模式的校驗。</li>'
    '<li><a href="https://www.cwa.gov.tw/V8/C/W/analysis.html">中央氣象署天氣圖</a>、'
    '<a href="https://www.cwa.gov.tw/V8/C/P/Typhoon/TY_NEWS.html">颱風消息</a>、'
    '<a href="https://www.cwa.gov.tw/V8/C/W/week.html">一週預報</a>。</li>'
    '<li><a href="https://www.tropicaltidbits.com/analysis/models/">Tropical Tidbits</a>:各模式的天氣圖。</li>'
    f'<li><a href="data.json">data.json</a>:這一頁所有數字的原始資料。<a href="{REPO_URL}">原始碼與方法筆記</a>。</li>'
    "</ul></section>"
)

RADAR_JS = """
(function(){
const el=document.getElementById('map'),img=document.getElementById('cwa'),label=document.getElementById('rtime'),
note=document.getElementById('rnote'),play=document.getElementById('rplay');
if(!el)return;
if(!window.L){el.textContent='地圖元件載入失敗';return}
const here=()=>{const s=document.querySelector('article.place.on')||document.querySelector('article.place');return [+s.dataset.lat,+s.dataset.lon]};
const map=L.map(el,{scrollWheelZoom:false}).setView(here(),7);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:10,attribution:'&copy; OpenStreetMap'}).addTo(map);
const pin=L.circleMarker(here(),{radius:6,weight:2,color:'#0b0b0b',fillColor:'#fff',fillOpacity:1}).addTo(map);
// 地圖建立時頁面可能還沒排好版(或這一頁還沒顯示),尺寸一變就重新置中,除非讀者自己拖動過
let moved=false;map.on('dragstart',()=>{moved=true});
const center=()=>{map.invalidateSize();map.setView(here(),map.getZoom(),{animate:false});pin.setLatLng(here())};
window.radarMove=()=>{moved=false;center()};
window.radarShow=window.radarMove;
if(window.ResizeObserver)new ResizeObserver(()=>{if(moved)map.invalidateSize();else center()}).observe(el);
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
 if(frames[at])label.textContent='雷達時間 '+new Date(frames[at].t).toLocaleString('zh-TW',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})}
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


def _overview(names: list[str], payloads: dict[str, dict] | None) -> str:
    """總覽頁:一句話的結論、所有地點的逐日預報表、各地的注意事項。"""
    if not payloads or not all(n in payloads and payloads[n].get("briefing") for n in names):
        links = "".join(f'<li><a href="#/place/{i}/summary">{esc(n)}</a></li>' for i, n in enumerate(names))
        return f'<h1>一週天氣預報</h1><ul>{links}</ul>'
    stages = briefing.outline([payloads[n]["briefing"]["roles"] for n in names])
    systems = {day: system for stage in stages for day, system in stage["systems"].items()}
    if len(stages) > 1:
        lede = f"這一週分成 {len(stages)} 段:" + ";".join(f"{briefing._span(s['days'])} {s['headline']}" for s in stages) + "。"
    else:
        lede = f"這一週:{stages[0]['headline']}。"
    head = "<th>日期</th><th>主要影響系統<small>多數地點的判斷</small></th>" + "".join(f'<th><a href="#/place/{i}/summary">{esc(n)}</a></th>' for i, n in enumerate(names))
    by_place = {n: {d["day"]: d for d in payloads[n]["days"]} for n in names}
    body = ""
    for day in sorted(systems):
        cells = ""
        for n in names:
            d = by_place[n].get(day)
            cells += (
                f'<td><span class="wx">{_icon(d["sky"])}{esc(d["sky"])}</span><br>{d["tmin"]["p50"]:.0f}–{d["tmax"]["p50"]:.0f}<small>({d["pop"]:.0%})</small></td>'
                if d else "<td>–</td>"
            )
        body += (
            f'<tr data-day="{day}"><th data-rel>{esc(_day(day))}</th>'
            f"<td>{esc(systems[day])}<small>{esc(plain.system_words(systems[day]))}</small></td>{cells}</tr>"
        )
    table = (
        '<p class="swipe">表格可以左右滑動,看其他地點。</p>'
        f'<div class="scroll"><table class="zone"><caption>氣溫為最低–最高,°C;括號內是降雨機率。點地名看那個地點的完整分析。'
        '<span class="wide">主要影響系統下方的小字是白話說明。</span></caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )
    boxes = "".join(
        f'<details class="wkd"{" open" if i == 0 else ""}><summary>{esc(n)}:{esc(payloads[n]["plain"]["week"]["headline"])}</summary>'
        + _week_box(payloads[n]["plain"]["week"], f'<p class="sub"><a href="#/place/{i}/plain">看 {esc(n)} 每天的說明</a></p>')
        + "</details>"
        for i, n in enumerate(names)
        if payloads[n].get("plain", {}).get("week")
    )
    merged: dict[tuple[str, str], list[str]] = {}
    for n in names:
        for hazard, when, _ in payloads[n]["briefing"]["hazards"]:
            merged.setdefault((hazard, when), []).append(n)
    if merged:
        hazards = _table([["危害", "時間", "地點"], *([h, w, "、".join(p)] for (h, w), p in merged.items())])
    else:
        hazards = '<p class="sub">各地這一週都沒有大雨、強風或明顯降溫的訊號。</p>'
    return (
        f"<h1>這一週的天氣</h1>{boxes}"
        f'<h2>天氣系統怎麼演變</h2><p class="lede">{esc(lede)}</p>'
        f"<h2>逐日分區預報</h2>{table}"
        f"<h2>注意事項與劇烈天氣</h2>{hazards}"
        '<p class="sub">主要影響系統取最多地點的判斷;各地點自己的分段、預報理由與信心度在<a href="#/place">地點預報</a>。'
        '接下來幾小時的雨看<a href="#/radar">即時雷達</a>。</p>'
    )


def build_page(sections: list[tuple], generated: datetime, failed: list[str], payloads: dict[str, dict] | None = None) -> str:
    """sections 的每一項是 (地名, HTML 區塊) 或 (地名, HTML 區塊, 緯度, 經度);有座標才會附雷達圖。

    payloads 是 {地名: build_place 回傳的資料},用來組總覽頁;沒有的話總覽只列出地點。
    """
    sections = [(s[0], s[1], *(s[2:4] if len(s) >= 4 else (None, None))) for s in sections]
    radar = all(lat is not None for _, _, lat, _ in sections)
    stamp = generated.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    iso = generated.astimezone(timezone.utc).isoformat()
    names = [s[0] for s in sections]
    chips = "".join(f'<button role="tab">{esc(n)}</button>' for n in names)
    places = "".join(
        f'<article class="place" data-name="{esc(name)}"' + (f' data-lat="{lat}" data-lon="{lon}"' if radar else "")
        + f"><h1>{esc(name)}</h1>{block}</article>"
        for name, block, lat, lon in sections
    )
    nav = "".join(
        f'<a data-page="{key}" href="#/{key}">{title}</a>'
        for key, title in (("overview", "總覽"), ("place", "地點預報"), ("radar", "即時雷達"), ("about", "說明"))
        if radar or key != "radar"
    )
    leaflet = "https://unpkg.com/leaflet@1.9.4/dist/leaflet"
    missing = f'<p class="sub">這次沒能更新:{esc("、".join(failed))}。</p>' if failed else ""
    script = JS.replace("__TABS__", json.dumps([key for key, _ in TABS])).replace(
        "__TOWNS__", json.dumps([list(t) for t in TOWNS], ensure_ascii=False, separators=(",", ":"))
    )
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>一週天氣預報</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{FONTS}">'
        + (f'<link rel="stylesheet" href="{leaflet}.css">' if radar else "")
        + f"<style>{CSS}</style><script>document.documentElement.classList.add('js')</script></head><body>{ICON_SPRITE}"
        f'<header class="top"><div class="in"><a class="brand" href="#/overview">一週天氣預報</a><nav class="nav" aria-label="主要頁面">{nav}</nav>'
        f'<p class="sub" id="fresh" data-generated="{iso}">更新於 {stamp}</p></div></header><main>'
        f'<section class="page on" data-page="overview">{_overview(names, payloads)}{missing}</section>'
        f'<section class="page" data-page="place"><div id="places" role="tablist" aria-label="地點">{chips}</div>{SEARCH_HTML}{places}</section>'
        f'{RADAR_HTML if radar else ""}{ABOUT_HTML}'
        "<footer>資料來自 Open-Meteo(ECMWF、AIFS、GEFS 系集)。數值是系集中位數;影響系統、信心度與高空研判是程式依經驗法則做的推論,"
        "不是官方預報。颱風與災害性天氣請以中央氣象署為準。</footer>"
        '</main><div id="tip"></div>'
        + (f'<script src="{leaflet}.js"></script>' if radar else "")
        + f"<script>{script}{RADAR_JS if radar else ''}</script></body></html>"
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
    parser.add_argument("--previous", metavar="PATH", type=Path, help="上一次發布的 data.json,用來比較預報趨勢與校驗")
    parser.add_argument(
        "--only-if-older-than", metavar="HOURS", type=float,
        help="上一次發布的資料還不到這麼多小時就不重算(排程每小時檢查一次時使用)",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    if args.only_if_older_than is not None:
        age = previous_age_hours(args.previous, now)
        if age is not None and 0 <= age < args.only_if_older_than:
            print(f"上一次發布是 {age:.1f} 小時前,還不到 {args.only_if_older_than:g} 小時,這次不重算")
            return 0
    previous = load_previous(args.previous)
    sections, data, runs, failed = [], {}, {}, []
    for name in args.places[:1] if args.from_file else args.places:
        try:
            observed_raw = None
            if args.from_file:
                bundle = client.load_raw(args.from_file)
                raw, upper_raw = client.split_bundle(bundle)
                period_raw = bundle.get("periods")
                observed_raw = bundle.get("observed")
            else:
                place = client.geocode(name)
                raw = client.fetch_ensemble(place.latitude, place.longitude, args.days)
                zone = raw.get("timezone") or "GMT"
                try:
                    upper_raw = client.fetch_upper(place.latitude, place.longitude, args.days, zone)
                except client.FetchError as err:
                    print(f"{name}:沒有高空資料({err})", file=sys.stderr)
                    upper_raw = None
                try:
                    period_raw = client.fetch_periods(place.latitude, place.longitude, args.days, zone)
                except client.FetchError as err:
                    print(f"{name}:沒有時段資料({err})", file=sys.stderr)
                    period_raw = None
                if previous.get(name):  # 有先前的預報才需要實際狀況來校驗
                    try:
                        observed_raw = client.fetch_observed(place.latitude, place.longitude, zone)
                    except client.FetchError as err:
                        print(f"{name}:沒有校驗用的資料({err})", file=sys.stderr)
            block, payload = build_place(name, raw, upper_raw, args.days, period_raw, previous.get(name, []), now, observed_raw)
        except (client.FetchError, ValueError, OSError) as err:
            print(f"{name}:略過({err})", file=sys.stderr)
            failed.append(name)
            if previous.get(name):  # 這次沒抓到,先前的紀錄仍要留著,下次才接得上
                runs[name] = previous[name]
            continue
        runs[name] = payload.pop("runs")
        sections.append((name, block, payload["location"].get("latitude") or 0.0, payload["location"].get("longitude") or 0.0))
        data[name] = payload
    if not sections:
        print("沒有任何地點成功,不產生網頁", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(build_page(sections, now, failed, data), encoding="utf-8")
    write_engine(args.out / "weekcast.zip")
    (args.out / "data.json").write_text(
        json.dumps({"generated": now.isoformat(), "places": data, "runs": runs}, ensure_ascii=False), encoding="utf-8"
    )
    print(f"已產生 {args.out}/index.html({len(sections)} 個地點)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
