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
from .cities import CITIES
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
table.zone td{white-space:nowrap}
td small,th small{color:var(--ink2);font-size:13px}
caption{caption-side:bottom;text-align:left;color:var(--ink2);font-size:13px;padding:8px 12px 0}
.wx{font-family:var(--kai);font-weight:700;font-size:16px}
.ribwrap{overflow-x:auto;margin:6px 0 4px}
.ribbon{display:grid;grid-template-columns:repeat(var(--n),minmax(94px,1fr));min-width:calc(var(--n)*94px);background:var(--sheet);border:1px solid var(--line);border-radius:4px}
.stage{padding:7px 12px 9px;border-top:5px solid var(--ochre);border-bottom:1px solid var(--line);font-family:var(--kai);font-weight:700;line-height:1.4;background:var(--ochre-soft)}
.stage.rain{border-top-color:var(--rain);background:var(--rain-soft)}.stage.cold{border-top-color:var(--cold);background:var(--cold-soft)}
.stage small{display:block;font:400 12px/1.5 var(--sans);color:var(--ink2)}
.stage small.say{font-size:13px;margin-top:2px}
.zone small{display:block;font-size:12px;line-height:1.45;color:var(--muted);font-weight:400;max-width:17em;white-space:normal}
.swipe{display:none;font-size:13px;color:var(--muted);margin:4px 0}
.rel{display:inline-block;font:600 12px/1.5 var(--sans);font-style:normal;color:var(--sheet);background:var(--ink);border-radius:3px;padding:0 5px;margin-left:6px;vertical-align:middle}
.past{opacity:.5}.past .rel{background:var(--muted)}
#fresh.stale{color:var(--warm);font-weight:600}
#renew{font:inherit;font-weight:600;margin-left:8px;padding:2px 10px;border:1px solid var(--warm);border-radius:4px;background:var(--sheet);color:var(--warm);cursor:pointer}
.renewed{color:var(--rain)}
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
[hidden]{display:none!important}
/* 總覽:自選比較的地點 */
.pick{display:none;margin:2px 0 14px}.js .pick{display:block}
.pick-h{margin:0 0 6px;font-weight:600}.pick-h small{font-weight:400;color:var(--muted);margin-left:8px;font-size:13px}
#pickchips{display:flex;flex-wrap:wrap;gap:6px}
#pickchips label{display:inline-flex;align-items:center;gap:6px;padding:4px 12px 4px 9px;border:1px solid var(--ink2);border-radius:999px;background:var(--sheet);font-size:15px;cursor:pointer;user-select:none}
#pickchips label.custom{border-style:dashed}
#pickchips input{margin:0;accent-color:var(--rain)}
#pickchips label:has(input:checked){background:var(--ink);color:var(--sheet);border-color:var(--ink)}
#pickchips label:has(input:focus-visible){outline:2px solid var(--cold);outline-offset:2px}
#addcmp{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0 0}
#addq{font:inherit;font-size:14px;padding:5px 10px;border:1px solid var(--ink2);border-radius:4px;background:var(--sheet);color:var(--ink);flex:1 1 180px;min-width:0;max-width:330px}
#addcmp>button{font:inherit;font-size:14px;padding:5px 12px;border:1px solid var(--ink2);border-radius:4px;background:transparent;color:var(--ink);cursor:pointer}
#addmsg{font-size:13px;color:var(--ink2)}#addmsg button{font:inherit;font-size:13px;padding:2px 9px;margin-left:6px;border:1px solid var(--ink2);border-radius:4px;background:transparent;color:var(--ink);cursor:pointer}#addhits{display:flex;flex-wrap:wrap;gap:6px;flex-basis:100%}
#addhits button{font:inherit;font-size:14px;padding:4px 11px;border:1px solid var(--ink2);border-radius:4px;background:transparent;color:var(--ink);cursor:pointer}
.stages{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));background:var(--sheet);border:1px solid var(--line);border-radius:4px;margin:10px 0}
.stg{padding:14px 18px 16px;border-top:5px solid var(--ochre)}
.stg.rain{border-top-color:var(--rain)}.stg.cold{border-top-color:var(--cold)}
.stg .when{font-size:13px;color:var(--ink2);margin:0}
.stg h3{margin:2px 0 8px;font-size:20px}
.stg p{margin:6px 0;font-size:15px}
.stg .why{font-size:13px;color:var(--ink2)}
.reason{margin:14px 0 22px;padding-left:16px;border-left:5px solid var(--ochre)}
.reason.rain{border-left-color:var(--rain)}.reason.cold{border-left-color:var(--cold)}
.reason h3{margin-top:0}
.reason dl{margin:6px 0;display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;font-size:15px;max-width:60em}
.reason dt{font-weight:600;white-space:nowrap}.reason dd{margin:0}
details.how{font-size:14px;color:var(--ink2);margin:8px 0;max-width:46em}
details.how summary{cursor:pointer;color:var(--ink)}
.verdict-ok{color:var(--rain);font-weight:600}.verdict-miss{color:var(--warm);font-weight:600}.verdict-fa{color:var(--ochre);font-weight:600}
svg{display:block;min-width:620px;width:100%}
svg.sprite{position:absolute;min-width:0;width:0;height:0}
/* 總覽的一覽表 */
.hero[data-slim]{height:132px;margin-bottom:14px}
table.glance{border-collapse:separate;border-spacing:0;background:var(--sheet);border:1px solid var(--line);border-radius:4px;font-size:14px}
.glance caption{caption-side:bottom;text-align:left;color:var(--ink2);font-size:13px;padding:8px 2px 0}
.glance th,.glance td{border-bottom:1px solid var(--line);padding:8px 6px;text-align:center;vertical-align:top}
.glance tbody tr:last-child th,.glance tbody tr:last-child td{border-bottom:0}
.glance thead th{font-weight:600;color:var(--ink);white-space:nowrap;border-bottom:2px solid var(--ink)}
.glance thead th span{color:var(--muted);font-weight:400;margin-left:3px}
.glance thead th:first-child,.glance tbody th{text-align:left;padding-left:12px}
.glance tbody th{font-family:var(--kai);font-size:17px;white-space:nowrap;vertical-align:middle}
.glance tbody th a{text-decoration:none}
.glance td{min-width:84px;border-left:1px solid var(--line)}
.glance td .ico{display:block;width:34px;height:34px;margin:0 auto 2px}
.glance .sk{display:block;font-family:var(--kai);font-weight:700;font-size:14px;white-space:nowrap}
.glance .tt{display:block;color:var(--ink2);white-space:nowrap}.glance .tt b{color:var(--ink);font-size:15px}
.glance .pb{display:block;height:4px;margin:5px 8px 2px;background:var(--line);border-radius:2px;overflow:hidden}
.glance .pb i{display:block;height:100%;background:var(--rain);transform-origin:left}
.glance small{display:block;font-size:12px;color:var(--muted)}
.glance td.today,.glance th.today{background:color-mix(in srgb,var(--rain-soft) 32%,transparent)}
.why{display:grid;gap:10px;margin:10px 0}
.why-row{display:grid;grid-template-columns:5.5em 1fr;gap:4px 14px;align-items:start;padding:10px 0;border-top:1px solid var(--line)}
.why-row>a{font-family:var(--kai);font-weight:700;font-size:17px;text-decoration:none}
.why ol{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:8px}
.why li{flex:1 1 240px;margin:0;padding:8px 12px;border-left:4px solid var(--ochre);background:var(--ochre-soft);border-radius:2px;font-size:15px}
.why li.wet{border-left-color:var(--rain);background:var(--rain-soft)}
.why li b{display:block;font-size:13px;color:var(--ink2)}
.why li small{display:block;font-size:13px;color:var(--ink2);margin-top:2px}
ul.texts li{margin:6px 0}
/* 動態效果:只在讀者沒有關閉動態效果時才播 */
.rcol i{transform-origin:bottom}.grid td.cell i{transform-origin:left}
@media print{[data-fxk]{opacity:1!important;transform:none!important}}
@media (prefers-reduced-motion:no-preference){
.js .page.on,.js .view.on{animation:fadein .35s ease-out}
@keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.ico.live .rays{transform-box:fill-box;transform-origin:center;animation:spin 16s linear infinite}
.ico.live .cloud{animation:bob 4.5s ease-in-out infinite alternate}
.ico.live .cloud+.cloud{animation-duration:6s;animation-delay:-2s}
.ico.live .rain line{animation:fall 1.1s linear infinite}
.ico.live .rain line:nth-child(2){animation-delay:-.4s}.ico.live .rain line:nth-child(3){animation-delay:-.75s}
.ico.live .rain line:nth-child(4){animation-delay:-.2s}.ico.live .rain line:nth-child(5){animation-delay:-.6s}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes bob{from{transform:translateX(-1.2px)}to{transform:translateX(1.2px)}}
@keyframes fall{0%{transform:translateY(-4px);opacity:0}25%{opacity:1}80%{opacity:1}100%{transform:translateY(5px);opacity:0}}
}
@media (hover:hover){
.d,.stg,.pd,.week,.glance td,.grid td.cell{transition:background-color .2s,box-shadow .25s}
.d:hover,.grid td.cell:hover,.glance td:hover{background:color-mix(in srgb,var(--rain-soft) 40%,transparent)}
.stg:hover,.week:hover{box-shadow:0 6px 18px -10px color-mix(in srgb,var(--ink) 45%,transparent)}
.tabs button,#places button,.nav a{transition:color .2s,border-color .25s,background-color .2s}
}
/* 天氣景象橫幅 */
.hero{position:relative;height:190px;border-radius:6px;overflow:hidden;margin:8px 0 18px;border:1px solid var(--line);
--sky1:#8fc3ee;--sky2:#dff0fb;--hill1:#9fc6ae;--hill2:#5f9a7b;--hcloud:#ffffff;--hdark:#c9d3d8;--cap:#12202b}
.hero.mild{--sky1:#a9c6dd;--sky2:#e6eef3;--hcloud:#f6f9fa}
.hero.grey{--sky1:#9fb0ba;--sky2:#d8dfe2;--hill1:#93ab9d;--hill2:#5a8370;--hcloud:#e9eef0;--hdark:#aab6bc}
.hero.wet{--sky1:#73858f;--sky2:#b9c5c9;--hill1:#7f978a;--hill2:#476f5d;--hcloud:#d3dbde;--hdark:#8d9aa1;--cap:#0e1a22}
@media (prefers-color-scheme:dark){.hero{--sky1:#17354f;--sky2:#2c5878;--hill1:#2f5544;--hill2:#1c3a2d;--hcloud:#8fa3b1;--hdark:#5c6f7b;--cap:#f1f6f8}
.hero.mild{--sky1:#1d3445;--sky2:#35536a}.hero.grey{--sky1:#232f37;--sky2:#3b4a54;--hcloud:#7f8f99;--hdark:#55646d}
.hero.wet{--sky1:#1a242a;--sky2:#2f3d45;--hcloud:#6c7b84;--hdark:#48565e;--cap:#f1f6f8}}
.hero svg{position:absolute;inset:0;width:100%;height:100%;min-width:0}
.bg3d{position:fixed;inset:0;width:100%;height:100%;z-index:-1;pointer-events:none;display:block}
#altitude{position:fixed;left:14px;bottom:12px;z-index:4;font-size:12px;color:var(--muted);background:color-mix(in srgb,var(--sheet) 75%,transparent);
padding:2px 10px;border-radius:999px;pointer-events:none;opacity:0;transition:opacity .4s}
#altitude.on{opacity:1}
@media (max-width:760px){#altitude{bottom:64px;left:10px}}
.hero.see{background:linear-gradient(color-mix(in srgb,var(--sky1) 40%,transparent),color-mix(in srgb,var(--sky2) 28%,transparent))}
.hero.see .sun,.hero.see .drift,.hero.see .rain{display:none}
#fxtoggle{font:inherit;font-size:13px;margin-left:10px;padding:2px 10px;border:1px solid var(--line);border-radius:999px;background:var(--sheet);color:var(--ink2);cursor:pointer}
#fxtoggle[aria-pressed=true]{border-color:var(--rain);color:var(--rain)}
.hero .sky{fill:url(#none);fill:var(--sky2)}
.hero{background:linear-gradient(var(--sky1),var(--sky2))}.hero .sky{fill:transparent}
.hero .disc{fill:var(--sun)}.hero .glow{fill:var(--sun);opacity:.28}.hero .rays line{stroke:var(--sun);stroke-width:5;stroke-linecap:round}
.hero .cl{fill:var(--hcloud);opacity:.92}.hero .cl.dk{fill:var(--hdark)}
.hero .drop{stroke:var(--rain);stroke-width:2.6;stroke-linecap:round;opacity:.85}
.hero .hill{fill:var(--hill2)}.hero .hill.far{fill:var(--hill1)}
.hero .cap{position:absolute;left:20px;bottom:16px;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;color:var(--cap);
text-shadow:0 1px 6px color-mix(in srgb,var(--sky2) 70%,transparent)}
.hero .cap .when{font-size:14px;font-weight:600;flex-basis:100%}
.hero .cap b{font-family:var(--kai);font-size:34px;line-height:1.1}.hero .cap b small{font-size:16px;margin-left:4px}
.hero .cap .t{font-size:26px;font-weight:700}.hero .cap .t i{font-style:normal;font-weight:400;font-size:18px}
.hero .cap .p{font-size:15px;font-weight:600}
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
.swipe{display:block}.wide{display:none}
.hero[data-slim]{height:118px}
.glance tbody th,.glance thead th:first-child{position:sticky;left:0;z-index:1;background:var(--sheet);box-shadow:1px 0 0 var(--line)}
.glance td{min-width:76px}.glance tbody th{font-size:16px;padding-left:10px}
.why-row{grid-template-columns:1fr}
.week .wh{font-size:21px}
.hero{height:160px}.hero .cap{left:14px;bottom:12px}.hero .cap b{font-size:28px}.hero .cap .t{font-size:22px}
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
 fresh.textContent=(old?'資料已 '+Math.round(h)+' 小時沒有更新,內容可能過時。':'')+'更新於 '+local(built)+'('+ago+')'+(extra||'');
 if(old){const b=document.createElement('button');b.type='button';b.id='renew';b.textContent='立即重算';b.addEventListener('click',renew);fresh.appendChild(b)}}
stampText();
// 排程沒跑、資料太舊時,讀者可以自己用最新資料重算目前的地點(和搜尋欄用同一套做法,在瀏覽器裡算)
async function renew(){const s=secs()[cur];if(!s||s.dataset.pending)return;
 const keep=s.innerHTML;go('place',cur,tab);
 s.innerHTML='<h1>'+esc(s.dataset.name)+'</h1><p class="wait"></p>';s.dataset.pending='1';
 await compute(s);
 if(s.dataset.pending){const why=(s.querySelector('.wait')||{}).textContent||'';delete s.dataset.pending;s.innerHTML=keep;paint();markDays();
  stampText(';重算沒有成功:'+why)}
 else{const note=document.createElement('p');note.className='sub renewed';
  note.textContent='這個地點剛在你的瀏覽器裡用最新資料重算('+local(Date.now())+')。其他地點仍是上面寫的更新時間;預報趨勢與校驗要等網站下次自動更新。';
  s.querySelector('h1').after(note)}}
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
 p.runPython('from weekcast.browser import forecast, bundle, overview');return p})();
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
 const c=cmp();c.on=c.on.filter(n=>n!==s.dataset.name);keepCmp(c);
 b.remove();s.remove();drawChips();go('place',0,TABS[0])}
// 同一個地點只算一次:地點分頁與總覽的比較共用同一份結果,3 小時內重新整理也不必重算
// 進度訊息給最後一個在等這個地點的畫面(地點分頁或總覽)
const done=new Map();
function ensure(name,lat,lon,say){const k=name+'@'+(+lat).toFixed(3)+','+(+lon).toFixed(3);
 let e=done.get(k);if(e){if(say)e.say=say;return e.job}
 e={say};const tell=t=>{if(e.say)e.say(t)};
 e.job=(async()=>{
  try{const c=JSON.parse(sessionStorage.getItem('wk:'+k)||'null');if(c&&Date.now()-c.at<108e5)return c.v}catch(x){}
  tell('正在載入分析程式,第一次約需 10–20 秒。');const p=await engine();
  tell('正在抓取 '+name+' 的系集資料並分析。');
  const v=JSON.parse(await p.globals.get('bundle')(name,+lat,+lon));
  try{sessionStorage.setItem('wk:'+k,JSON.stringify({at:Date.now(),v}))}catch(x){}
  return v})();
 done.set(k,e);e.job.catch(()=>done.delete(k));return e.job}
async function compute(s){delete s.dataset.pending;const w=s.querySelector('.wait');
 try{w.textContent='正在抓取 '+s.dataset.name+' 的系集資料並分析。';
  const v=await ensure(s.dataset.name,s.dataset.lat,s.dataset.lon,t=>{w.textContent=t});
  s.innerHTML='<h1>'+esc(s.dataset.name)+'</h1>'+v.block;paint();markDays();if(window.heroPaint)heroPaint();if(window.fxRun)fxRun()}
 catch(e){s.dataset.pending='1';w.textContent='無法取得預報:'+explain(e)+'請再點一次這個地點重試。'}}
// 把程式或瀏覽器的英文錯誤訊息換成看得懂的中文
function explain(e){const t=String(e&&e.message||e);try{console.warn('weekcast:',t)}catch(x){}
 if(/minutely|hourly/i.test(t)&&/limit/i.test(t))return '氣象資料來源暫時忙線(短時間內查詢太多次),等一分鐘。';
 if(/daily/i.test(t)&&/limit/i.test(t))return '今天向氣象資料來源查詢的次數已達上限,明天才能再查。';
 if(/failed to fetch|networkerror|load failed|network/i.test(t))return '連不上網路或資料來源,請確認網路連線。';
 if(/pyodide|import|module|wasm/i.test(t))return '分析程式沒有載入成功,可能是網路較慢。';
 if(/沒有可用|格式|回應/.test(t))return '這個地點沒有可用的預報資料。';
 return '發生預期外的狀況。'}
function open(name,lat,lon){hits.innerHTML='';msg.textContent='';box.value='';
 const list=saved();if(!list.some(p=>p.name===name&&p.lat==lat)){list.push({name,lat,lon});keep(list)}
 const i=addTab(name,lat,lon);drawChips();go('place',i,TABS[0])}
// 先查內建的臺灣鄉鎮市區清單(不連網、一定是中文);查不到才問外部的地名服務
const TOWNS=__TOWNS__,CITIES=__CITIES__,norm=t=>t.trim().replace(/臺/g,'台').replace(/\\s+/g,'');
function localHits(text){const q=norm(text);if(!q)return [];const out=[];
 for(const [county,town,lat,lon] of TOWNS){const base=town.slice(0,-1),c=county.slice(0,-1);
  const forms=[town,base,county+town,county+base,c+town,c+base];
  const rank=forms.includes(q)?0:(town.startsWith(q)||(county+town).startsWith(q))?1:(county+town).includes(q)?2:-1;
  if(rank>=0)out.push({rank,name:county+town,where:'',lat,lon})}
 // 世界主要城市:中文名或英文名都可以
 const low=text.trim().toLowerCase();
 for(const [zh,en,country,lat,lon] of CITIES){const name=zh.replace(/臺/g,'台'),e=en.toLowerCase(),bare=name.split('(')[0];
  const rank=(q===name||q===bare||low===e)?0:(name.startsWith(q)||e.startsWith(low))?1:(q.length>=2&&(name.includes(q)||e.includes(low)||q===country))?2:-1;
  if(rank>=0)out.push({rank,name:bare,where:country,lat,lon})}
 return out.sort((a,b)=>a.rank-b.rank).slice(0,8)}
// 找地點:座標 → 內建的臺灣鄉鎮與世界城市 → 氣象資料來源的地名服務 → Photon。direct 表示不必再選,可以直接用
const COORD=/^(-?\\d+(?:\\.\\d+)?)\\s*[,,\\s]\\s*(-?\\d+(?:\\.\\d+)?)$/;
async function lookup(q){const m=q.match(COORD);
 if(m&&Math.abs(+m[1])<=90&&Math.abs(+m[2])<=180)return {list:[{name:(+m[1]).toFixed(2)+', '+(+m[2]).toFixed(2),lat:+m[1],lon:+m[2]}],direct:true};
 const found=localHits(q);if(found.length)return {list:found,direct:found.length===1};
 // 清單裡沒有的地名:先問氣象資料來源自己的地名服務(較穩定,有中文名就用中文),問不到再問 Photon
 try{const g=await(await fetch('https://geocoding-api.open-meteo.com/v1/search?count=8&language=zh&name='+encodeURIComponent(q))).json();
  const list=(g.results||[]).map(r=>({name:r.name,where:[r.admin1,r.country].filter((v,i,a)=>v&&v!==r.name&&a.indexOf(v)===i).join('、'),lat:r.latitude,lon:r.longitude}));
  if(list.length)return {list:list.slice(0,6)}}catch(err){}
 try{const url='https://photon.komoot.io/api/?limit=8&lat=23.7&lon=121&q='+encodeURIComponent(q);
  // lang=default 讓地名用當地的文字,不跟著瀏覽器的語言變成英文;服務不接受這個參數時退回原本的查法
  let r=await fetch(url+'&lang=default');if(!r.ok)r=await fetch(url);
  if(!r.ok)throw new Error(r.status);const d=await r.json();
  const seen=new Set(),out=[];
  for(const f of d.features||[]){const p=f.properties,[lon,lat]=f.geometry.coordinates;
   const where=[p.city,p.county,p.state,p.country].filter((v,i,a)=>v&&v!==p.name&&a.indexOf(v)===i).join('、');
   const key=lat.toFixed(1)+','+lon.toFixed(1);if(seen.has(key))continue;seen.add(key);out.push({name:p.name||q,where,lat,lon})}
  return {list:out.slice(0,6)}}
 catch(err){return {list:[]}}}
function offer(list,note,where,act){where.hits.innerHTML='';where.msg.textContent=note;
 list.forEach(o=>{const b=document.createElement('button');b.type='button';
  b.textContent=o.name+(o.where?'('+o.where+')':'');b.addEventListener('click',()=>act(o.name,+(+o.lat).toFixed(4),+(+o.lon).toFixed(4)));where.hits.appendChild(b)})}
const MISS=q=>'找不到「'+q+'」。臺灣請輸入鄉鎮市區(例如 東港、恆春鎮);國外請輸入城市的中文或英文名(例如 東京、Paris),或直接輸入座標,例如 35.68,139.69';
// 「地點預報」的搜尋欄與總覽的「加入比較」共用:邊打邊列出內建清單裡符合的地點(注音或倉頡選字時先不動),送出才查外部的地名服務
function searchBox(f,input,where,act){
 input.addEventListener('input',e=>{if(e.isComposing)return;const found=localHits(input.value);
  if(found.length)offer(found,'請選擇:',where,act);else{where.hits.innerHTML='';where.msg.textContent=''}});
 input.addEventListener('compositionend',()=>{const found=localHits(input.value);if(found.length)offer(found,'請選擇:',where,act)});
 f.addEventListener('submit',async e=>{e.preventDefault();const q=input.value.trim();where.hits.innerHTML='';if(!q)return;
  where.msg.textContent='搜尋中';const r=await lookup(q);
  if(r.direct){where.msg.textContent='';act(r.list[0].name,r.list[0].lat,r.list[0].lon);return}
  if(r.list.length){offer(r.list,'請選擇:',where,act);return}
  where.msg.textContent=MISS(q)})}
searchBox(form,box,{hits,msg},open);
saved().forEach(p=>addTab(p.name,p.lat,p.lon));
route();

// ---- 總覽:自選要一起比較的地點 ----
const ov=document.getElementById('ovbody'),ovd=document.getElementById('ovdata');
const OV=ovd?JSON.parse(ovd.textContent):null,ORIGINAL=ov?ov.innerHTML:'';
const pickc=document.getElementById('pickchips'),addf=document.getElementById('addcmp'),addq=document.getElementById('addq'),
 addhits=document.getElementById('addhits'),addmsg=document.getElementById('addmsg');
// 記在瀏覽器裡:off 是取消勾選的預設地點,on 是勾選要比較的、自己加入的地點
function cmp(){try{return Object.assign({off:[],on:[]},JSON.parse(localStorage.getItem('compare')||'{}'))}catch(e){return {off:[],on:[]}}}
function keepCmp(c){try{localStorage.setItem('compare',JSON.stringify(c))}catch(e){}}
function customs(){const seen=new Set(OV?OV.names:[]);return saved().filter(p=>!seen.has(p.name)&&seen.add(p.name))}
function chosen(){const c=cmp();return {pre:OV?OV.names.filter(n=>!c.off.includes(n)):[],cus:customs().filter(p=>c.on.includes(p.name))}}
function drawChips(){if(!pickc||!OV)return;const c=cmp();pickc.innerHTML='';
 const chip=(name,on,custom)=>{const l=document.createElement('label');if(custom)l.className='custom';
  const i=document.createElement('input');i.type='checkbox';i.checked=on;i.addEventListener('change',()=>toggleCmp(name,custom,i));
  l.append(i,document.createTextNode(name));pickc.append(l)};
 OV.names.forEach(n=>chip(n,!c.off.includes(n),false));customs().forEach(p=>chip(p.name,c.on.includes(p.name),true))}
function toggleCmp(name,custom,input){const c=cmp();
 if(custom)c.on=input.checked?[...new Set([...c.on,name])]:c.on.filter(n=>n!==name);
 else c.off=input.checked?c.off.filter(n=>n!==name):[...new Set([...c.off,name])];
 if(!OV.names.some(n=>!c.off.includes(n))&&!customs().some(p=>c.on.includes(p.name))){input.checked=true;addmsg.textContent='至少要選一個地點。';return}
 addmsg.textContent='';keepCmp(c);applyCmp()}
// 只有預設地點:直接在畫面上顯示或隱藏,跨地點的結論與橫幅用事先準備好的,不必跑分析程式
function showPresets(pre){const sel=new Set(pre);
 ov.querySelectorAll('[data-place]').forEach(el=>{el.hidden=!sel.has(el.dataset.place)});
 let any=false;ov.querySelectorAll('tr[data-places]').forEach(tr=>{const ps=JSON.parse(tr.dataset.places).filter(n=>sel.has(n));
  tr.hidden=!ps.length;if(ps.length){any=true;tr.querySelector('.where').textContent=ps.join('、')}});
 const hz=ov.querySelector('.hazards'),no=ov.querySelector('.nohaz');if(hz)hz.hidden=!any;if(no)no.hidden=any;
 const l=ov.querySelector('#ovlede'),key=pre.join('|');
 if(l){if(key in OV.ledes){const t=OV.ledes[key];l.textContent=t;l.hidden=!t}else if(pre.length!==OV.names.length)l.hidden=true}
 const h=ov.querySelector('.hero'),d=OV.heroes[pre[0]];
 if(h&&d){h.dataset.days=JSON.stringify(d.rows);h.dataset.name=pre[0];
  if(d.low)h.dataset.low='1';else delete h.dataset.low;if(d.south)h.dataset.south='1';else delete h.dataset.south}}
function refreshOv(){markDays();if(window.heroPaint)heroPaint();if(window.fxRun)fxRun()}
// 加入的地點若還沒在「地點預報」裡算過,順便填上,點過去就不必再等
function fillTab(p,v){secs().forEach(s=>{if(s.dataset.pending&&s.dataset.name===p.name&&s.dataset.lat==p.lat){
 delete s.dataset.pending;s.innerHTML='<h1>'+esc(p.name)+'</h1>'+v.block}})}
let allData=null,cmpGen=0,cmpSig='';
// 畫好的比較表記在這個分頁裡(跟著網站這一版的資料):重新整理時不必等分析程式重新載入
const ovKey=sig=>'ov:'+built+':'+sig;
// 抓不到時說明原因,附上「再試一次」(例如查詢太頻繁,等一分鐘再按)
function retry(text){addmsg.textContent=text;const b=document.createElement('button');b.type='button';b.textContent='再試一次';
 b.addEventListener('click',()=>{cmpSig='';applyCmp()});addmsg.append(b)}
async function applyCmp(){if(!ov||!OV)return;const {pre,cus}=chosen(),index={};secs().forEach((s,i)=>{index[s.dataset.name]=i});
 // 加入的地點在「地點預報」的分頁位置也算在內:移除別的地點後位置會變,表格裡的連結要跟著改
 const sig=JSON.stringify([pre,cus,cus.map(p=>index[p.name])]);
 if(sig===cmpSig)return;cmpSig=sig;const my=++cmpGen;
 const ready=pre.join('|') in OV.ledes||pre.length===OV.names.length;
 if(!cus.length&&ready){if(ov.dataset.mode==='custom'){ov.innerHTML=ORIGINAL;delete ov.dataset.mode}addmsg.textContent='';showPresets(pre);refreshOv();return}
 // 有加入的地點:在瀏覽器裡算出它們的預報,連同預設地點(資料在 data.json)用同一份 Python 重畫。
 // 算好之前先把預設地點照勾選顯示,讀者馬上看得到變化
 const say=t=>{if(my===cmpGen)addmsg.textContent=t};
 try{const c=JSON.parse(sessionStorage.getItem(ovKey(sig))||'null');
  if(c&&Date.now()-c.at<108e5){ov.innerHTML=c.html;ov.dataset.mode='custom';addmsg.textContent='';refreshOv();return}}catch(x){}
 // 加入的地點還沒算好,不能先說「選的地點都沒有危害」
 if(ov.dataset.mode!=='custom'&&pre.length){showPresets(pre);const no=ov.querySelector('.nohaz');if(no)no.hidden=true;refreshOv()}
 try{allData=allData||await(await fetch('data.json',{cache:'no-store'})).json();
  const datas={},missed=[];let why='',err=null;pre.forEach(n=>{if(allData.places&&allData.places[n])datas[n]=allData.places[n]});
  // 某個加入的地點這次抓不到(例如查詢太頻繁),先比較其他地點,附上「再試一次」
  for(const p of cus){try{const v=await ensure(p.name,p.lat,p.lon,say);datas[p.name]=v.data;fillTab(p,v)}catch(e){missed.push(p.name);err=e;why=explain(e)}}
  if(my!==cmpGen)return;
  const names=[...pre,...cus.map(p=>p.name)].filter(n=>datas[n]);
  if(!names.length)throw err||new Error('沒有可用的資料');
  say('正在整理比較表。');const p=await engine();if(my!==cmpGen)return;
  const html=p.globals.get('overview')(JSON.stringify(names),JSON.stringify(datas),JSON.stringify(index));
  ov.innerHTML=html;ov.dataset.mode='custom';
  if(missed.length){cmpSig='';retry(missed.join('、')+' 這次沒有加入比較:'+why)}
  else{say('');try{sessionStorage.setItem(ovKey(sig),JSON.stringify({at:Date.now(),html}))}catch(x){}}
  refreshOv()}
 catch(e){cmpSig='';if(my!==cmpGen)return;
  // 分析程式或資料載入失敗:至少把勾選的預設地點顯示對(只勾了加入的地點時,先顯示全部預設地點)
  if(ov.dataset.mode==='custom'){ov.innerHTML=ORIGINAL;delete ov.dataset.mode}showPresets(pre.length?pre:OV.names);refreshOv();
  retry('無法加入比較:'+explain(e))}}
function addCmp(name,lat,lon){addq.value='';addhits.innerHTML='';addmsg.textContent='';
 if(OV.names.includes(name)){const c=cmp();c.off=c.off.filter(n=>n!==name);keepCmp(c);drawChips();applyCmp();return}
 const list=saved();if(!list.some(p=>p.name===name&&p.lat==lat)){list.push({name,lat,lon});keep(list);addTab(name,lat,lon)}
 const c=cmp();if(!c.on.includes(name))c.on.push(name);keepCmp(c);drawChips();applyCmp()}
if(addf)searchBox(addf,addq,{hits:addhits,msg:addmsg},addCmp);
drawChips();
// 只有預設地點時馬上套用;有加入的地點時,等讀者真的看總覽頁才算,不在別頁偷偷下載分析程式
const onOverview=()=>parse().page==='overview';
window.addEventListener('hashchange',()=>{if(onOverview())applyCmp()});
if(onOverview()||!chosen().cus.length)applyCmp();
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
_SUN = f'<g style="fill:var(--sun);stroke:var(--sun);stroke-width:3;stroke-linecap:round"><circle cx="24" cy="24" r="8"/><g class="rays">{_RAYS}</g></g>'


def _drops(xs: list[float], length: float = 7, top: float = 33) -> str:
    return '<g class="rain" style="stroke:var(--rain);stroke-width:3.2;stroke-linecap:round">' + "".join(
        f'<line x1="{x}" y1="{top}" x2="{x - length * 0.35:.1f}" y2="{top + length}"/>' for x in xs
    ) + "</g>"


_CLOUD_STYLE = {"cl": "fill:var(--cloud)", "cl2": "fill:var(--cloud);opacity:.55", "dk": "fill:var(--cloud-dark)"}


def _cloud(kind: str, scale: float = 1.0, dx: float = 0, dy: float = 0) -> str:
    # 外層的 <g> 留給動畫用:CSS 的 transform 會蓋掉 SVG 的 transform 屬性,所以不能直接動 path
    return f'<g class="cloud"><path style="{_CLOUD_STYLE[kind]}" d="{_CLOUD}" transform="translate({dx} {dy}) scale({scale})"/></g>'


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
            f'<div class="stage s{stage.index} {stage.tone}" style="grid-column:span {len(stage.days)};--o:{order}">'
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
        "時段的顏色:綠色是降雨系統、藍色是冷空氣、黃褐色是天氣穩定;時段名稱下方的小字是白話說明。</p>"
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
        f'<div class="stg {s.tone}"><p class="when">時段{ORDINALS[s.index - 1]} {esc(s.span)}</p><h3>{esc(s.headline)}</h3>'
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
                f'<div class="reason {s.tone}"><h3>時段{ORDINALS[s.index - 1]} {esc(s.span)} {esc(s.headline)}</h3>'
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


def _hero_rows(days: list[dict]) -> list[dict]:
    """橫幅需要的逐日資料:天氣用語、高低溫、降雨機率、風、雲量。"""
    p50 = lambda v, default: round(v["p50"]) if isinstance(v, dict) else default
    return [
        {
            "day": d["day"], "sky": d["sky"], "hi": p50(d["tmax"], 0), "lo": p50(d["tmin"], 0), "pop": round(d["pop"] * 100),
            "wind": p50(d.get("wind"), 0), "cloud": p50(d.get("cloud"), 50),
        }
        for d in days
    ]


def _hero(days: list[dict], name: str = "", slim: bool = False, low: bool = False, south: bool = False) -> str:
    """天氣景象橫幅:太陽、雲、雨的動畫畫面。days 是 report.to_json 裡的逐日資料。

    這裡只放每天的資料,畫面由瀏覽器端的 HERO_JS 依「讀者的今天」畫出來;動畫用開源的
    Anime.js(MIT 授權),載入失敗或讀者關閉動態效果時,仍會顯示靜止的畫面。
    """
    data = esc(json.dumps(_hero_rows(days), ensure_ascii=False, separators=(",", ":")))
    label = f' data-name="{esc(name)}"' if name else ""
    flags = (" data-slim" if slim else "") + (' data-low="1"' if low else "") + (' data-south="1"' if south else "")
    return f'<div class="hero" data-days="{data}"{label}{flags}></div>'


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
        _hero([asdict(f) for f in forecast], low=any(r["kind"] == "low" for r in asdict(brief)["roles"]), south=lat < 0)
        + _week_box(asdict(week) if week else None, '<p class="sub">每天的原因和準備建議在下方「白話總結」分頁;想看專業的分析依據,請看其他分頁。</p>')
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
placeholder="查其他地點:東港、恆春鎮、東京、New York 或座標 22.37,120.59" aria-label="搜尋地點"><button>查詢</button>
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
    "資料超過 3 小時就重算(實際上 GitHub 每 4 到 7 小時才觸發一次)。資料太舊時,右上角會出現「立即重算」,按下去會在你的瀏覽器裡用最新資料重算目前的地點,不必等排程。頁面右上角會寫資料是幾小時前的;超過 9 小時沒更新會出現提醒。開著的頁面每 10 分鐘檢查一次,有新預報會自動重新載入。</li>"
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

ANIME_URL = "https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.2/anime.min.js"

# 天氣景象橫幅。依當天的天氣用語決定畫什麼:有沒有太陽、雲多不多、雨下多大;風越大,雲飄得越快、雨越斜。
HERO_JS = """
(function(){
const SCENE={'晴':[1,0,0],'晴時多雲':[1,1,0],'多雲':[0,2,0],'陰':[0,3,0],'晴偶陣雨':[1,1,1],'多雲偶雨':[0,2,1],
 '可能有雨':[0,3,2],'有雨':[0,3,3],'明顯降雨':[0,3,4],'大雨':[0,3,5]};
const CLOUD='M13 34a8 8 0 0 1-.6-16A11 11 0 0 1 33.5 15 9.5 9.5 0 0 1 35 34z';
const calm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
let live=[];
function iso(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function draw(box){let days;try{days=JSON.parse(box.dataset.days)}catch(e){return}
 if(!days.length)return;const today=iso(new Date());
 const d=days.find(x=>x.day===today)||days.find(x=>x.day>today)||days[0];
 const [sun,cloud,rain]=SCENE[d.sky]||[0,2,0];
 const tone=rain>=2?'wet':cloud>=3?'grey':cloud>=2?'mild':'clear';
 let g='<svg viewBox="0 0 1200 240" preserveAspectRatio="xMidYMid slice" aria-hidden="true">';
 g+='<rect class="sky" width="1200" height="240"/>';
 if(sun){g+='<g class="sun" transform="translate(830 72)"><circle class="glow" r="70"/><g class="rays">';
  for(let i=0;i<12;i++)g+='<line x1="0" y1="-44" x2="0" y2="-58" transform="rotate('+i*30+')"/>';
  g+='</g><circle class="disc" r="34"/></g>'}
 const n=[0,3,5,7][cloud];
 for(let i=0;i<n;i++){const sc=2.2+((i*37)%5)*0.55,x=(i*211+90)%1150,y=18+((i*53)%4)*22;
  g+='<g class="drift" data-i="'+i+'"><path class="cl'+(cloud>=3&&i%2?' dk':'')+'" d="'+CLOUD+'" transform="translate('+x+' '+y+') scale('+sc.toFixed(2)+')"/></g>'}
 const slant=Math.min(60,d.wind*1.6),drops=[0,14,26,40,56,72][rain];
 if(drops){g+='<g class="rain">';
  for(let i=0;i<drops;i++){const x=(i*163+40)%1260-30,y=((i*71)%200)-20,len=16+rain*4;
   g+='<line class="drop" x1="'+x+'" y1="'+y+'" x2="'+(x-slant*len/60).toFixed(1)+'" y2="'+(y+len)+'"/>'}
  g+='</g>'}
 g+='<path class="hill far" d="M0 240V186c120-34 210-8 330-30s200-52 320-28 190 44 300 20 160-40 250-22v94z"/>';
 g+='<path class="hill" d="M0 240V208c150-26 260 6 400-12s240-30 380-10 250 26 420 4v50z"/></svg>';
 const word=d.day===today?'今天':d.day>today?'接下來':'最近一筆';
 const who=box.dataset.name?box.dataset.name+' · ':'';
 const snowy=d.hi<=2&&d.pop>=20;
 g+='<div class="cap"><span class="when">'+who+word+' '+(+d.day.slice(5,7))+'/'+(+d.day.slice(8,10))+'</span><b>'+d.sky+(snowy?'<small>(可能是雪)</small>':'')+'</b>'
  +'<span class="t">'+d.hi+'° <i>/ '+d.lo+'°</i></span><span class="p">降雨機率 '+d.pop+'%</span></div>';
 box.className='hero '+tone;box.innerHTML=g;box.dataset.wind=d.wind;
 Object.assign(box.dataset,{sky:d.sky,pop:d.pop,hi:d.hi,lo:d.lo,tone})}
function move(box){if(calm||!window.anime)return;
 const wind=+box.dataset.wind||0,speed=Math.max(0.5,Math.min(3,wind/14));
 box.querySelectorAll('.drift').forEach((el,i)=>{live.push(anime({targets:el,translateX:[-60-i*12,80+i*18],duration:(26000+i*5200)/speed,
  direction:'alternate',easing:'easeInOutSine',loop:true}))});
 const rays=box.querySelector('.rays');if(rays)live.push(anime({targets:rays,rotate:360,duration:90000,easing:'linear',loop:true}));
 const glow=box.querySelector('.glow');if(glow)live.push(anime({targets:glow,scale:[1,1.12],opacity:[0.22,0.36],duration:4200,direction:'alternate',easing:'easeInOutSine',loop:true}));
 const drops=box.querySelectorAll('.drop');
 drops.forEach((el,i)=>{const a=anime({targets:el,translateY:[-70,270],duration:760+(i*53)%420,easing:'linear',loop:true});
  a.seek(((i*197)%1000)/1000*a.duration);live.push(a)})}
// 只讓目前看得到的那個地點動,切換地點時重畫
window.heroPause=function(){live.forEach(a=>a.pause());live=[]};
window.heroPaint=function(){live.forEach(a=>a.pause());live=[];
 document.querySelectorAll('.hero').forEach(draw);
 const on=document.querySelector('.page.on article.place.on .hero')||document.querySelector('.page.on[data-page=overview] .hero');if(on)move(on);
 if(window.hero3d)hero3d()};
window.addEventListener('hashchange',()=>setTimeout(heroPaint,0));
heroPaint();
})();
"""

THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.min.js"

# 華麗版的天氣背景:用開源的 three.js(MIT 授權)在整個頁面後面畫一個 WebGL 天氣場景——
# 上緣依天氣染色的天空、有景深與視差的雲、帶光暈與光芒的太陽、隨風傾斜的雨絲、大雨時的閃電、
# 冰點以下的雪,跟著畫面上那個地點今天的天氣變化。橫幅變成半透明的窗,透出後面的天氣。
# 整頁只有一個畫布(一個 WebGL),解析度最多 1.25 倍。three.js 壓縮後約 166 KB:頁面載入完、
# 瀏覽器閒下來才下載;讀者關閉動態效果、開省流量、網路是 2G、記憶體小於 2 GB 或不支援 WebGL
# 時完全不載入。頁首有開關,讀者可以自己關掉(記在瀏覽器裡)。
HERO3D_JS = """
const THREE_URL='__THREE_URL__';
const calm=matchMedia('(prefers-reduced-motion: reduce)').matches,conn=navigator.connection||{};
const weak=conn.saveData||/2g$/.test(conn.effectiveType||'')||(navigator.deviceMemory&&navigator.deviceMemory<2);
function hasGL(){try{const c=document.createElement('canvas');return !!(c.getContext('webgl2')||c.getContext('webgl'))}catch(e){return false}}
function idle(f){'requestIdleCallback' in window?requestIdleCallback(f,{timeout:3000}):setTimeout(f,800)}
const SCENE={'晴':[1,0,0],'晴時多雲':[1,1,0],'多雲':[0,2,0],'陰':[0,3,0],'晴偶陣雨':[1,1,1],'多雲偶雨':[0,2,1],
 '可能有雨':[0,3,2],'有雨':[0,3,3],'明顯降雨':[0,3,4],'大雨':[0,3,5]};
let T=null,bg=null,key='',last=null;
const toggle=document.getElementById('fxtoggle');
function wanted(){try{return localStorage.getItem('bgfx')!=='off'}catch(e){return true}}
function label(){if(!toggle)return;const on=wanted();toggle.textContent='背景動畫:'+(on?'開':'關');toggle.setAttribute('aria-pressed',on)}
const source=()=>document.querySelector('.page.on article.place.on .hero')||document.querySelector('.page.on[data-page=overview] .hero');
// 由橫幅的 heroPaint 在每次換頁、換地點後呼叫:背景跟著畫面上那個地點今天的天氣
window.hero3d=function(){
 document.querySelectorAll('.hero.see').forEach(h=>h.classList.remove('see'));
 if(!T)return;
 if(!wanted()){if(bg){bg.stop();bg=null;key=''}return}
 const box=source()||last;
 if(box&&box.dataset.sky){last=box;const k=['sky','hi','pop','wind','tone','low'].map(x=>box.dataset[x]).join('|');
  if(!bg||k!==key){if(bg)bg.stop();try{bg=scene(T,Object.assign({},box.dataset));key=k}catch(e){bg=null;key=''}}}
 const on=source();if(bg&&on){on.classList.add('see');if(window.heroPause)heroPause()}
};
if(!calm&&!weak&&hasGL()){
 if(toggle){toggle.hidden=false;label();toggle.addEventListener('click',()=>{try{localStorage.setItem('bgfx',wanted()?'off':'on')}catch(e){}label();hero3d()})}
 const go=()=>import(THREE_URL).then(m=>{T=m;hero3d()}).catch(()=>{if(toggle)toggle.hidden=true});
 document.readyState==='complete'?idle(go):addEventListener('load',()=>idle(go));
}
function rnd(seed){let s=seed%2147483647||7;return()=>(s=s*16807%2147483647)/2147483647}
function cloudTexture(T,seed){const c=document.createElement('canvas');c.width=256;c.height=128;const g=c.getContext('2d'),r=rnd(seed);
 for(let i=0;i<16;i++){const x=46+r()*164,mid=1-Math.abs(x-128)/90,y=82-r()*28*mid,rad=18+r()*26+mid*14;
  const gr=g.createRadialGradient(x,y,0,x,y,rad);gr.addColorStop(0,'rgba(255,255,255,.95)');gr.addColorStop(.55,'rgba(255,255,255,.55)');
  gr.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=gr;g.beginPath();g.arc(x,y,rad,0,7);g.fill()}
 const t=new T.CanvasTexture(c);t.colorSpace=T.SRGBColorSpace;return t}
// 整頁的天氣背景:一個固定在內容後面的畫布。天空在畫面上緣依天氣染色,雲在上方飄,
// 雨或雪落滿整個畫面,大雨時閃電;滑鼠移動時,近的雲動得比遠的多。
// 往下捲動 = 從高空降到地面(對應綜觀分析的 250 → 500 → 850 hPa → 地面):頁首是噴流的
// 高速氣流線;往下穿過雲層時有一層霧;到了頁尾,下雨天地面濺起水花、晴天有斜射的光束、
// 下雪天積起一層雪。這一週有低壓或颱風接近時,上方多一個慢慢旋轉的螺旋雲帶。
function scene(T,d){
 const [sun,cloud,rain0]=SCENE[d.sky]||[0,2,0],wind=+d.wind||0,cold=+d.hi<=2&&+d.pop>=20;
 const dark=matchMedia('(prefers-color-scheme: dark)').matches,tone=d.tone||'mild';
 const canvas=document.createElement('canvas');canvas.className='bg3d';canvas.setAttribute('aria-hidden','true');document.body.prepend(canvas);
 const R=new T.WebGLRenderer({canvas,alpha:true,antialias:false,powerPreference:'low-power'});
 R.setPixelRatio(Math.min(devicePixelRatio||1,1.25));
 const S=new T.Scene(),cam=new T.OrthographicCamera(0,1,1,0,-10,10);
 let W=1,H=1,U=200;const clouds=[],r=rnd(+(d.hi||7)*131+(+d.pop||3)*17+(d.sky||'').length),u=(v)=>({value:v});
 // 天色:上緣染色,往下漸漸透明,不影響內文
 const SKY={clear:[.55,.76,.93],mild:[.66,.77,.86],grey:[.6,.67,.72],wet:[.42,.5,.55]},DSKY={clear:[.1,.22,.34],mild:[.12,.21,.28],grey:[.15,.19,.22],wet:[.1,.14,.17]};
 const skyMesh=new T.Mesh(new T.PlaneGeometry(1,1),new T.ShaderMaterial({transparent:true,depthWrite:false,
  uniforms:{uC:u(new T.Vector3(...((dark?DSKY:SKY)[tone]||SKY.mild))),uA:u(dark?.6:.5)},
  vertexShader:'varying float v;void main(){v=uv.y;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
  fragmentShader:'uniform vec3 uC;uniform float uA;varying float v;void main(){gl_FragColor=vec4(uC,uA*smoothstep(.45,1.,v));}'}));
 skyMesh.renderOrder=-1;S.add(skyMesh);
 // 太陽:右上角,有光暈與緩慢旋轉的光芒
 let sunMesh=null;
 if(sun){sunMesh=new T.Mesh(new T.PlaneGeometry(1,1),new T.ShaderMaterial({transparent:true,depthWrite:false,
  uniforms:{uT:u(0),uC:u(new T.Color(dark?0xf3c04a:0xffbf3a)),uK:u(cloud?0.75:1)},
  vertexShader:'varying vec2 v;void main(){v=uv-.5;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
  fragmentShader:'uniform float uT,uK;uniform vec3 uC;varying vec2 v;void main(){float r=length(v)*2.,a=atan(v.y,v.x);'+
   'float disc=smoothstep(.2,.17,r),glow=exp(-r*4.)*.75,ray=(pow(max(0.,sin(a*10.+uT*.25)),16.)*.28+pow(max(0.,sin(a*7.-uT*.18)),22.)*.2)*smoothstep(.95,.2,r)*smoothstep(.16,.24,r);'+
   'float k=(disc+glow+ray)*uK;vec3 c=mix(vec3(1.,.95,.78),uC,clamp(disc+glow*.35,0.,1.));gl_FragColor=vec4(c,clamp(k,0.,1.));}'}));
  sunMesh.renderOrder=0;S.add(sunMesh)}
 // 雲:三種形狀、遠近三層,只在畫面上方
 const n=[0,6,11,16][cloud],tex=[cloudTexture(T,11),cloudTexture(T,37),cloudTexture(T,73)];
 const shade={clear:[1,1,1],mild:[.97,.98,1],grey:[.86,.89,.91],wet:[.68,.73,.76]}[tone]||[.9,.92,.94];
 for(let i=0;i<n;i++){const depth=i/Math.max(1,n-1),m=new T.Mesh(new T.PlaneGeometry(1,1),new T.MeshBasicMaterial({map:tex[i%3],transparent:true,depthWrite:false,
   opacity:.4+depth*.42,color:new T.Color(...shade.map(c=>c*(dark?.55:1)*(1-depth*(cloud>=3?.18:.06))))}));
  m.userData={depth,x:r(),y:.08+r()*.85,w:.8+r()*.6,speed:(4+depth*16)*(.5+Math.min(wind,40)/14)};m.renderOrder=1+depth*10;S.add(m);clouds.push(m)}
 // 雨:位置在 GPU 上依時間算;數量依畫面面積調整,上限 1600 條
 const base=cold?0:[0,160,340,560,820,1100][rain0];let rainU=null,snowU=null;
 const area=()=>Math.max(.4,Math.min(2.2,innerWidth*innerHeight/(1200*700)));
 if(base){const drops=Math.min(1600,Math.round(base*area())),pos=new Float32Array(drops*6),seed=new Float32Array(drops*8),end=new Float32Array(drops*2);
  for(let i=0;i<drops;i++){const a=[r()*3000,r()*2000,340+r()*320,7+r()*15];for(let k=0;k<2;k++){seed.set(a,(i*2+k)*4);end[i*2+k]=k}}
  const g=new T.BufferGeometry();g.setAttribute('position',new T.BufferAttribute(pos,3));g.setAttribute('aSeed',new T.BufferAttribute(seed,4));g.setAttribute('aEnd',new T.BufferAttribute(end,1));
  rainU={uT:u(0),uW:u(1),uH:u(1),uU:u(1),uS:u(Math.min(.9,wind/45)),uA:u(dark?.4:.34),
   uC:u((dark||tone==='wet')?new T.Vector3(.86,.92,.97):new T.Vector3(.36,.5,.6))};
  const m=new T.LineSegments(g,new T.ShaderMaterial({transparent:true,depthWrite:false,uniforms:rainU,
   vertexShader:'uniform float uT,uW,uH,uU,uS;attribute vec4 aSeed;attribute float aEnd;varying float vA;void main(){'+
    'float len=aSeed.w*uU,span=uH+len+40.,fall=mod(aSeed.y+uT*aSeed.z*uU,span);vec2 p=vec2(mod(aSeed.x+fall*uS,uW+80.)-40.,uH+len-fall);'+
    'p+=aEnd*vec2(uS*len,-len);vA=aEnd*(.45+aSeed.w/22.);gl_Position=projectionMatrix*modelViewMatrix*vec4(p,.5,1.);}',
   fragmentShader:'uniform float uA;uniform vec3 uC;varying float vA;void main(){gl_FragColor=vec4(uC,vA*uA);}'}));
  m.renderOrder=20;S.add(m)}
 // 雪:冰點以下又有降水時
 if(cold){const k=Math.min(900,Math.round([0,120,220,320,420,520][Math.max(1,rain0)]*area())),pos=new Float32Array(k*3),sd=new Float32Array(k*4);
  for(let i=0;i<k;i++)sd.set([r()*3000,r()*2000,18+r()*30,1.5+r()*2.5],i*4);
  const g=new T.BufferGeometry();g.setAttribute('position',new T.BufferAttribute(pos,3));g.setAttribute('aSeed',new T.BufferAttribute(sd,4));
  snowU={uT:u(0),uW:u(1),uH:u(1),uP:u(R.getPixelRatio())};
  const m=new T.Points(g,new T.ShaderMaterial({transparent:true,depthWrite:false,uniforms:snowU,
   vertexShader:'uniform float uT,uW,uH,uP;attribute vec4 aSeed;void main(){float fall=mod(aSeed.y+uT*aSeed.z,uH+20.);'+
    'vec2 p=vec2(mod(aSeed.x+sin(uT*.8+aSeed.x)*12.,uW),uH+10.-fall);gl_PointSize=aSeed.w*uP*1.6;gl_Position=projectionMatrix*modelViewMatrix*vec4(p,.6,1.);}',
   fragmentShader:'void main(){float d=length(gl_PointCoord-.5);gl_FragColor=vec4(1.,1.,1.,smoothstep(.5,.15,d)*.85);}'}));
  m.renderOrder=20;S.add(m)}
 // 閃電:大雨時偶爾一次,背景微微亮起,上方出現一道分岔的電光(亮度壓低,不刺眼)
 const storm=rain0>=4&&!cold;let flash=null,bolt=null,nextFlash=3+r()*5,flashAt=-9;
 if(storm){flash=new T.Mesh(new T.PlaneGeometry(1,1),new T.MeshBasicMaterial({color:0xffffff,transparent:true,opacity:0,depthWrite:false}));flash.renderOrder=30;S.add(flash);
  bolt=new T.Line(new T.BufferGeometry(),new T.LineBasicMaterial({color:0xf4f8ff,transparent:true,opacity:0}));bolt.renderOrder=31;S.add(bolt)}
 function newBolt(){const pts=[];let x=W*(.1+r()*.8),y=H;while(y>H-U*1.8){pts.push(new T.Vector3(x,y,0));y-=U*(.12+r()*.16);x+=(r()-.5)*U*.35}
  bolt.geometry.dispose();bolt.geometry=new T.BufferGeometry().setFromPoints(pts)}
 // ---- 往下捲動的分層 ----
 const lowSys=d.low==='1',spin=d.south==='1'?-1:1;
 // 高空:噴流的高速氣流線,只在頁首附近
 const jetN=Math.round(70*area()),jp=new Float32Array(jetN*6),js=new Float32Array(jetN*8),je=new Float32Array(jetN*2);
 for(let i=0;i<jetN;i++){const a=[r()*3000,r(),220+r()*260,40+r()*90];for(let k=0;k<2;k++){js.set(a,(i*2+k)*4);je[i*2+k]=k}}
 const jg=new T.BufferGeometry();jg.setAttribute('position',new T.BufferAttribute(jp,3));jg.setAttribute('aSeed',new T.BufferAttribute(js,4));jg.setAttribute('aEnd',new T.BufferAttribute(je,1));
 const jetU={uT:u(0),uW:u(1),uH:u(1),uP:u(0),uA:u(dark?.4:.45),uC:u(dark?new T.Vector3(.8,.88,.95):new T.Vector3(.98,.99,1))};
 const jet=new T.LineSegments(jg,new T.ShaderMaterial({transparent:true,depthWrite:false,uniforms:jetU,
  vertexShader:'uniform float uT,uW,uH,uP;attribute vec4 aSeed;attribute float aEnd;varying float vA;void main(){'+
   'float x=mod(aSeed.x+uT*aSeed.z,uW+aSeed.w*2.)-aSeed.w;float y=uH*(.5+aSeed.y*.48)+uP*uH*1.4;'+
   'vec2 p=vec2(x+aEnd*aSeed.w,y+aEnd*2.);vA=aEnd;gl_Position=projectionMatrix*modelViewMatrix*vec4(p,.4,1.);}',
  fragmentShader:'uniform float uA,uP;uniform vec3 uC;varying float vA;void main(){gl_FragColor=vec4(uC,vA*uA*(1.-smoothstep(.12,.38,uP)));}'}));
 jet.renderOrder=15;S.add(jet);
 // 中層:穿過雲層時的一層霧,雲越多越濃
 const fogU={uT:u(0),uP:u(0),uA:u([0,.12,.24,.36][cloud]*(dark?.8:1)),uC:u(new T.Vector3(...shade.map(c=>c*(dark?.5:1))))};
 const fog=new T.Mesh(new T.PlaneGeometry(1,1),new T.ShaderMaterial({transparent:true,depthWrite:false,uniforms:fogU,
  vertexShader:'varying vec2 v;void main(){v=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
  fragmentShader:'uniform float uT,uP,uA;uniform vec3 uC;varying vec2 v;float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5);}'+
   'float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}'+
   'void main(){vec2 q=v*vec2(3.,2.)+vec2(uT*.03,uP*3.);float k=n(q)*.6+n(q*2.3)*.4;'+
   'float band=smoothstep(.25,.45,uP)*(1.-smoothstep(.55,.75,uP));gl_FragColor=vec4(uC,uA*band*smoothstep(.3,.8,k));}'}));
 fog.renderOrder=12;S.add(fog);
 // 地面:下雨時濺起的水花(一圈圈擴散的漣漪)
 let splashU=null;
 if(base){const k=Math.round([0,20,35,55,80,110][rain0]*area()),pos=new Float32Array(k*3),sd=new Float32Array(k*4);
  for(let i=0;i<k;i++)sd.set([r()*3000,r(),.7+r()*.9,8+r()*14],i*4);
  const g=new T.BufferGeometry();g.setAttribute('position',new T.BufferAttribute(pos,3));g.setAttribute('aSeed',new T.BufferAttribute(sd,4));
  splashU={uT:u(0),uW:u(1),uB:u(60),uP:u(0),uR:u(R.getPixelRatio()),uC:rainU.uC};
  const m=new T.Points(g,new T.ShaderMaterial({transparent:true,depthWrite:false,uniforms:splashU,
   vertexShader:'uniform float uT,uW,uB,uR;attribute vec4 aSeed;varying float vL;void main(){vL=fract(uT*aSeed.z+aSeed.y);'+
    'vec2 p=vec2(mod(aSeed.x+floor(uT*aSeed.z+aSeed.y)*577.,uW),uB+aSeed.y*36.);gl_PointSize=(6.+vL*aSeed.w*1.6)*uR*1.5;gl_Position=projectionMatrix*modelViewMatrix*vec4(p,.7,1.);}',
   fragmentShader:'uniform float uP;uniform vec3 uC;varying float vL;void main(){vec2 c=gl_PointCoord-.5;c.y*=3.6;float d=length(c);'+
    'float ring=smoothstep(.5,.43,d)*smoothstep(.3,.4,d);gl_FragColor=vec4(uC,ring*(1.-vL)*smoothstep(.62,.9,uP));}'}));
  m.renderOrder=21;S.add(m)}
 // 地面:晴天時從太陽斜射下來的光束
 let beamU=null;
 if(sun){beamU={uT:u(0),uP:u(0),uC:u(new T.Vector3(1.,.93,.7))};
  const m=new T.Mesh(new T.PlaneGeometry(1,1),new T.ShaderMaterial({transparent:true,depthWrite:false,uniforms:beamU,
   vertexShader:'varying vec2 v;void main(){v=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
   fragmentShader:'uniform float uT,uP;uniform vec3 uC;varying vec2 v;void main(){vec2 o=v-vec2(.92,1.05);float a=atan(o.y,o.x),r=length(o);'+
    'float b=pow(max(0.,sin(a*9.+uT*.05)),6.)*.6+pow(max(0.,sin(a*14.-uT*.04)),10.)*.4;'+
    'gl_FragColor=vec4(uC,b*smoothstep(1.4,.2,r)*.28*smoothstep(.5,.9,uP));}'}));
  m.renderOrder=11;S.add(m);beamU.mesh=m}
 // 地面:下雪天積起的一層雪
 let snowBed=null;
 if(cold){snowBed=new T.Mesh(new T.PlaneGeometry(1,1),new T.ShaderMaterial({transparent:true,depthWrite:false,uniforms:{uP:u(0)},
  vertexShader:'varying vec2 v;void main(){v=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
  fragmentShader:'uniform float uP;varying vec2 v;void main(){float edge=.55+sin(v.x*23.)*.06+sin(v.x*57.)*.03;'+
   'gl_FragColor=vec4(1.,1.,1.,smoothstep(edge,edge-.08,v.y)*.85*smoothstep(.6,1.,uP));}'}));
  snowBed.renderOrder=22;S.add(snowBed)}
 // 這一週有低壓或颱風接近:慢慢旋轉的螺旋雲帶(北半球逆時針、南半球順時針)
 let spiral=null;
 if(lowSys){spiral=new T.Mesh(new T.PlaneGeometry(1,1),new T.ShaderMaterial({transparent:true,depthWrite:false,
  uniforms:{uT:u(0),uP:u(0),uS:u(spin),uC:u(new T.Vector3(...shade.map(c=>c*(dark?.6:1))))},
  vertexShader:'varying vec2 v;void main(){v=uv-.5;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
  fragmentShader:'uniform float uT,uP,uS;uniform vec3 uC;varying vec2 v;void main(){float r=length(v)*2.,a=atan(v.y,v.x);'+
   'float arm=sin(a*2.-uS*log(r+.02)*5.5+uS*uT*.35);float body=smoothstep(.15,.65,arm*.5+.5)*smoothstep(1.,.25,r)*smoothstep(.04,.14,r);'+
   'gl_FragColor=vec4(uC,body*.5*(1.-smoothstep(.45,.8,uP)));}'}));
  spiral.renderOrder=3;S.add(spiral)}
 // 背景目前在哪個高度:左下角的小字
 const alt=document.createElement('div');alt.id='altitude';alt.setAttribute('aria-hidden','true');document.body.append(alt);
 const BANDS=[[.22,'高空 · 約 10 公里 · 250 hPa 噴流'],[.47,'中層 · 約 5.5 公里 · 500 hPa'],[.72,'低層 · 約 1.5 公里 · 850 hPa 雲層'],[2,'地面']];
 let pr=0,band='';
 function layout(){W=innerWidth||1;H=innerHeight||1;U=Math.max(150,Math.min(260,H*.28));R.setSize(W,H,false);
  Object.assign(cam,{left:0,right:W,top:H,bottom:0});cam.updateProjectionMatrix();
  skyMesh.scale.set(W,H,1);skyMesh.position.set(W/2,H/2,-1);
  if(sunMesh){const s=U*1.9;sunMesh.scale.set(s,s,1)}
  if(flash){flash.scale.set(W,H,1);flash.position.set(W/2,H/2,2)}
  if(rainU){rainU.uW.value=W;rainU.uH.value=H;rainU.uU.value=U/190}
  if(snowU){snowU.uW.value=W;snowU.uH.value=H}
  jetU.uW.value=W;jetU.uH.value=H;fog.scale.set(W,H,1);fog.position.set(W/2,H/2,1.5);
  if(splashU){splashU.uW.value=W;splashU.uB.value=innerWidth<761?64:14}
  if(beamU){beamU.mesh.scale.set(W,H,1);beamU.mesh.position.set(W/2,H/2,.2)}
  if(snowBed){snowBed.scale.set(W,U*.5,1);snowBed.position.set(W/2,U*.25+(innerWidth<761?52:0),2.2)}
  if(spiral){const sp=U*3.4;spiral.scale.set(sp,sp,1)}
  clouds.forEach(m=>{const sz=U*(.32+m.userData.depth*.42)*m.userData.w;m.scale.set(sz*2,sz,1);m.position.z=m.userData.depth})}
 layout();addEventListener('resize',layout);
 let px=0,tx=0,sy=0;const move=e=>{tx=e.clientX/(innerWidth||1)-.5};addEventListener('pointermove',move,{passive:true});
 let last=performance.now(),t=0,raf=0;
 function frame(now){const dt=Math.min(.05,(now-last)/1000);last=now;t+=dt;px+=(tx-px)*.05;sy+=((scrollY||0)-sy)*.12;
  const room=(document.documentElement.scrollHeight||0)-H;pr+=((room>200?Math.min(1,(scrollY||0)/room):0)-pr)*.1;
  jetU.uT.value=t;jetU.uP.value=pr;fogU.uT.value=t;fogU.uP.value=pr;
  if(splashU){splashU.uT.value=t;splashU.uP.value=pr}if(beamU){beamU.uT.value=t;beamU.uP.value=pr}if(snowBed)snowBed.material.uniforms.uP.value=pr;
  if(spiral){spiral.material.uniforms.uT.value=t;spiral.material.uniforms.uP.value=pr;spiral.position.set(W*.78-px*20,H-U*.9+pr*H*.9,.1)}
  const b=room>200?BANDS.find(x=>pr<x[0])[1]:'';if(b!==band){band=b;alt.textContent=b?'背景高度:'+b:'';alt.classList.toggle('on',!!b);
   clearTimeout(alt.t);if(b)alt.t=setTimeout(()=>alt.classList.remove('on'),2600)}
  if(sunMesh){sunMesh.material.uniforms.uT.value=t;sunMesh.position.set(W*.86-px*12,H-U*.55+pr*U*.4,0)}
  clouds.forEach(m=>{const q=m.userData;q.x+=q.speed*dt/W;if(q.x>1.2)q.x=-.2;
   m.position.x=W*q.x-px*(10+q.depth*40);m.position.y=H-U*(.2+q.y)+pr*H*(.5+q.depth*.9)});
  if(rainU)rainU.uT.value=t;if(snowU)snowU.uT.value=t;
  if(storm){if(t>nextFlash){flashAt=t;nextFlash=t+6+r()*7;newBolt()}
   const f=t-flashAt,lit=f<.09?1:f<.16?.15:f<.24?.8:Math.max(0,1-(f-.24)*3);
   flash.material.opacity=lit*.1;bolt.material.opacity=f<.5?lit*.85:0}
  R.render(S,cam);raf=requestAnimationFrame(frame)}
 raf=requestAnimationFrame(frame);
 return {stop(){cancelAnimationFrame(raf);removeEventListener('resize',layout);removeEventListener('pointermove',move);
  S.traverse(o=>{o.geometry&&o.geometry.dispose();o.material&&o.material.dispose()});tex.forEach(x=>x.dispose());R.dispose();canvas.remove();alt.remove()}};
}
"""

# 全站的動態效果:都用已經載入的 Anime.js 與 CSS,不另外載入函式庫。
# 只對畫面上看得到、而且還沒播過的元素播一次進場動畫;讀者關閉動態效果時全部略過。
FX_JS = """
(function(){
const calm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
function root(){const pg=document.querySelector('.page.on');if(!pg)return null;
 return pg.dataset.page==='place'?pg.querySelector('article.place.on'):pg}
// 時間軸上的圖示換成內嵌的圖形,太陽才轉得動、雨才落得下來(<use> 引用的圖形無法個別做動畫)
function liveIcons(box){box.querySelectorAll('.ribbon .wx svg.ico, .glance td.today svg.ico').forEach(svg=>{
 if(svg.dataset.live)return;const u=svg.querySelector('use');if(!u)return;
 const sym=document.getElementById((u.getAttribute('href')||'').slice(1));if(!sym)return;
 svg.innerHTML=sym.innerHTML;svg.dataset.live='1';svg.classList.add('live')})}
// 播完把行內樣式拿掉,交還給 CSS(例如「已過」的日子本來就是半透明)
const settle=els=>()=>els.forEach(e=>{e.style.opacity='';e.style.transform=''});
const base=el=>+el.dataset.op||1;
const hide=el=>{el.style.opacity=0},flat=a=>el=>{el.style.transform='scale'+a+'(0)'};
// 往下捲時,每個元素進到畫面才播:雨柱像雨滴落下彈起、溫度條長出來、卡片與表格列依序滑入
const KINDS=[
 ['.ribbon .d',hide,{opacity:[0,base],translateY:[18,0],delay:anime.stagger(60),duration:560}],
 ['.tcol i',flat('Y'),{scaleY:[0,1],delay:anime.stagger(60,{start:150}),duration:750,easing:'easeOutBack'}],
 ['.rcol i',flat('Y'),{scaleY:[0,1],delay:anime.stagger(60,{start:260}),duration:950,easing:'easeOutBounce'}],
 ['.grid td.cell i, .glance .pb i',flat('X'),{scaleX:[0,1],delay:anime.stagger(14,{start:120}),duration:650,easing:'easeOutQuart'}],
 ['h2, p.lede',hide,{opacity:[0,base],translateX:[-18,0],delay:anime.stagger(80),duration:600}],
 ['.week, .stg, .pd, .reason, .why-row, details.how, .sheet, svg[viewBox^="0 0 760"]',hide,{opacity:[0,base],translateY:[22,0],delay:anime.stagger(80),duration:620}],
 ['tbody tr, .view ul > li, .page ul > li',hide,{opacity:[0,base],translateX:[-12,0],delay:anime.stagger(30),duration:420}]];
let io=null;
function watch(){if(io)return io;
 io=new IntersectionObserver(es=>{const groups=new Map();
  es.forEach(e=>{if(!e.isIntersecting)return;io.unobserve(e.target);const k=e.target.dataset.fxk;if(!groups.has(k))groups.set(k,[]);groups.get(k).push(e.target)});
  groups.forEach((els,k)=>anime(Object.assign({targets:els,easing:'easeOutCubic',complete:settle(els)},KINDS[k][2])))},{threshold:.08});
 return io}
window.fxRun=function(){const box=root();if(!box)return;liveIcons(box);
 if(calm||!window.anime||!('IntersectionObserver' in window))return;const w=watch();
 KINDS.forEach(([sel,prime],k)=>box.querySelectorAll(sel).forEach(el=>{
  if(el.dataset.fxk||!el.getClientRects().length)return;el.dataset.fxk=k;el.dataset.op=getComputedStyle(el).opacity;prime(el);w.observe(el)}));
 // 橫幅上的高溫由低溫往上數,像電視氣象的數字跳動
 box.querySelectorAll('.hero .cap .t').forEach(t=>{if(t.dataset.fx)return;t.dataset.fx='1';
  const m=t.textContent.match(/(-?\\d+)°\\s*\\/\\s*(-?\\d+)°/);if(!m)return;const hi=+m[1],lo=+m[2],o={v:lo};
  anime({targets:o,v:hi,round:1,duration:900,easing:'easeOutQuad',update:()=>{t.firstChild.textContent=o.v+'° '}})})};
window.addEventListener('hashchange',()=>setTimeout(fxRun,0));
fxRun();
})();
"""

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


def _glance(names: list[str], by_place: dict[str, dict[str, dict]], index: dict[str, int]) -> str:
    """總覽的主角:各地點一週的天氣一覽表,一列一個地點、一欄一天,像地點預報的時間軸一樣簡潔。"""
    days = sorted({day for n in names for day in by_place[n]})
    head = "<th>地點</th>" + "".join(
        f'<th data-day="{day}" data-rel>{int(day[5:7])}/{int(day[8:10])}<span>{"一二三四五六日"[datetime.fromisoformat(day).weekday()]}</span></th>'
        for day in days
    )
    body = ""
    for i, n in enumerate(names):
        cells = ""
        for day in days:
            d = by_place[n].get(day)
            if not d:
                cells += f'<td data-day="{day}">–</td>'
                continue
            hi, lo, pop = d["tmax"]["p50"], d["tmin"]["p50"], d["pop"]
            tip = f"{n} {_day(day)}\n{d['sky']},高溫 {hi:.0f}°C、低溫 {lo:.0f}°C\n降雨機率 {pop:.0%},信心度{d.get('confidence_label', '')}"
            cells += (
                f'<td data-day="{day}" data-tip="{esc(tip)}">{_icon(d["sky"])}<span class="sk">{esc(d["sky"])}</span>'
                f'<span class="tt"><b>{hi:.0f}°</b> / {lo:.0f}°</span>'
                f'<span class="pb" aria-hidden="true"><i style="width:{pop * 100:.0f}%"></i></span><small>降雨 {pop:.0%}</small></td>'
            )
        body += f'<tr data-place="{esc(n)}"><th><a href="#/place/{index[n]}/summary">{esc(n)}</a></th>{cells}</tr>'
    return (
        '<p class="swipe">表格可以左右滑動,看後面幾天。</p>'
        f'<div class="scroll"><table class="glance"><caption>每格是天氣、最高 / 最低氣溫(°C)與降雨機率,綠色橫條越長越容易下雨。'
        "點地名看那個地點的完整預報;滑鼠移到格子上可以看信心度。</caption>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _why(names: list[str], payloads: dict[str, dict], index: dict[str, int]) -> str:
    """一覽表下方的「為什麼」:每個地點這一週由哪些天氣系統主導,專業名稱旁附白話。"""
    rows = []
    for i, n in enumerate(names):
        stages = payloads[n]["briefing"].get("stages") or []
        chips = "".join(
            f'<li class="{"fair" if st.get("stable") else "wet"}"><b>{esc(briefing._span(st["days"]))}</b>'
            f'{esc(st["headline"])}<small>{esc(plain.system_words(st["headline"]))}</small></li>'
            for st in stages
        )
        rows.append(f'<div class="why-row" data-place="{esc(n)}"><a href="#/place/{index[n]}/synoptic">{esc(n)}</a><ol>{chips}</ol></div>')
    return '<div class="why">' + "".join(rows) + "</div>"


def _cross_lede(names: list[str], payloads: dict[str, dict]) -> str:
    """一組地點合起來的結論。只有一個地點時,「各地都有雨」不通順,改用那個地點自己的結論。"""
    if len(names) == 1:
        return payloads[names[0]]["briefing"].get("lede", "")
    return briefing.island_summary(
        [
            {
                "name": n,
                "latitude": payloads[n]["location"].get("latitude") or 0.0,
                "longitude": payloads[n]["location"].get("longitude") or 0.0,
                "roles": payloads[n]["briefing"]["roles"],
                "pops": {d["day"]: d["pop"] for d in payloads[n]["days"]},
            }
            for n in names
        ]
    )


def _overview_body(names: list[str], payloads: dict[str, dict], index: dict[str, int] | None = None) -> str:
    """總覽頁選單下面的全部內容:橫幅、一覽表、注意事項、原因與各地的文字說明。

    index 是 {地名: 在「地點預報」裡的分頁位置};瀏覽器把自己加入的地點一起比較時,會用同一個
    函式在瀏覽器裡重畫(見 browser.overview),所以預設地點與加入的地點畫法完全一樣。
    """
    index = index or {n: i for i, n in enumerate(names)}
    by_place = {n: {d["day"]: d for d in payloads[n]["days"]} for n in names}
    merged: dict[tuple[str, str], list[str]] = {}
    for n in names:
        for hazard, when, _ in payloads[n]["briefing"]["hazards"]:
            merged.setdefault((hazard, when), []).append(n)
    # 每一列記下涉及哪些地點,瀏覽器只顯示有勾選的地點
    rows = "".join(
        f'<tr data-places="{esc(json.dumps(ps, ensure_ascii=False))}"><th>{esc(h)}</th><td>{esc(w)}</td><td class="where">{esc("、".join(ps))}</td></tr>'
        for (h, w), ps in merged.items()
    )
    hazards = (
        f'<div class="scroll hazards"{"" if merged else " hidden"}><table><thead><tr><th>危害</th><th>時間</th><th>地點</th></tr></thead><tbody>{rows}</tbody></table></div>'
        f'<p class="sub nohaz"{" hidden" if merged else ""}>選的地點這一週都沒有大雨、強風或明顯降溫的訊號。</p>'
    )
    texts = "".join(
        f'<li data-place="{esc(n)}"><a href="#/place/{index[n]}/plain">{esc(n)}</a>:{esc(payloads[n]["plain"]["week"]["headline"])}</li>'
        for n in names
        if payloads[n].get("plain", {}).get("week")
    )
    first = payloads[names[0]]
    hero = _hero(
        first["days"], names[0], slim=True,
        low=any(r["kind"] == "low" for r in first["briefing"]["roles"]), south=(first["location"].get("latitude") or 0) < 0,
    )
    return (
        f"{hero}{_glance(names, by_place, index)}"
        f"<h2>注意事項與劇烈天氣</h2>{hazards}"
        "<h2>為什麼:天氣系統怎麼演變</h2>"
        f'<p class="lede" id="ovlede">{esc(_cross_lede(names, payloads))}</p>'
        '<p class="sub">各地點這一週的分段。粗體是日期,接著是主導的天氣系統,小字是白話說明;點地名看那裡的綜觀分析。</p>'
        f"{_why(names, payloads, index)}"
        '<p class="sub">同一個系統在各地的影響不一樣,例如東北季風下,迎風的北部與東部有雨,背風的中南部通常不下。</p>'
        + (f"<h2>各地的文字說明</h2><ul class=\"texts\">{texts}</ul>"
           '<p class="sub">點地名看那個地點每天的天氣、原因與準備建議。接下來幾小時的雨看<a href="#/radar">即時雷達</a>。</p>' if texts else "")
    )


MAX_PRECOMPUTED = 7  # 預設地點不超過這麼多時,先把每一種組合的結論算好(2^n − 1 種)

COMPARE_HTML = """<div class="pick" id="pick"><p class="pick-h">比較的地點<small>勾選要放進下面一覽表的地點,也可以加入其他地點一起比較;設定會記在這個瀏覽器裡。</small></p>
<div id="pickchips" role="group" aria-label="比較的地點"></div>
<form id="addcmp" role="search"><input id="addq" type="search" autocomplete="off"
placeholder="加入其他地點一起比較:東港、恆春鎮、東京" aria-label="加入比較的地點"><button>加入</button>
<span id="addmsg"></span><div id="addhits"></div></form></div>"""


def _overview(names: list[str], payloads: dict[str, dict] | None) -> str:
    """總覽頁:可自選比較的地點;最上面是一覽表,下面才是原因、注意事項,以及各地文字說明的連結。"""
    if not payloads or not all(n in payloads and payloads[n].get("briefing") for n in names):
        links = "".join(f'<li><a href="#/place/{i}/summary">{esc(n)}</a></li>' for i, n in enumerate(names))
        return f'<h1>一週天氣預報</h1><ul>{links}</ul>'
    # 只切換預設地點時不必在瀏覽器裡跑分析程式:每一種組合的結論與每個地點的橫幅都先準備好
    ledes = {}
    if len(names) <= MAX_PRECOMPUTED:
        for mask in range(1, 2 ** len(names)):
            subset = [n for i, n in enumerate(names) if mask >> i & 1]
            ledes["|".join(subset)] = _cross_lede(subset, payloads)
    heroes = {
        n: {
            "rows": _hero_rows(payloads[n]["days"]),
            "low": any(r["kind"] == "low" for r in payloads[n]["briefing"]["roles"]),
            "south": (payloads[n]["location"].get("latitude") or 0) < 0,
        }
        for n in names
    }
    data = json.dumps({"names": names, "ledes": ledes, "heroes": heroes}, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")  # 放在 <script> 裡,不能出現 </script>
    return (
        f"<h1>這一週的天氣</h1>{COMPARE_HTML}"
        f'<div id="ovbody">{_overview_body(names, payloads)}</div>'
        f'<script type="application/json" id="ovdata">{data}</script>'
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
    ).replace("__CITIES__", json.dumps([list(c) for c in CITIES], ensure_ascii=False, separators=(",", ":")))
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>一週天氣預報</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{FONTS}">'
        + (f'<link rel="stylesheet" href="{leaflet}.css">' if radar else "")
        + f"<style>{CSS}</style><script>document.documentElement.classList.add('js')</script></head><body>{ICON_SPRITE}"
        f'<header class="top"><div class="in"><a class="brand" href="#/overview">一週天氣預報</a><nav class="nav" aria-label="主要頁面">{nav}</nav>'
        f'<p class="sub" id="fresh" data-generated="{iso}">更新於 {stamp}</p>'
        '<button id="fxtoggle" type="button" hidden aria-pressed="true">背景動畫:開</button></div></header><main>'
        f'<section class="page on" data-page="overview">{_overview(names, payloads)}{missing}</section>'
        f'<section class="page" data-page="place"><div id="places" role="tablist" aria-label="地點">{chips}</div>{SEARCH_HTML}{places}</section>'
        f'{RADAR_HTML if radar else ""}{ABOUT_HTML}'
        "<footer>資料來自 Open-Meteo(ECMWF、AIFS、GEFS 系集)。數值是系集中位數;影響系統、信心度與高空研判是程式依經驗法則做的推論,"
        "不是官方預報。颱風與災害性天氣請以中央氣象署為準。</footer>"
        '</main><div id="tip"></div>'
        + (f'<script src="{leaflet}.js"></script>' if radar else "")
        + f'<script src="{ANIME_URL}"></script>'
        + f"<script>{script}{RADAR_JS if radar else ''}{HERO_JS}{FX_JS}</script>"
        + '<script type="module">' + HERO3D_JS.replace("__THREE_URL__", THREE_URL) + "</script></body></html>"
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
