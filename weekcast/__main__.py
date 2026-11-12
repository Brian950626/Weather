"""命令列介面:python -m weekcast 台北"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analysis, client, history, periods, report, upper
from .systems import SYSTEMS


def build_parser() -> argparse.ArgumentParser:
    models = "、".join(f"{s.api_id}({s.name})" for s in SYSTEMS)
    parser = argparse.ArgumentParser(
        prog="weekcast",
        description="以多模式系集預報未來一週的天氣,並標示每一天的信心度。",
    )
    parser.add_argument("place", nargs="?", default="台北", help="地名(預設:台北)")
    parser.add_argument("--lat", type=float, help="緯度(與 --lon 一起使用,取代地名)")
    parser.add_argument("--lon", type=float, help="經度")
    parser.add_argument("--days", type=int, default=7, help="預報天數,1–16(預設 7)")
    parser.add_argument("--models", help=f"逗號分隔的模式代碼,預設全部:{models}")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 而不是文字報表")
    parser.add_argument("--from-file", metavar="PATH", help="改讀先前存下的原始資料,不連網")
    parser.add_argument("--save-raw", metavar="PATH", help="把原始資料存成 .json 或 .json.gz")
    parser.add_argument("--no-upper", action="store_true", help="不做高空(250–925 hPa)分析")
    parser.add_argument("--no-history", action="store_true", help="不記錄、也不比較先前的預報")
    parser.add_argument("--history-dir", type=Path, help="預報紀錄的存放位置")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.days <= 16:
        print("--days 必須介於 1 到 16", file=sys.stderr)
        return 2
    if (args.lat is None) != (args.lon is None):
        print("--lat 與 --lon 必須一起指定", file=sys.stderr)
        return 2

    try:
        upper_raw = period_raw = None
        if args.from_file:
            bundle = client.load_raw(args.from_file)
            raw, upper_raw = client.split_bundle(bundle)
            period_raw = bundle.get("periods")
            name = args.place
        else:
            if args.lat is not None:
                place = client.Place(f"{args.lat:.2f}, {args.lon:.2f}", args.lat, args.lon)
            else:
                place = client.geocode(args.place)
            name = place.name
            ids = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
            raw = client.fetch_ensemble(place.latitude, place.longitude, args.days, ids)
            if not args.no_upper:
                try:
                    zone = raw.get("timezone") or "GMT"
                    upper_raw = client.fetch_upper(place.latitude, place.longitude, args.days, zone)
                except client.FetchError as err:  # 高空資料抓不到時,仍然給出地面預報
                    print(f"(無法取得高空資料,略過高空分析:{err})", file=sys.stderr)
            try:
                period_raw = client.fetch_periods(place.latitude, place.longitude, args.days, raw.get("timezone") or "GMT")
            except client.FetchError as err:
                print(f"(無法取得時段資料,略過時段預報:{err})", file=sys.stderr)
            if args.save_raw:
                client.save_raw({"surface": raw, "upper": upper_raw, "periods": period_raw}, args.save_raw)
        ens = analysis.parse(raw)
    except (client.FetchError, ValueError, OSError) as err:
        print(f"無法取得預報:{err}", file=sys.stderr)
        return 1

    forecast = analysis.daily_forecast(ens, args.days)
    if not forecast:
        print("資料中沒有可用的預報日", file=sys.stderr)
        return 1
    signal_list = analysis.signals(ens, args.days)
    upper_days = None
    if upper_raw and not args.no_upper:
        try:
            upper_days = upper.analyse(upper_raw, ens.meta.get("latitude") or 0.0, args.days)
        except (KeyError, IndexError, TypeError, ValueError) as err:
            print(f"(高空資料格式不符,略過高空分析:{err})", file=sys.stderr)

    period_list = None
    if period_raw:
        try:
            period_list = periods.period_forecast(period_raw, args.days)
        except (KeyError, IndexError, TypeError, ValueError) as err:
            print(f"(時段資料格式不符,略過時段預報:{err})", file=sys.stderr)

    comparison = None
    if not args.no_history and not args.from_file:
        directory = args.history_dir or history.default_dir()
        lat, lon = ens.meta.get("latitude") or 0.0, ens.meta.get("longitude") or 0.0
        try:
            comparison = history.compare(directory, lat, lon, forecast)
            history.record(directory, lat, lon, forecast)
        except OSError as err:  # 紀錄寫不進去不該讓預報失敗
            print(f"(無法讀寫預報紀錄:{err})", file=sys.stderr)

    if args.json:
        print(json.dumps(report.to_json(name, ens, forecast, signal_list, comparison, upper_days, period_list), ensure_ascii=False, indent=2))
    else:
        print(report.render(name, ens, forecast, signal_list, comparison, upper_days, period_list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
