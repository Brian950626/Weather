import contextlib
import io
import json
import math
import statistics
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weekcast import analysis, briefing, client, history, report, upper, verify
from weekcast.__main__ import main

FIXTURE = str(Path(__file__).parent / "data" / "taipei_20261112.json.gz")


def load_surface():
    return analysis.parse(client.split_bundle(client.load_raw(FIXTURE))[0])


def load_upper():
    return client.split_bundle(client.load_raw(FIXTURE))[1]


def make_raw(days, systems, hourly=None, latitude=25.0):
    """systems: {後綴: {變數: [每個成員的逐日數列]}}"""
    raw = {"latitude": latitude, "longitude": 121.5, "timezone": "Asia/Taipei", "elevation": 5.0, "daily": {"time": days}}
    for suffix, variables in systems.items():
        for var, members in variables.items():
            for i, series in enumerate(members):
                key = var if i == 0 else f"{var}_member{i:02d}"
                raw["daily"][f"{key}_{suffix}" if suffix else key] = series
    if hourly:
        raw["hourly"] = hourly
    return raw


def simple_system(tmax, rain, tmin=None):
    return {
        "temperature_2m_max": tmax,
        "temperature_2m_min": tmin or [[None if v is None else v - 6 for v in m] for m in tmax],
        "precipitation_sum": rain,
    }


class QuantileTest(unittest.TestCase):
    def test_equal_weights_match_plain_median(self):
        pairs = [(v, 1.0) for v in (1, 2, 3, 4, 5)]
        self.assertEqual(analysis.wquantile(pairs, 0.5), 3)
        self.assertEqual(analysis.wquantile(pairs, 0.0), 1)
        self.assertEqual(analysis.wquantile(pairs, 1.0), 5)

    def test_weights_pull_the_median(self):
        pairs = [(10.0, 1.0), (20.0, 9.0)]
        self.assertGreater(analysis.wquantile(pairs, 0.5), 18)

    def test_empty_is_nan(self):
        self.assertTrue(math.isnan(analysis.wquantile([], 0.5)))


class ParseTest(unittest.TestCase):
    def test_multi_model_keys_are_split_by_system(self):
        raw = make_raw(
            ["2026-01-01"],
            {
                "ncep_gefs025": simple_system([[20], [22]], [[0], [5]]),
                "ecmwf_ifs025_ensemble": simple_system([[24], [25], [26]], [[0], [0], [0]]),
            },
        )
        ens = analysis.parse(raw)
        self.assertEqual(sorted(ens.systems), ["ECMWF", "GEFS"])
        self.assertEqual(ens.member_count("GEFS"), 2)
        self.assertEqual(ens.member_count("ECMWF"), 3)

    def test_single_model_response_has_no_suffix(self):
        ens = analysis.parse(make_raw(["2026-01-01"], {"": simple_system([[20], [22]], [[0], [5]])}))
        self.assertEqual(len(ens.systems), 1)
        self.assertEqual(ens.member_count(ens.systems[0]), 2)

    def test_variables_a_model_does_not_provide_are_dropped(self):
        system = simple_system([[20], [22]], [[0], [5]])
        system["wind_gusts_10m_max"] = [[None], [None]]
        ens = analysis.parse(make_raw(["2026-01-01"], {"ecmwf_aifs025_ensemble": system}))
        self.assertNotIn("wind_gusts_10m_max", ens.daily["AIFS"])
        self.assertIsNone(analysis.daily_forecast(ens)[0].gust)

    def test_api_error_is_reported(self):
        with self.assertRaises(ValueError):
            analysis.parse({"error": True, "reason": "limit"})


class DailyForecastTest(unittest.TestCase):
    def test_systems_get_equal_weight_regardless_of_member_count(self):
        # 2 個成員的系統說 20°C,10 個成員的系統說 30°C:中位數應落在兩者之間
        raw = make_raw(
            ["2026-01-01"],
            {
                "ncep_gefs025": simple_system([[20], [20]], [[0], [0]]),
                "ecmwf_ifs025_ensemble": simple_system([[30]] * 10, [[5]] * 10),
            },
        )
        fc = analysis.daily_forecast(analysis.parse(raw))[0]
        self.assertAlmostEqual(fc.pop, 0.5)
        self.assertTrue(20 < fc.tmax.p50 < 30)
        self.assertAlmostEqual(fc.tmax_gap, 10)
        self.assertAlmostEqual(fc.pop_gap, 1.0)

    def test_constant_model_bias_is_removed_but_real_disagreement_is_kept(self):
        days = [f"2026-01-0{d}" for d in range(1, 7)]
        dry = [[0] * 6] * 2
        # GEFS 每天都比 ECMWF 高 4°C(格點偏差),只有第 5 天多差了 5°C(真正的分歧)
        ecmwf = [[20, 20, 20, 20, 20, 20]] * 2
        gefs = [[24, 24, 24, 24, 29, 24]] * 2
        raw = make_raw(days, {"ecmwf_ifs025_ensemble": simple_system(ecmwf, dry), "ncep_gefs025": simple_system(gefs, dry)})
        forecast = analysis.daily_forecast(analysis.parse(raw))
        self.assertAlmostEqual(forecast[0].tmax_gap, 0)
        self.assertAlmostEqual(forecast[0].tmax.p50, 22)
        self.assertAlmostEqual(forecast[4].tmax_gap, 5)
        self.assertGreater(forecast[0].confidence, forecast[4].confidence)

    def test_days_beyond_every_model_are_skipped(self):
        raw = make_raw(["2026-01-01", "2026-01-02"], {"ncep_gefs025": simple_system([[20, None], [21, None]], [[0, None], [0, None]])})
        self.assertEqual([f.day for f in analysis.daily_forecast(analysis.parse(raw))], ["2026-01-01"])

    def test_agreement_scores_higher_than_disagreement(self):
        calm = analysis.score_confidence(1, t_spread=1.5, t_gap=0.5, pop=0.05, pop_gap=0.05)
        messy = analysis.score_confidence(1, t_spread=6.0, t_gap=3.0, pop=0.5, pop_gap=0.8)
        self.assertGreater(calm, 85)
        self.assertLess(messy, 30)
        self.assertEqual(analysis.confidence_label(calm), "高")
        self.assertEqual(analysis.confidence_label(messy), "低")

    def test_confidence_never_negative(self):
        self.assertEqual(analysis.score_confidence(15, 20, 20, 0.5, 1.0), 0)

    def test_two_clusters_are_detected(self):
        two = [(20 + 0.1 * i, 1.0) for i in range(10)] + [(27 + 0.1 * i, 1.0) for i in range(20)]
        scenario = analysis.find_scenario(two)
        self.assertIsNotNone(scenario)
        self.assertAlmostEqual(scenario.low_share, 1 / 3, places=2)
        self.assertAlmostEqual(scenario.high, 27.95, places=1)

    def test_wide_single_cluster_is_not_split(self):
        uniform = [(20 + 0.2 * i, 1.0) for i in range(30)]
        self.assertIsNone(analysis.find_scenario(uniform))
        normal = statistics.NormalDist(24, 2.5)
        bell = [(normal.inv_cdf((i + 0.5) / 60), 1.0) for i in range(60)]
        self.assertIsNone(analysis.find_scenario(bell))

    def test_sky_wording(self):
        dry = analysis.Spread(0, 0, 0)
        self.assertEqual(analysis.describe_sky(0.05, dry, analysis.Spread(5, 10, 20)), "晴")
        self.assertEqual(analysis.describe_sky(0.05, dry, analysis.Spread(80, 90, 100)), "陰")
        self.assertEqual(analysis.describe_sky(0.9, analysis.Spread(5, 20, 60), None), "明顯降雨")


class SignalTest(unittest.TestCase):
    def raw(self, latitude, pressures):
        hours = [f"2026-01-0{d + 1}T{h:02d}:00" for d in range(len(pressures)) for h in (0, 6, 12, 18)]
        series = [p for p in pressures for _ in range(4)]
        hourly = {"time": hours, "pressure_msl_ncep_gefs025": series, "pressure_msl_member01_ncep_gefs025": series}
        days = [f"2026-01-0{d + 1}" for d in range(len(pressures))]
        flat = [[20] * len(days)] * 2
        return make_raw(days, {"ncep_gefs025": simple_system(flat, [[0] * len(days)] * 2)}, hourly, latitude)

    def test_pressure_fall_flagged_in_subtropics_but_not_midlatitudes(self):
        kinds = lambda lat: [s.kind for s in analysis.signals(analysis.parse(self.raw(lat, [1016, 1012, 1012])))]
        self.assertEqual(kinds(25.0), ["pressure_fall"])
        self.assertEqual(kinds(50.0), [])

    def test_deep_low_mentions_tropical_system(self):
        found = analysis.signals(analysis.parse(self.raw(20.0, [1008, 1007, 990])))
        deep = [s for s in found if s.kind == "deep_low"]
        self.assertEqual(len(deep), 1)
        self.assertIn("颱風", deep[0].text)


class RealDataTest(unittest.TestCase):
    """2026-11-12 台北的真實系集資料(逐時資料已抽稀為每 3 小時)。"""

    @classmethod
    def setUpClass(cls):
        cls.ens = load_surface()
        cls.forecast = analysis.daily_forecast(cls.ens, 7)

    def test_three_systems_present_and_removed_models_ignored(self):
        # 樣本檔裡還有 GEPS 與 ICON 的資料,已不採用,解析時應略過
        counts = {s: self.ens.member_count(s) for s in self.ens.systems}
        self.assertEqual(counts, {"ECMWF": 51, "GEFS": 31, "AIFS": 51})

    def test_values_are_ordered_and_plausible(self):
        self.assertEqual(len(self.forecast), 7)
        for fc in self.forecast:
            self.assertLessEqual(fc.tmax.p10, fc.tmax.p50)
            self.assertLessEqual(fc.tmax.p50, fc.tmax.p90)
            self.assertLess(fc.tmin.p50, fc.tmax.p50)
            self.assertTrue(0 <= fc.pop <= 1)
            self.assertTrue(fc.pop_heavy <= fc.pop_moderate <= fc.pop)
            self.assertTrue(0 <= fc.confidence <= 100)

    def test_cold_surge_shows_up_in_both_surface_and_pattern(self):
        by_day = {fc.day: fc for fc in self.forecast}
        self.assertLess(by_day["2026-11-18"].tmax.p50, by_day["2026-11-17"].tmax.p50 - 3)
        kinds = {s.kind for s in analysis.signals(self.ens, 7) if s.day == "2026-11-18"}
        self.assertEqual(kinds, {"pressure_rise", "cold"})

    def test_report_renders(self):
        text = report.render("台北", self.ens, self.forecast, analysis.signals(self.ens, 7))
        self.assertIn("133 個系集成員", text)
        self.assertIn("11/18(三)", text)

    def test_padding_counts_cjk_as_double_width(self):
        self.assertEqual(report._width("11/12(四)"), 9)
        self.assertEqual(report._width(report._pad("台北", 7)), 7)
        self.assertEqual(report._pad("5", 3, right=True), "  5")


class BriefingTest(unittest.TestCase):
    """把預報整理成報告的骨架:時段、主要影響系統、模式比較、情境、注意事項。"""

    @classmethod
    def setUpClass(cls):
        ens = load_surface()
        cls.forecast = analysis.daily_forecast(ens, 7)
        cls.upper = upper.analyse(load_upper(), 25.0, 7)
        cls.brief = briefing.build(cls.forecast, analysis.signals(ens, 7), analysis.patterns(ens), cls.upper, 25.0, 121.5)

    def test_each_day_gets_a_controlling_system(self):
        self.assertEqual(
            [(r.kind, r.system) for r in self.brief.roles],
            [("ridge", "副熱帶高壓籠罩"), ("ridge", "副熱帶高壓籠罩"), ("trough", "高空槽接近"), ("front", "鋒面通過"),
             ("cold", "東北季風影響"), ("warm", "偏南風回暖"), ("front", "鋒面通過、東北季風增強")],
        )

    def test_week_is_split_into_at_most_three_stages(self):
        stages = self.brief.stages
        self.assertEqual([len(s.days) for s in stages], [2, 3, 2])
        self.assertEqual([s.stable for s in stages], [True, False, False])
        self.assertEqual(stages[1].headline, "高空槽接近 → 鋒面通過 → 東北季風影響")
        self.assertEqual(stages[2].headline, "偏南風回暖 → 鋒面通過、東北季風增強")  # 太短的兩段併在一起
        self.assertEqual(stages[0].confidence_label, "高")
        self.assertIn("11/14(六)", stages[1].reasons[0])  # 理由取該時段最沒把握的一天
        self.assertEqual([level for level, _ in stages[1].rationale], ["500 hPa", "850 / 925 hPa", "250 hPa", "水氣與不穩定"])
        self.assertIn("低層鋒面通過", stages[1].rationale[1][1])
        self.assertIn("天氣穩定", stages[0].coupling)
        self.assertIn("配合", stages[2].coupling)

    def test_lede_says_when_it_changes_and_what_to_watch(self):
        self.assertIn("分成 3 段", self.brief.lede)
        self.assertIn("11/14(六)、11/17(二) 轉變", self.brief.lede)
        self.assertIn("11/18(三) 的降溫", self.brief.lede)
        self.assertIn("最沒把握的是 11/14(六)", self.brief.lede)

    def test_southern_hemisphere_and_other_regions_do_not_get_a_monsoon(self):
        ens = load_surface()
        roles = briefing.day_roles(self.forecast, analysis.signals(ens, 7), self.upper, 25.0, -80.0)
        self.assertEqual(roles[4].system, "鋒後冷空氣影響")
        self.assertEqual(roles[6].system, "鋒面通過、冷空氣南下")

    def test_without_upper_air_data_the_surface_signals_still_give_stages(self):
        ens = load_surface()
        brief = briefing.build(self.forecast, analysis.signals(ens, 7), analysis.patterns(ens), None, 25.0, 121.5)
        self.assertTrue(1 <= len(brief.stages) <= 3)
        self.assertEqual([level for level, _, _ in brief.current], ["地面"])
        self.assertEqual(brief.stages[0].rationale, [])

    def test_current_analysis_goes_from_the_surface_up(self):
        levels = [level for level, _, _ in self.brief.current]
        self.assertEqual(levels, ["地面", "925 hPa", "850 hPa", "700 hPa", "500 hPa", "250 hPa", "整層"])
        meaning = dict((level, text) for level, _, text in self.brief.current)
        self.assertIn("588 線", meaning["500 hPa"])
        self.assertIn("暖平流", meaning["850 hPa"])

    def test_model_comparison_names_the_odd_one_out(self):
        rows = {m.day: m for m in self.brief.models}
        self.assertIn("GEFS 偏乾", rows["2026-11-14"].verdict)
        self.assertIn("ECMWF 偏乾", rows["2026-11-16"].verdict)
        self.assertTrue(rows["2026-11-16"].split)
        self.assertEqual(rows["2026-11-18"].verdict, "各模式一致。")
        tmax, pop, rain = rows["2026-11-15"].systems["ECMWF"]
        self.assertGreater(pop, 0.9)
        self.assertGreater(rain, 5)

    def test_scenarios_cover_the_least_certain_days(self):
        days = {s.day for s in self.brief.scenarios}
        self.assertIn("2026-11-14", days)
        self.assertLessEqual(len(days), 3)
        for day in days:
            pair = [s for s in self.brief.scenarios if s.day == day]
            self.assertEqual([s.name for s in pair], ["A", "B"])
            self.assertAlmostEqual(pair[0].share + pair[1].share, 1, places=6)
            self.assertGreaterEqual(pair[0].share, pair[1].share)  # A 是主要情境
        self.assertEqual([briefing.likelihood(x) for x in (0.9, 0.7, 0.5, 0.3, 0.1)], ["高", "中高", "中", "中低", "低"])

    def test_hazards_and_watch_points(self):
        names = [name for name, _, _ in self.brief.hazards]
        self.assertIn("降溫", names)
        when = dict((name, when) for name, when, _ in self.brief.hazards)["降溫"]
        self.assertEqual(when, "11/18(三)")
        text = "\n".join(self.brief.watch)
        self.assertIn("槽線", text)
        self.assertIn("低層鋒面", text)
        self.assertIn("各模式的降雨機率差很多", text)

    def test_confidence_has_five_levels_with_reasons(self):
        self.assertEqual([analysis.confidence_label(s) for s in (90, 70, 55, 40, 10)], ["高", "中高", "中", "中低", "低"])
        shaky = self.forecast[2]  # 11/14
        reasons = "、".join(analysis.confidence_reasons(shaky))
        self.assertIn("各模式降雨機率相差", reasons)
        self.assertIn("下不下雨不明確", reasons)
        self.assertEqual(analysis.confidence_reasons(self.forecast[1]), ["成員集中、各模式一致"])

    def test_quartiles_sit_inside_the_ten_ninety_range(self):
        for fc in self.forecast:
            self.assertTrue(fc.tmax.p10 <= fc.tmax.p25 <= fc.tmax.p50 <= fc.tmax.p75 <= fc.tmax.p90)

    def test_outline_takes_the_majority_view_across_places(self):
        role = lambda day, kind, system: {"day": day, "kind": kind, "system": system}
        north = [role("2026-01-01", "ridge", "高壓脊籠罩"), role("2026-01-02", "front", "鋒面通過"), role("2026-01-03", "cold", "東北季風影響")]
        south = [role("2026-01-01", "ridge", "高壓脊籠罩"), role("2026-01-02", "quiet", "天氣穩定"), role("2026-01-03", "cold", "東北季風影響")]
        stages = briefing.outline([north, north, south])
        self.assertEqual([s["days"] for s in stages], [["2026-01-01"], ["2026-01-02", "2026-01-03"]])
        self.assertEqual(stages[1]["headline"], "鋒面通過 → 東北季風影響")
        self.assertEqual(briefing.outline([]), [])


class VerifyTest(unittest.TestCase):
    def runs(self):
        day = lambda tmax, tmin, pop: {"tmax": tmax, "tmin": tmin, "pop": pop}
        return [
            # 1/1 做的預報:1/2 是 1 天前,1/4 是 3 天前
            {"at": "2026-01-01T01:00:00+00:00", "days": {"2026-01-01": day(20, 12, 0.1), "2026-01-02": day(21, 13, 0.7), "2026-01-04": day(18, 10, 0.2)}},
            # 1/3 做了兩次,同一個時效只取較晚的一次
            {"at": "2026-01-03T01:00:00+00:00", "days": {"2026-01-03": day(19, 11, 0.5), "2026-01-04": day(17, 9, 0.9)}},
            {"at": "2026-01-03T07:00:00+00:00", "days": {"2026-01-03": day(19, 11, 0.5), "2026-01-04": day(16, 9, 0.3)}},
        ]

    def test_errors_are_forecast_minus_observed_and_rain_is_a_contingency(self):
        observed = {"2026-01-02": {"tmax": 20.0, "tmin": 14.0, "rain": 0.0}, "2026-01-04": {"tmax": 15.0, "tmin": 9.0, "rain": 6.0}}
        result = verify.verify(self.runs(), observed)
        day2 = result.at("2026-01-02", 1)
        self.assertAlmostEqual(day2.tmax_error, 1.0)  # 預報 21、實際 20:偏暖 1 度
        self.assertAlmostEqual(day2.tmin_error, -1.0)
        self.assertEqual(day2.rain, "空報")  # 報 70% 卻沒下
        self.assertEqual(result.at("2026-01-04", 3).rain, "漏報")  # 3 天前只報 20%,結果下了 6 mm
        latest = result.at("2026-01-04", 1)
        self.assertEqual((latest.tmax, latest.rain), (16, "漏報"))  # 取 07 時那一次(30%)
        self.assertIsNone(result.at("2026-01-03", 0))  # 當天的預報不算
        one_day = next(s for s in result.scores if s.lead == 1)
        self.assertEqual((one_day.count, one_day.hits, one_day.misses, one_day.false_alarms), (2, 0, 1, 1))
        self.assertAlmostEqual(one_day.tmax_bias, 1.0)
        self.assertAlmostEqual(one_day.tmax_mae, 1.0)

    def test_observed_keeps_only_complete_past_days(self):
        raw = {"daily": {"time": ["2026-01-01", "2026-01-02", "2026-01-03"], "temperature_2m_max": [20, None, 22],
                         "temperature_2m_min": [10, 11, 12], "precipitation_sum": [0, 1, 2]}}
        self.assertEqual(list(verify.parse_observed(raw, "2026-01-03")), ["2026-01-01"])
        self.assertIsNone(verify.verify(self.runs(), {}))
        self.assertIsNone(verify.verify([], {"2026-01-02": {"tmax": 20.0, "tmin": 14.0, "rain": 0.0}}))

    def test_page_shows_verification_when_history_and_observations_exist(self):
        from weekcast import site

        bundle = client.load_raw(FIXTURE)
        now = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
        value = {"tmax": 27.0, "tmin": 20.0, "pop": 0.6}
        runs = [{"at": "2026-11-10T01:00:00+00:00", "days": {"2026-11-10": value, "2026-11-11": value, "2026-11-12": value}}]
        observed = {"daily": {"time": ["2026-11-10", "2026-11-11", "2026-11-12"], "temperature_2m_max": [26.0, 28.5, 27.0],
                              "temperature_2m_min": [20.0, 21.0, 19.0], "precipitation_sum": [0.0, 3.0, 0.0]}}
        block, data = site.build_place("台北", bundle["surface"], bundle["upper"], 7, bundle["periods"], runs, now, observed)
        self.assertIn("過去幾天報得準不準", block)
        self.assertIn('<span class="verdict-ok">有對</span>', block)
        self.assertIn("高溫 -1.5", block)  # 預報 27、實際 28.5
        self.assertEqual(data["verification"]["checks"][0]["day"], "2026-11-11")
        self.assertEqual(len(data["runs"]), 2)

    def test_observed_request_asks_for_past_days_in_local_time(self):
        from unittest import mock

        with mock.patch.object(client, "_get_json", return_value={}) as get:
            client.fetch_observed(25.0, 121.5, "Asia/Taipei")
        url, params = get.call_args.args[:2]
        self.assertEqual(url, client.FORECAST_URL)
        self.assertEqual((params["timezone"], params["past_days"]), ("Asia/Taipei", 8))


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.forecast = analysis.daily_forecast(load_surface(), 7)
        self.now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)

    def test_compares_against_an_older_run_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertIsNone(history.compare(d, 25, 121.5, self.forecast, self.now))
            history.record(d, 25, 121.5, self.forecast, self.now - timedelta(hours=12))
            history.record(d, 25, 121.5, self.forecast, self.now - timedelta(hours=1))  # 太近,同一次起報
            warmer = analysis.daily_forecast(load_surface(), 7)
            warmer[0].tmax.p50 += 2
            result = history.compare(d, 25, 121.5, warmer, self.now)
            self.assertAlmostEqual(result.age_hours, 12)
            self.assertAlmostEqual(result.changes[0].tmax, 2, places=1)
            self.assertAlmostEqual(result.changes[1].tmax, 0, places=1)
            text = report.render("台北", load_surface(), warmer, [], result)
            self.assertIn("高溫上修 2°C", text)

    def test_other_locations_are_not_mixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            history.record(Path(tmp), 25, 121.5, self.forecast, self.now - timedelta(hours=12))
            self.assertIsNone(history.compare(Path(tmp), 35, 139.7, self.forecast, self.now))

    def runs(self, day, pops, tmax=25.0):
        """先前各次起報(每 6 小時一次,由舊到新)對某一天的預報。"""
        ages = range(6 * len(pops), 0, -6)
        return [
            {"at": (self.now - timedelta(hours=age)).isoformat(), "days": {day: {"tmax": tmax, "tmin": 20.0, "pop": pop}}}
            for age, pop in zip(ages, pops)
        ]

    def test_repeated_revisions_in_one_direction_are_a_trend(self):
        target = self.forecast[4]  # 11/16,降雨機率 72%
        result = history.compare_runs(self.runs(target.day, [0.30, 0.45, 0.58]), self.forecast, self.now)
        trend = next(t for t in result.trends if t.what == "pop")
        self.assertEqual((trend.day, trend.kind), (target.day, "rising"))
        self.assertEqual(len(trend.values), 4)
        self.assertAlmostEqual(trend.values[-1], target.pop)
        self.assertIn("降雨機率連續 3 次上修(30% → 45% → 58% → 72%)", "\n".join(report.comparison_lines(result)))

    def test_flip_flopping_runs_are_reported_as_swinging(self):
        target = self.forecast[4]
        result = history.compare_runs(self.runs(target.day, [0.75, 0.40]), self.forecast, self.now)
        trend = next(t for t in result.trends if t.what == "pop")
        self.assertEqual(trend.kind, "swinging")
        self.assertIn("來回擺盪", "\n".join(report.comparison_lines(result)))

    def test_small_wobbles_are_not_a_trend(self):
        target = self.forecast[4]
        steady = self.runs(target.day, [target.pop - 0.04, target.pop + 0.03, target.pop - 0.02], target.tmax.p50)
        result = history.compare_runs(steady, self.forecast, self.now)
        self.assertEqual(result.trends, [])
        self.assertEqual(report.comparison_lines(result), ["沒有明顯變化,預報穩定。"])

    def test_runs_from_the_same_model_cycle_count_once(self):
        target = self.forecast[4]
        rows = self.runs(target.day, [0.30, 0.45])
        # 一小時內重跑三次(例如推送程式碼觸發的重建)不能被當成連續三次上修
        rows += [{"at": (self.now - timedelta(hours=h)).isoformat(), "days": {target.day: {"tmax": 25.0, "tmin": 20.0, "pop": pop}}} for h, pop in ((0.9, 0.5), (0.6, 0.55), (0.3, 0.6))]
        result = history.compare_runs(rows, self.forecast, self.now)
        self.assertAlmostEqual(result.age_hours, 6)
        trend = next(t for t in result.trends if t.what == "pop")
        self.assertEqual([round(v, 2) for v in trend.values[:-1]], [0.30, 0.45])


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_text_report_from_file(self):
        code, out, _ = self.run_cli("台北", "--from-file", FIXTURE)
        self.assertEqual(code, 0)
        self.assertIn("台北 未來 7 天天氣預報", out)

    def test_json_output(self):
        code, out, _ = self.run_cli("--from-file", FIXTURE, "--json", "--days", "3")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["days"]), 3)
        self.assertEqual(data["systems"]["ECMWF"], 51)

    def test_bad_arguments(self):
        self.assertEqual(self.run_cli("--days", "0")[0], 2)
        self.assertEqual(self.run_cli("--lat", "25")[0], 2)

    def test_missing_file_is_a_clean_error(self):
        code, _, err = self.run_cli("--from-file", "/nonexistent.json")
        self.assertEqual(code, 1)
        self.assertIn("無法取得預報", err)

    def test_upper_air_section_is_optional(self):
        self.assertIn("高空分析", self.run_cli("--from-file", FIXTURE)[1])
        self.assertNotIn("高空分析", self.run_cli("--from-file", FIXTURE, "--no-upper")[1])

    def test_surface_only_file_from_older_version_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.json")
            client.save_raw(client.split_bundle(client.load_raw(FIXTURE))[0], path)
            code, out, _ = self.run_cli("--from-file", path)
        self.assertEqual(code, 0)
        self.assertNotIn("高空分析", out)

    def test_json_includes_upper_air(self):
        data = json.loads(self.run_cli("--from-file", FIXTURE, "--json")[1])
        self.assertEqual(len(data["upper"]["days"]), 7)
        self.assertIn("regime", data["upper"]["days"][0])

    def test_known_places_need_no_network(self):
        place = client.geocode("臺北市")
        self.assertAlmostEqual(place.latitude, 25.04)
        self.assertEqual(client.geocode("高雄").longitude, 120.30)
        self.assertEqual(client.geocode("枋寮").latitude, 22.37)

    def test_place_with_explicit_coordinates(self):
        place = client.geocode("恆春@22.00,120.75")
        self.assertEqual((place.name, place.latitude, place.longitude), ("恆春", 22.0, 120.75))
        for bad in ("恆春@abc", "恆春@22", "恆春@99,120"):
            with self.assertRaises(client.FetchError):
                client.geocode(bad)


def make_upper(days=2, z500=None, u250=None, v250=None, t850=None, site=None, latitude=40.0):
    """合成的高空資料。z500/u250/v250/t850 是 (軸, 偏移) → 數值的函式;site 的值可以是逐時刻的串列。"""
    times = [f"2026-01-0{d + 1}T{h:02d}:00" for d in range(days) for h in (0, 12)]
    n = len(times)
    const = lambda value: list(value) if isinstance(value, list) else [value] * n
    profile_values = {
        "wind_speed_250hPa": 40.0, "wind_direction_250hPa": 270.0,
        "geopotential_height_500hPa": 5600.0, "temperature_500hPa": -20.0,
        "wind_speed_500hPa": 20.0, "wind_direction_500hPa": 270.0,
        "temperature_700hPa": 0.0, "relative_humidity_700hPa": 60.0,
        "temperature_850hPa": 8.0, "relative_humidity_850hPa": 65.0,
        "wind_speed_850hPa": 10.0, "wind_direction_850hPa": 270.0,
        "temperature_925hPa": 12.0, "wind_speed_925hPa": 5.0, "wind_direction_925hPa": 270.0,
    } | (site or {})
    hourly = {"time": times}
    for var, value in profile_values.items():
        for key in (var, f"{var}_member01"):
            hourly[f"{key}_ecmwf_ifs025_ensemble"] = const(value)
    points, data = client.cross_points(latitude, 0.0), []
    for point in points:
        where = (point["axis"], point["offset"])
        u = (u250 or (lambda *_: 40.0))(*where)
        v = (v250 or (lambda *_: 0.0))(*where)
        fields = {
            "geopotential_height_500hPa": (z500 or (lambda *_: 5600.0))(*where),
            "wind_speed_250hPa": (u * u + v * v) ** 0.5,
            "wind_direction_250hPa": upper.wind_direction(u, v),
            "temperature_850hPa": (t850 or (lambda *_: 8.0))(*where),
        }
        one = {"time": times}
        for var, value in fields.items():
            one[var] = const(value)
            one[f"{var}_member01"] = const(value)
        data.append({"hourly": one})
    return {"profile": {"hourly": hourly}, "cross": {"points": points, "data": data}}


class UpperToolsTest(unittest.TestCase):
    def test_wind_components_follow_meteorological_convention(self):
        u, v = upper.wind_components(10, 270)  # 西風:由西往東吹
        self.assertAlmostEqual(u, 10)
        self.assertAlmostEqual(v, 0)
        u, v = upper.wind_components(10, 0)  # 北風:由北往南吹
        self.assertAlmostEqual(v, -10)
        for direction in (0, 45, 135, 225, 359):
            self.assertAlmostEqual(upper.wind_direction(*upper.wind_components(7, direction)), direction)
        self.assertEqual(upper.compass(225), "西南")
        self.assertEqual(upper.compass(350), "北")

    def test_dew_point_and_k_index(self):
        self.assertAlmostEqual(upper.dew_point(20, 100), 20, places=3)
        self.assertLess(upper.dew_point(20, 50), 10)
        humid = upper.k_index(t850=20, rh850=90, t700=8, rh700=85, t500=-8)
        dry = upper.k_index(t850=20, rh850=40, t700=8, rh700=20, t500=-8)
        self.assertGreater(humid, 30)
        self.assertLess(dry, 15)

    def test_axes_are_located_between_grid_points(self):
        offsets = list(range(-40, 41, 5))
        wave = [5600 - 60 * math.cos(math.radians((o + 12) * 6)) for o in offsets]  # 槽在 -12°,脊在 +18°
        troughs, ridges = upper.find_axes(offsets, wave, 20)
        self.assertEqual(len(troughs), 1)
        self.assertAlmostEqual(troughs[0], -12, delta=1)
        self.assertAlmostEqual(ridges[0], 18, delta=1)

    def test_small_wiggles_are_not_axes(self):
        offsets = list(range(-40, 41, 5))
        bumpy = [5600 + o + (3 if i % 2 else 0) for i, o in enumerate(offsets)]
        self.assertEqual(upper.find_axes(offsets, bumpy, 20), ([], []))

    def test_flow_regime_from_trough_position(self):
        offsets = list(range(-40, 41, 5))
        wave = lambda axis: [5600 - 60 * math.cos(math.radians((o - axis) * 6)) for o in offsets]
        self.assertEqual(upper.classify_flow(offsets, wave(-12), 40)[0], "槽前")
        self.assertEqual(upper.classify_flow(offsets, wave(12), 40)[0], "槽後")
        self.assertEqual(upper.classify_flow(offsets, wave(0), 40)[0], "槽線過境")
        self.assertEqual(upper.classify_flow(offsets, wave(30), 40)[0], "脊線上")
        self.assertEqual(upper.classify_flow(offsets, [5600] * len(offsets), 40)[0], "平直")
        self.assertEqual(upper.classify_flow(offsets, wave(-12), 10, easterly=True)[0], "東風帶")


class UpperAnalysisTest(unittest.TestCase):
    def test_divergence_sign_and_size(self):
        # 西風往東每 10° 增加 10 m/s:空氣流出比流入多 → 輻散
        day = upper.analyse(make_upper(u250=lambda axis, o: 40 + o if axis == "we" else 40), 0.0)[0]
        self.assertAlmostEqual(day.div250, 10 / (6_371_000 * math.radians(10)) * 1e5, places=2)
        day = upper.analyse(make_upper(u250=lambda axis, o: 40 - o if axis == "we" else 40), 0.0)[0]
        self.assertLess(day.div250, -0.3)
        self.assertIn("250 hPa 高層輻合", day.sink)

    def test_cold_advection_when_wind_blows_from_the_cold_side(self):
        colder_north = lambda axis, o: 8 - 0.8 * o if axis == "ns" else 8
        northerly = {"wind_speed_850hPa": 10.0, "wind_direction_850hPa": 0.0}
        southerly = {"wind_speed_850hPa": 10.0, "wind_direction_850hPa": 180.0}
        cold = upper.analyse(make_upper(t850=colder_north, site=northerly), 40.0)[0]
        warm = upper.analyse(make_upper(t850=colder_north, site=southerly), 40.0)[0]
        self.assertAlmostEqual(cold.tadv850, -10 * 8 / (6_371_000 * math.radians(10)) * 86400, places=2)
        self.assertAlmostEqual(warm.tadv850, -cold.tadv850, places=5)
        self.assertIn("850 hPa 冷平流", cold.sink)
        self.assertIn("850 hPa 暖平流", warm.lift)

    def test_jet_axis_is_found_on_the_north_south_section(self):
        core = lambda axis, o: 70 - 3 * abs(o - 10) if axis == "ns" else 40
        day = upper.analyse(make_upper(u250=core), 30.0)[0]
        self.assertEqual(day.jet_offset, 10)
        self.assertAlmostEqual(day.jet_core, 70)
        self.assertTrue(day.has_jet)
        calm = upper.analyse(make_upper(u250=lambda *_: 15, site={"wind_speed_250hPa": 15.0}), 30.0)[0]
        self.assertFalse(calm.has_jet)

    def test_trough_ahead_with_deep_moisture_means_rain(self):
        trough_west = lambda axis, o: 5600 - 60 * math.cos(math.radians((o + 10) * 6)) if axis == "we" else 5600
        moist = {"relative_humidity_700hPa": 85.0, "relative_humidity_850hPa": 90.0}
        diverging = lambda axis, o: 40 + o if axis == "we" else 40
        day = upper.analyse(make_upper(z500=trough_west, u250=diverging, site=moist), 40.0)[0]
        self.assertEqual(day.regime, "槽前")
        self.assertAlmostEqual(day.trough, -10, delta=1)
        self.assertEqual(day.outlook, "wet")

    def test_moisture_only_at_low_levels_is_shallow_rain(self):
        capped = {"relative_humidity_700hPa": 30.0, "relative_humidity_850hPa": 90.0}
        day = upper.analyse(make_upper(site=capped), 40.0)[0]
        self.assertEqual((day.moisture, day.outlook), ("淺層", "shallow"))

    def test_subtropical_high_only_applies_in_the_subtropics(self):
        high = {"geopotential_height_500hPa": 5900.0}
        flagged = lambda lat: upper.analyse(make_upper(site=high, latitude=lat), lat)[0].subtropical_high
        self.assertTrue(flagged(25.0))
        self.assertTrue(flagged(-30.0))
        self.assertFalse(flagged(50.0))
        self.assertFalse(flagged(2.0))  # 赤道附近高度本來就這麼高

    def test_moist_unstable_tropics_get_showers_without_lift(self):
        tropical = {"relative_humidity_700hPa": 80.0, "relative_humidity_850hPa": 85.0, "temperature_850hPa": 19.0,
                    "temperature_700hPa": 9.0, "temperature_500hPa": -5.0, "geopotential_height_500hPa": 5895.0}
        day = upper.analyse(make_upper(site=tropical, latitude=2.0), 2.0)[0]
        self.assertGreaterEqual(day.k, 30)
        self.assertEqual(day.sink, [])
        self.assertEqual(day.outlook, "showery")

    def test_displayed_trough_matches_the_regime(self):
        # 槽前時要回報西邊(上游)的槽,即使東邊有一條更近的
        offsets = list(range(-40, 41, 5))
        # 主槽在西 20°;東 15° 另有一個較近的短槽,兩者之間是東 10° 的脊
        heights = [5700, 5680, 5650, 5620, 5600, 5615, 5635, 5655, 5675, 5695, 5710, 5690, 5720, 5740, 5755, 5765, 5770]
        regime, trough, ridge = upper.classify_flow(offsets, heights, 40)
        self.assertEqual(regime, "槽前")
        self.assertAlmostEqual(trough, -20, delta=1)
        self.assertAlmostEqual(ridge, 10, delta=1)

    def test_hemisphere_wording(self):
        rows = [upper.UpperDay(day="2026-01-01", regime="槽前", trough=-8.0), upper.UpperDay(day="2026-01-02", regime="槽後", trough=9.0)]
        north = report.upper_impact(rows, [], southern=False)[0]
        south = report.upper_impact(rows, [], southern=True)[0]
        self.assertIn("通過前高空吹偏南風", north)
        self.assertIn("通過前高空吹偏北風", south)

    def test_jet_at_section_edge_is_marked(self):
        edge = lambda axis, o: 40 + 2 * o if axis == "ns" else 40
        day = upper.analyse(make_upper(u250=edge), 30.0)[0]
        self.assertTrue(day.jet_at_edge)
        self.assertEqual(report._jet(day), "北 20°+ 80")

    def test_wind_shift_to_the_cold_side_is_a_front(self):
        times = [f"2026-01-0{d}T{h:02d}:00" for d in (1, 2) for h in (0, 6, 12, 18)]
        winds = [upper.wind_components(s, d) for s, d in ((5, 250), (4, 260), (4, 270), (3, 265), (4, 350), (7, 40), (8, 50), (8, 50))]
        u, v = [w[0] for w in winds], [w[1] for w in winds]
        temperature = [18, 18, 19, 19, 17, 15, 13, 12]
        shifts = upper.find_wind_shifts(times, u, v, temperature, 25.0)
        self.assertEqual(list(shifts), ["2026-01-02"])  # 同一道鋒面只記一次
        shift = shifts["2026-01-02"]
        self.assertEqual((shift.start, shift.end), ("2026-01-01T18:00", "2026-01-02T00:00"))
        self.assertEqual((upper.compass(shift.from_dir), upper.compass(shift.to_dir)), ("西", "北"))
        self.assertAlmostEqual(shift.cooling, 7)
        self.assertIn("之後一天內 925 hPa 降溫 7°C", report.shift_text(shift))
        # 南半球的冷空氣從南邊來;赤道附近沒有鋒面;轉成偏南的暖風不算
        self.assertEqual(upper.find_wind_shifts(times, u, v, temperature, -25.0), {})
        self.assertEqual(upper.find_wind_shifts(times, u, v, temperature, 5.0), {})
        self.assertEqual(list(upper.find_wind_shifts(times, u, [-x for x in v], temperature, -25.0)), ["2026-01-02"])

    def test_wind_speed_survives_a_shift_within_the_day(self):
        # 上半天西風 8、下半天東北風 8:向量平均幾乎抵銷,但風速仍是 8
        turning = {"wind_speed_925hPa": [8.0, 8.0, 8.0, 8.0], "wind_direction_925hPa": [270.0, 45.0, 45.0, 45.0],
                   "relative_humidity_850hPa": 90.0, "relative_humidity_700hPa": 80.0}
        first, second = upper.analyse(make_upper(site=turning, latitude=25.0), 25.0)
        self.assertAlmostEqual(first.wind925, 8)
        self.assertIsNotNone(first.shift)
        self.assertIn("925 hPa 低層鋒面通過", first.lift)
        self.assertIsNone(second.shift)
        self.assertIn("西→東北 8", report.upper_rows([first])[1])

    def test_advection_is_computed_before_averaging(self):
        # 北冷南暖;上半天南風(暖平流)、下半天北風(冷平流)而且更強。
        # 風先平均再乘梯度會算成弱冷平流;逐時刻計算後平均才是對的。
        colder_north = lambda axis, o: 8 - 0.8 * o if axis == "ns" else 8
        swing = {"wind_speed_850hPa": [10.0, 20.0, 10.0, 10.0], "wind_direction_850hPa": [180.0, 0.0, 180.0, 180.0]}
        day = upper.analyse(make_upper(t850=colder_north, site=swing), 40.0)[0]
        gradient = -8 / (6_371_000 * math.radians(10))  # 往北每公尺降幾度
        expected = (-(10 * gradient) - (-20 * gradient)) / 2 * 86400
        self.assertAlmostEqual(day.tadv850, expected, places=3)
        self.assertLess(day.tadv850, 0)

    def test_divergence_includes_the_spherical_term(self):
        # 風場各處都是南風 20 m/s:平面上沒有輻散,但經線往極地收攏,球面上是輻合
        day = upper.analyse(make_upper(u250=lambda *_: 0.0, v250=lambda *_: 20.0, latitude=40.0), 40.0)[0]
        self.assertAlmostEqual(day.div250, -20 * math.tan(math.radians(40)) / 6_371_000 * 1e5, places=3)

    def test_cape_and_pwat_are_optional_and_cape_marks_instability(self):
        plain = upper.analyse(make_upper(), 40.0)[0]
        self.assertIsNone(plain.cape)
        self.assertIsNone(plain.pwat)
        self.assertFalse(plain.unstable)
        juicy = {"cape": [200.0, 1500.0, 0.0, 0.0], "total_column_integrated_water_vapour": [60.0, 62.0, 40.0, 40.0]}
        first, second = upper.analyse(make_upper(site=juicy), 40.0)
        self.assertEqual((first.cape, first.pwat), (1500.0, 61.0))  # CAPE 取當天最大,PWAT 取平均
        self.assertLess(first.k, upper.K_UNSTABLE)
        self.assertTrue(first.unstable)  # K 指數不到 30,但 CAPE 夠大
        self.assertFalse(second.unstable)
        self.assertEqual(report.upper_rows([first])[1][-3:], ["61", f"{first.k:.0f}", "1500"])

    def test_trough_passage(self):
        row = lambda day, regime, trough: upper.UpperDay(day=day, regime=regime, trough=trough)
        self.assertEqual(
            upper.trough_passage([row("d1", "槽前", -20.0), row("d2", "槽前", -8.0), row("d3", "槽後", 9.0)]), ("d2", "d3")
        )
        self.assertEqual(upper.trough_passage([row("d1", "槽前", -8.0), row("d2", "槽線過境", 1.0)]), ("d2", "d2"))
        self.assertIsNone(upper.trough_passage([row("d1", "槽前", -30.0), row("d2", "槽前", -25.0)]))


class RealUpperDataTest(unittest.TestCase):
    """2026-11-12 台北的真實高空資料(成員已抽稀)。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = {r.day: r for r in upper.analyse(load_upper(), 25.0, 7)}

    def test_ridge_and_subtropical_high_on_the_first_day(self):
        first = self.rows["2026-11-12"]
        self.assertEqual(first.regime, "脊線上")
        self.assertTrue(first.subtropical_high)
        self.assertEqual(first.jet_offset, 10)  # 噴流軸在北方 10°
        self.assertGreater(first.jet_core, 50)

    def test_trough_approaches_then_passes(self):
        offsets = [self.rows[f"2026-11-{d}"].trough for d in (13, 14, 16)]
        self.assertTrue(offsets[0] < offsets[1] < offsets[2] < 0)  # 槽線逐日東移靠近
        self.assertEqual(upper.trough_passage(list(self.rows.values())), ("2026-11-16", "2026-11-17"))

    def test_rainy_day_under_dry_mid_levels_is_flagged_as_shallow(self):
        day = self.rows["2026-11-15"]
        self.assertEqual(day.outlook, "shallow")
        forecast = analysis.daily_forecast(load_surface(), 7)
        text = "\n".join(report.upper_impact(list(self.rows.values()), forecast))
        self.assertIn("11/15(日)", text)
        self.assertIn("雨量不大", text)

    def test_low_level_fronts_are_found_on_the_days_the_wind_turns(self):
        self.assertEqual([d for d, r in self.rows.items() if r.shift], ["2026-11-15", "2026-11-18"])
        surge = self.rows["2026-11-18"]
        self.assertEqual((upper.compass(surge.shift.from_dir), upper.compass(surge.shift.to_dir)), ("西", "北"))
        self.assertGreater(surge.shift.cooling, 5)  # 東北季風增強:一天內 925 hPa 降溫超過 5°C
        # 逐日向量平均會把這天的風抵銷到只剩 2 m/s
        self.assertGreater(surge.wind925, 4)
        forecast = analysis.daily_forecast(load_surface(), 7)
        line = next(t for t in report.upper_impact(list(self.rows.values()), forecast) if t.startswith("11/18"))
        self.assertIn("低層鋒面", line)
        self.assertIn("與系集降雨機率 97% 一致", line)

    def test_mid_level_drying_within_the_day_is_reported(self):
        day = self.rows["2026-11-15"]
        self.assertGreater(day.rh700_early, 70)
        self.assertLess(day.rh700_late, 30)
        forecast = analysis.daily_forecast(load_surface(), 7)
        line = next(t for t in report.upper_impact(list(self.rows.values()), forecast) if t.startswith("11/15"))
        self.assertIn("中層在當天轉乾", line)

    def test_all_levels_are_filled(self):
        for row in self.rows.values():
            for name in ("wind250", "div250", "z500", "rh700", "t850", "tadv850", "wind925", "k"):
                self.assertIsNotNone(getattr(row, name), f"{row.day} {name}")


class SiteTest(unittest.TestCase):
    def test_page_and_data_are_generated_offline(self):
        from weekcast import site

        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(site.main(["台北", "--out", tmp, "--from-file", FIXTURE]), 0)
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            data = json.loads((Path(tmp) / "data.json").read_text(encoding="utf-8"))
        self.assertIn('<article class="place" data-name="台北"', page)
        self.assertIn("逐日高空分析", page)
        self.assertEqual(page.count('class="d d'), 7)  # 時間軸每天一欄
        self.assertEqual(page.count("<svg"), 2)
        self.assertEqual(len(data["places"]["台北"]["days"]), 7)
        # 四個頁面都在同一個檔案裡,用網址切換;地點頁有六個分頁
        for name in ("overview", "place", "radar", "about"):
            self.assertEqual(page.count(f'<section class="page{" on" if name == "overview" else ""}" data-page="{name}"'), 1)
        self.assertEqual(page.count('<section class="view"'), len(site.TABS))
        self.assertIn('<a href="#/place/0/summary">台北</a>', page)  # 總覽的表格連到地點頁
        self.assertIn("逐日分區預報", page)
        self.assertEqual(len(data["places"]["台北"]["briefing"]["stages"]), 3)
        self.assertIsNone(data["places"]["台北"]["verification"])
        self.assertIn("還沒有可以校驗的紀錄", page)

    def test_trend_section_uses_the_previously_published_data(self):
        from weekcast import site

        now = datetime.now(timezone.utc)
        days = {d: {"tmax": 25.0, "tmin": 20.0, "pop": pop} for d, pop in (("2026-11-16", 0.30), ("2026-11-17", 0.36))}
        older = {d: v | {"pop": v["pop"] - 0.2 if d == "2026-11-16" else v["pop"]} for d, v in days.items()}
        previous = {
            "generated": (now - timedelta(hours=6)).isoformat(),
            "places": {},
            "runs": {
                "台北": [{"at": (now - timedelta(hours=12)).isoformat(), "days": older}, {"at": (now - timedelta(hours=6)).isoformat(), "days": days}],
                "高雄": [{"at": (now - timedelta(hours=6)).isoformat(), "days": days}],
            },
        }
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            (Path(tmp) / "previous.json").write_text(json.dumps(previous), encoding="utf-8")
            code = site.main(["台北", "--out", tmp, "--from-file", FIXTURE, "--previous", str(Path(tmp) / "previous.json")])
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            data = json.loads((Path(tmp) / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertIn("<h2>預報趨勢:和前幾次比</h2>", page)
        self.assertIn("11/16(一):降雨機率提高 42 個百分點;降雨機率連續 2 次上修(10% → 30% → 72%)", page)
        self.assertEqual(len(data["runs"]["台北"]), 3)  # 先前兩次加上這一次
        self.assertNotIn("runs", data["places"]["台北"])
        self.assertEqual(data["places"]["台北"]["comparison"]["trends"][0]["kind"], "rising")

    def test_first_run_and_old_format_have_no_trend_but_do_not_fail(self):
        from weekcast import site

        self.assertEqual(site.load_previous(None), {})
        self.assertEqual(site.load_previous(Path("/nonexistent.json")), {})
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            old = Path(tmp) / "old.json"
            old.write_text("not json", encoding="utf-8")
            self.assertEqual(site.load_previous(old), {})
            # 舊版 data.json 沒有 runs:用它當時的逐日預報當作一筆紀錄
            old.write_text(json.dumps({"generated": "2026-11-12T02:00:00+00:00", "places": {"台北": {"days": [
                {"day": "2026-11-13", "tmax": {"p50": 28.0}, "tmin": {"p50": 21.0}, "pop": 0.05}]}}}), encoding="utf-8")
            self.assertEqual(site.load_previous(old)["台北"][0]["days"]["2026-11-13"]["pop"], 0.05)
            self.assertEqual(site.main(["台北", "--out", tmp, "--from-file", FIXTURE]), 0)
            self.assertIn("還沒有 5 小時以前的預報可以比較", (Path(tmp) / "index.html").read_text(encoding="utf-8"))

    def test_text_is_escaped(self):
        from weekcast import site

        page = site.build_page([("<b>x</b>", "")], datetime(2026, 1, 1, tzinfo=timezone.utc), ["a&b"])
        self.assertNotIn("<b>x</b>", page)
        self.assertIn('data-generated="2026-01-01T00:00:00+00:00"', page)
        self.assertIn('id="notes"', page)
        self.assertIn("這不是官方預報", page)
        self.assertIn("a&amp;b", page)

    def test_no_page_when_every_place_fails(self):
        from weekcast import site

        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(site.main(["台北", "--out", tmp, "--from-file", "/nonexistent.json"]), 1)
            self.assertFalse((Path(tmp) / "index.html").exists())


class RateLimitTest(unittest.TestCase):
    def fake(self, outcomes):
        import urllib.error
        from unittest import mock

        calls, sleeps = [], []

        def urlopen(request, timeout=None):
            calls.append(request.full_url)
            outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
            if isinstance(outcome, tuple):
                code, reason = outcome
                body = io.BytesIO(json.dumps({"error": True, "reason": reason}).encode())
                raise urllib.error.HTTPError(request.full_url, code, "err", {}, body)
            return contextlib.closing(io.BytesIO(json.dumps(outcome).encode()))

        patches = [mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep", sleeps.append)]
        return calls, sleeps, patches

    def run_get(self, outcomes):
        calls, sleeps, patches = self.fake(outcomes)
        with patches[0], patches[1]:
            try:
                result = client._get_json("https://example.test/x", {})
            except client.FetchError as err:
                result = err
        return result, calls, sleeps

    def test_minutely_limit_waits_and_retries(self):
        limited = (429, "Minutely API request limit exceeded. Please try again in one minute.")
        result, calls, sleeps = self.run_get([limited, limited, {"ok": 1}])
        self.assertEqual(result, {"ok": 1})
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [client.MINUTE_WAIT, client.MINUTE_WAIT])

    def test_minutely_limit_gives_up_eventually(self):
        limited = (429, "Minutely API request limit exceeded.")
        result, calls, _ = self.run_get([limited])
        self.assertIsInstance(result, client.FetchError)
        self.assertEqual(len(calls), client.MAX_MINUTE_WAITS + 1)

    def test_daily_limit_fails_immediately(self):
        result, calls, sleeps = self.run_get([(429, "Daily API request limit exceeded. Please try again tomorrow.")])
        self.assertIsInstance(result, client.FetchError)
        self.assertEqual((len(calls), sleeps), (1, []))


class PeriodTest(unittest.TestCase):
    def raw(self, rain_by_step, members=2):
        """一整天每 3 小時一筆,外加隔天 00:00(晚上時段的最後一筆雨量標示在那裡)。"""
        times = [f"2026-01-01T{h:02d}:00" for h in range(0, 24, 3)] + ["2026-01-02T00:00"]
        hourly = {"time": times}
        for m in range(members):
            tag = "" if m == 0 else f"_member{m:02d}"
            hourly[f"precipitation{tag}_ncep_gefs025"] = list(rain_by_step)
            hourly[f"temperature_2m{tag}_ncep_gefs025"] = [18, 17, 19, 23, 26, 27, 24, 21, 19]
            hourly[f"cloud_cover{tag}_ncep_gefs025"] = [20] * 9
        return {"hourly": hourly}

    def test_rain_lands_in_the_right_part_of_the_day(self):
        from weekcast import periods

        # 雨量是往前累積的:標示 15:00 的 2 mm 下在 12–15 時,標示 18:00 的 3 mm 下在 15–18 時
        rows = {r.name: r for r in periods.period_forecast(self.raw([0, 0, 0, 0, 0, 2, 3, 0, 0]), 1)}
        self.assertEqual(list(rows), ["凌晨", "上午", "下午", "晚上"])
        self.assertEqual([rows[n].pop for n in rows], [0, 0, 1, 0])
        self.assertAlmostEqual(rows["下午"].precip.p50, 5)
        self.assertAlmostEqual(rows["下午"].temp.p50, 26.5)  # 氣溫是瞬間值,取 12 與 15 時
        self.assertAlmostEqual(rows["凌晨"].temp.p50, 17.5)
        self.assertEqual(rows["上午"].sky, "晴")

    def test_rain_stamped_at_noon_fell_in_the_morning(self):
        from weekcast import periods

        rows = {r.name: r for r in periods.period_forecast(self.raw([9, 0, 0, 0, 4, 0, 0, 0, 6]), 1)}
        # 標示 00:00 的 9 mm 是前一天晚上下的,不屬於今天;標示隔天 00:00 的 6 mm 屬於今天晚上
        self.assertEqual([rows[n].precip.p50 for n in rows], [0, 4, 0, 6])

    def test_period_totals_add_up_to_the_day(self):
        from weekcast import periods

        rows = periods.period_forecast(self.raw([5.0, 0.4, 0.3, 0.1, 0.0, 0.1, 0.1, 0.1, 0.3]), 1)
        self.assertAlmostEqual(sum(r.precip.p50 for r in rows), 1.4)

    def test_period_without_its_last_step_has_no_rain_total(self):
        from weekcast import periods

        raw = self.raw([0, 0, 0, 0, 0, 0, 0, 3, 9])
        for series in raw["hourly"].values():
            series.pop()  # 資料只到 21:00,晚上時段缺最後 3 小時
        evening = periods.period_forecast(raw)[-1]
        self.assertEqual(evening.name, "晚上")
        self.assertIsNone(evening.pop)
        self.assertIsNone(evening.precip)
        self.assertAlmostEqual(evening.temp.p50, 22.5)
        self.assertEqual(report.period_rows([evening])[1][-1], "22°")

    def test_real_data_has_four_periods_for_each_of_seven_days(self):
        from weekcast import periods

        rows = periods.period_forecast(client.load_raw(FIXTURE)["periods"], 7)
        self.assertEqual(len(rows), 28)
        # 存檔只到最後一天 21:00,所以最後一個晚上沒有完整的雨量
        self.assertEqual([r.name for r in rows if r.pop is None], ["晚上"])
        for r in rows:
            self.assertTrue(r.pop is None or 0 <= r.pop <= 1)
            self.assertTrue(r.temp.p10 <= r.temp.p50 <= r.temp.p90)
        afternoons = [r.temp.p50 for r in rows if r.name == "下午"]
        dawns = [r.temp.p50 for r in rows if r.name == "凌晨"]
        # 平常下午比凌晨暖;11/18 冷空氣南下,白天升不了溫,所以只檢查前六天
        self.assertTrue(all(a > d for a, d in zip(afternoons[:6], dawns[:6])))
        self.assertLess(afternoons[6] - dawns[6], 2)

    def test_fetch_asks_for_one_extra_day(self):
        from unittest import mock

        with mock.patch.object(client, "_get_json", return_value={}) as get:
            client.fetch_periods(25.0, 121.5, 7, "Asia/Taipei")
        self.assertEqual(get.call_args.args[1]["forecast_days"], 8)

    def test_report_and_page_show_periods(self):
        from weekcast import site

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--from-file", FIXTURE])
        self.assertIn("時段預報", out.getvalue())
        self.assertIn("下午 12–18", out.getvalue())
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            site.main(["台北", "--out", tmp, "--from-file", FIXTURE])
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            data = json.loads((Path(tmp) / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(page.count('class="cell"'), 28)
        self.assertIn('id="radar"', page)
        self.assertIn('data-lat="25.0"', page)
        self.assertEqual(len(data["places"]["台北"]["periods"]), 28)


class BrowserSearchTest(unittest.TestCase):
    """網頁搜尋欄用的進入點:抓資料的方式不同,分析必須和命令列相同。"""

    def run_forecast(self, fail=()):
        import asyncio
        from weekcast import browser

        bundle = client.load_raw(FIXTURE)
        asked = []

        async def get(params):
            asked.append(params)
            kind = "cross" if "," in params["latitude"] else "profile" if "wind_speed_unit" in params else (
                "periods" if params.get("temporal_resolution") == "hourly_3" else "surface")
            if kind in fail:
                raise RuntimeError("HTTP 429")
            return {"surface": bundle["surface"], "profile": bundle["upper"]["profile"],
                    "cross": bundle["upper"]["cross"]["data"], "periods": bundle["periods"]}[kind]

        return asyncio.run(browser.forecast("枋寮", 25.05, 121.53, 7, get)), asked

    def test_same_sections_as_the_scheduled_page(self):
        block, asked = self.run_forecast()
        self.assertEqual(len(asked), 4)
        for needle in ("凌晨、上午、下午、晚上", "逐日高空分析", "逐日預報", "133 個系集成員", "這一週怎麼分段"):
            self.assertIn(needle, block)
        for view in ("summary", "daily", "synoptic", "models", "risk", "verify"):
            self.assertEqual(block.count(f'data-view="{view}"'), 1)
        self.assertIn("用搜尋欄查的地點沒有保存過去的預報", block)  # 搜尋的地點沒有紀錄,不能校驗
        self.assertEqual(asked[1]["timezone"], "Asia/Taipei")  # 高空資料沿用地面資料的時區
        self.assertTrue(client.url_for(asked[0]).startswith("https://ensemble-api.open-meteo.com/v1/ensemble?latitude=25.05"))

    def test_upper_air_failure_still_gives_surface_forecast(self):
        block, _ = self.run_forecast(fail=("cross", "periods"))
        self.assertIn("逐日預報", block)
        self.assertNotIn("逐日高空分析", block)
        self.assertIn("這次沒有取得高空資料", block)
        self.assertNotIn("凌晨、上午、下午、晚上", block)

    def test_engine_archive_contains_the_package(self):
        import zipfile
        from weekcast import site

        with tempfile.TemporaryDirectory() as tmp:
            site.write_engine(Path(tmp) / "weekcast.zip")
            names = zipfile.ZipFile(Path(tmp) / "weekcast.zip").namelist()
        for module in ("__init__", "analysis", "browser", "client", "periods", "report", "site", "systems", "upper"):
            self.assertIn(f"weekcast/{module}.py", names)


class PlainSummaryTest(unittest.TestCase):
    """給非專業讀者的白話總結。"""

    JARGON = ("槽", "脊線", "輻散", "輻合", "平流", "hPa", "噴流", "K 指數")

    @classmethod
    def setUpClass(cls):
        from weekcast import periods, plain

        cls.plain = plain
        cls.forecast = analysis.daily_forecast(load_surface(), 7)
        cls.upper = upper.analyse(load_upper(), 25.0, 7)
        cls.periods = periods.period_forecast(client.load_raw(FIXTURE)["periods"], 7)
        cls.days = plain.plain_days(cls.forecast, cls.upper, cls.periods)

    def test_one_entry_per_day_without_jargon(self):
        self.assertEqual([d.day for d in self.days], [f.day for f in self.forecast])
        text = self.plain.overview(self.forecast, self.upper) + "".join(d.text() for d in self.days)
        for word in self.JARGON:
            self.assertNotIn(word, text)

    def test_rainy_day_says_when_and_what_to_bring(self):
        saturday = next(d for d in self.days if d.day == "2026-11-14")
        self.assertIn("可能下雨(機率 60%)", saturday.weather)
        self.assertIn("主要在下午和晚上", saturday.weather)
        self.assertIn("帶把傘備用", saturday.advice)
        self.assertTrue(saturday.why.startswith("有機會下雨,因為高空有一道低壓帶從西邊靠近"))
        self.assertIn("看法不一致", saturday.caution)

    def test_shallow_rain_is_explained_as_light(self):
        sunday = next(d for d in self.days if d.day == "2026-11-15")
        self.assertIn("地面會濕的小雨(約 5 毫米)", sunday.weather)
        # 先講會下雨的原因,再講為什麼雨不大
        self.assertTrue(sunday.why.startswith("會下雨,是因為"))
        self.assertIn("雲長不高,所以雨不會太大", sunday.why)
        self.assertEqual(sunday.advice, ["出門帶傘"])

    def test_cooling_day_gets_a_jacket(self):
        last = next(d for d in self.days if d.day == "2026-11-18")
        self.assertIn("比前一天涼", last.weather)
        self.assertIn("明顯變涼,加件外套", last.advice)
        # 高空有高壓卻下雨:要說明兩者的關係,不能只把兩件事並列
        self.assertIn("不過上空有副熱帶高壓,空氣下沉,所以雨不會太大", last.why)

    def test_week_summary_tells_the_story_in_order(self):
        week = self.plain.week_summary(self.forecast, self.upper)
        self.assertEqual(week.headline, "未來一週多雨,只有11/12(四)到11/13(五)、11/17(二)比較穩定。")
        self.assertEqual(len(week.story), 5)  # 穩定 → 可能下雨 → 下雨 → 穩定 → 下雨
        self.assertTrue(week.story[0].startswith("11/12(四)到11/13(五)天氣穩定"))
        self.assertIn("會下雨,而且明顯變涼", week.story[-1])
        self.assertIn("低壓帶接近", week.cause)
        self.assertEqual(len(week.watch), 2)
        for word in self.JARGON:
            self.assertNotIn(word, week.text())

    def test_week_summary_headlines(self):
        fc = self.forecast[0]
        day = lambda i, pop: analysis.DayForecast(**{**fc.__dict__, "day": f"2026-01-0{i + 1}", "lead": i, "pop": pop})
        headline = lambda pops: self.plain.week_summary([day(i, p) for i, p in enumerate(pops)]).headline
        self.assertIn("幾乎不會下雨", headline([0.0, 0.1, 0.05]))
        self.assertIn("天天都會下雨", headline([0.9, 0.8, 0.7]))
        self.assertIn("前壞後好", headline([0.9, 0.8, 0.1]))
        self.assertIn("前好後壞", headline([0.1, 0.1, 0.5, 0.9]))
        # 好壞交替時不能說前好後壞:穩定 → 下雨 → 穩定 → 可能下雨(線上枋寮實際出現過)
        mixed = headline([0.01, 0.01, 0.75, 0.79, 0.27, 0.08, 0.45])
        self.assertEqual(mixed, "未來一週大致穩定,只有1/3(六)到1/4(日)、1/7(三)會下雨。")
        self.assertIn("多雨,只有", headline([0.9, 0.1, 0.8, 0.1, 0.9]))
        self.assertIsNone(self.plain.week_summary([]))

    def test_week_summary_leads_the_report_and_the_pages(self):
        from weekcast import site

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--from-file", FIXTURE])
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[4], "未來一週天氣總結")  # 緊接在標題與資料來源之後
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            site.main(["台北", "--out", tmp, "--from-file", FIXTURE])
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            data = json.loads((Path(tmp) / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(page.count('class="week"'), 2)  # 總覽頁一塊、地點頁一塊
        overview_page = page[page.index('data-page="overview"'):]
        self.assertLess(overview_page.index('class="week"'), overview_page.index("逐日分區預報"))
        self.assertIn("多雨", data["places"]["台北"]["plain"]["week"]["headline"])

    def test_wording_matches_the_sky_labels_on_the_same_page(self):
        # 表格寫「晴偶陣雨」的日子(降雨機率 20–40%),白話不能說成「可能下雨」
        for fc, day in zip(self.forecast, self.days):
            if fc.pop >= 0.7:
                self.assertTrue(day.weather.startswith("會下雨"), day.weather)
            elif fc.pop >= 0.4:
                self.assertTrue(day.weather.startswith("可能下雨"), day.weather)
            elif fc.pop >= 0.2:
                self.assertTrue(day.weather.startswith("大致不會下雨,但可能有短暫陣雨"), day.weather)

    def test_every_day_has_advice_and_a_reason_that_fits_the_weather(self):
        for fc, day in zip(self.forecast, self.days):
            self.assertTrue(day.advice, day.day)
            if fc.pop < 0.4:  # 不太會下雨的日子,原因不能以「會下雨」開頭
                self.assertFalse(day.why.startswith(("會下雨", "有機會下雨")), day.why)

    def test_technical_names_get_plain_words(self):
        said = self.plain.system_words("高空槽接近 → 鋒面通過、東北季風增強")
        self.assertEqual(said, "高空有低壓帶靠近,天氣轉不穩定,接著冷暖空氣的交界通過,有雨、之後轉涼;東北風增強,變涼、迎風面有雨")
        self.assertEqual(self.plain.system_words("沒見過的名稱"), "沒見過的名稱")
        self.assertEqual([self.plain.rain_words(mm) for mm in (0.2, 3, 10, 20, 60)], ["幾乎沒有雨", "地面會濕的小雨", "需要撐傘的雨", "雨勢明顯", "大雨"])
        self.assertEqual([self.plain.wind_words(v) for v in (5, 20, 40, 60)], ["微風", "有風", "風偏大", "強風"])

    def test_works_without_upper_air_or_periods(self):
        days = self.plain.plain_days(self.forecast)
        self.assertEqual(len(days), 7)
        self.assertTrue(all(d.why == "" for d in days))
        self.assertTrue(self.plain.overview(self.forecast, None))

    def test_dry_sunny_day_advice(self):
        fc = self.forecast[1]
        clear = analysis.DayForecast(**{**fc.__dict__, "pop": 0.02, "cloud": analysis.Spread(10, 20, 30)})
        self.assertEqual(self.plain._advice(clear, None, None)[0], "不用帶傘")
        self.assertIn("適合戶外活動和曬衣服", self.plain._advice(clear, None, None))
        self.assertIn("天氣晴朗", self.plain._weather(clear, None, None))

    def test_shown_in_report_page_and_json(self):
        from weekcast import site

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--from-file", FIXTURE])
        self.assertIn("白話總結", out.getvalue())
        self.assertIn("名詞對照", out.getvalue())
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            site.main(["台北", "--out", tmp, "--from-file", FIXTURE])
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            data = json.loads((Path(tmp) / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(page.count('class="pd"'), 7)
        # 白話總結是每個地點的最後一個分頁
        self.assertEqual(site.TABS[-1], ("plain", "白話總結"))
        self.assertLess(page.index('data-view="verify"'), page.index('data-view="plain"'))
        self.assertIn("怎麼準備:", page)
        self.assertEqual(len(data["places"]["台北"]["plain"]["days"]), 7)


class ReadabilityTest(unittest.TestCase):
    """給一般讀者的版面:白話在前、手機看得到全部、日期有今天明天、資料多舊看得出來。"""

    @classmethod
    def setUpClass(cls):
        from weekcast import site

        cls.site = site
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            site.main(["台北", "--out", tmp, "--from-file", FIXTURE])
            cls.page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
        cls.place = cls.page[cls.page.index('<article class="place"'):]

    def test_plain_summary_comes_before_the_professional_one(self):
        self.assertLess(self.place.index('class="week"'), self.place.index('class="ribbon"'))
        self.assertLess(self.place.index('class="ribbon"'), self.place.index("這一週分成"))
        summary_tab = self.place[self.place.index('data-view="summary"'):self.place.index('data-view="daily"')]
        self.assertIn("這一週分成", summary_tab)  # 專業版的結論收在摘要分頁裡

    def test_technical_stage_names_carry_a_plain_gloss(self):
        self.assertEqual(self.place.count('<small class="say">'), 3)
        self.assertIn("高壓籠罩,天氣穩定、偏暖", self.place)

    def test_numbers_are_explained(self):
        self.assertIn("這些數字怎麼看", self.place)
        for term, _ in self.site.plain.HOW_TO_READ:
            self.assertIn(f"<dt>{term}</dt>", self.place)

    def test_days_are_tagged_for_today_and_tomorrow_labels(self):
        self.assertEqual(self.place.count('class="d d'), 7)
        self.assertEqual(self.place.count('data-day="2026-11-12"'), 2)  # 時間軸與白話總結
        self.assertIn("markDays()", self.page)
        self.assertIn("'今天'", self.page)

    def test_page_reports_how_old_the_data_is(self):
        self.assertIn("沒有更新,內容可能過時", self.page)
        self.assertIn("小時前", self.page)

    def test_phone_layout_rules_exist(self):
        css = self.page[self.page.index("@media (max-width:760px)"):]
        self.assertIn(".tabs{position:static;flex-wrap:wrap", css)  # 七個分頁都看得到
        self.assertIn('grid-template-areas:"date wx temp" "pop pop conf"', css)  # 時間軸每天一列

    def test_radar_pin_is_not_stretched_by_chart_styles(self):
        self.assertIn("#map svg{display:inline;min-width:0;width:auto}", self.page)
        self.assertIn("ResizeObserver", self.page)
        self.assertIn('class="sub legend"', self.page)

    def test_about_page_counts_the_tabs_correctly(self):
        self.assertIn("下面有七個分頁", self.page)
        self.assertEqual(len(self.site.TABS), 7)


class ScheduleGuardTest(unittest.TestCase):
    """排程每小時檢查一次,資料還新就不重算。"""

    def run_site(self, age_hours, *extra):
        from weekcast import site

        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp) / "previous.json"
            at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
            previous.write_text(json.dumps({"generated": at.isoformat(), "places": {}}), encoding="utf-8")
            out = Path(tmp) / "site"
            with contextlib.redirect_stdout(io.StringIO()) as said:
                code = site.main(["台北", "--out", str(out), "--from-file", FIXTURE, "--previous", str(previous), *extra])
            return code, (out / "index.html").exists(), said.getvalue()

    def test_fresh_data_is_left_alone(self):
        code, built, said = self.run_site(2, "--only-if-older-than", "5")
        self.assertEqual((code, built), (0, False))
        self.assertIn("這次不重算", said)

    def test_old_data_is_rebuilt(self):
        self.assertEqual(self.run_site(6, "--only-if-older-than", "5")[:2], (0, True))

    def test_without_the_option_it_always_builds(self):
        self.assertEqual(self.run_site(0.1)[:2], (0, True))

    def test_unreadable_previous_file_means_build(self):
        from weekcast import site

        self.assertIsNone(site.previous_age_hours(Path("/nonexistent.json"), datetime.now(timezone.utc)))
        self.assertIsNone(site.previous_age_hours(None, datetime.now(timezone.utc)))

    def test_workflow_checks_hourly_and_skips_deploy_when_nothing_was_built(self):
        text = (Path(__file__).parent.parent / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "23 * * * *"', text)
        self.assertIn("--only-if-older-than 5", text)
        self.assertIn("if: needs.build.outputs.built == 'true'", text)


class TownSearchTest(unittest.TestCase):
    """內建的臺灣鄉鎮市區清單:搜尋不依賴外部服務,結果一定是中文。"""

    def test_all_townships_are_listed_once_within_taiwan(self):
        from weekcast.towns import TOWNS

        self.assertEqual(len(TOWNS), 368)
        self.assertEqual(len({(county, town) for county, town, _, _ in TOWNS}), 368)
        for county, town, lat, lon in TOWNS:
            self.assertTrue(21.8 < lat < 26.5 and 118.1 < lon < 122.1, (county, town))
            self.assertNotIn("臺", county + town)
            self.assertIn(town[-1], "鄉鎮市區")

    def test_names_can_be_typed_several_ways(self):
        first = lambda q: client.find_towns(q)[0][0]
        for q in ("東港", "東港鎮", "屏東縣東港鎮", "屏東東港", " 東港 "):
            self.assertEqual(first(q), "屏東縣東港鎮", q)
        self.assertEqual(first("臺東市"), "台東縣台東市")  # 臺與台視為相同
        self.assertEqual(client.find_towns("沒有這個地方"), [])
        self.assertEqual(client.find_towns(""), [])

    def test_same_named_townships_are_all_offered(self):
        names = [t[0] for t in client.find_towns("信義")]
        self.assertEqual(sorted(names), ["南投縣信義鄉", "台北市信義區", "基隆市信義區"])

    def test_coordinates_are_near_the_real_town(self):
        _, lat, lon = client.find_towns("恆春")[0]
        self.assertAlmostEqual(lat, 22.0, delta=0.1)
        self.assertAlmostEqual(lon, 120.75, delta=0.1)

    def test_command_line_lookup_uses_the_list_without_network(self):
        place = client.geocode("恆春")
        self.assertEqual((place.name, place.country), ("屏東縣恆春鎮", "台灣"))
        with self.assertRaises(client.FetchError) as caught:
            client.geocode("信義區")
        self.assertIn("請連縣市一起輸入", str(caught.exception))
        self.assertEqual(client.geocode("台北市信義區").name, "台北市信義區")
        self.assertEqual(client.geocode("枋寮").latitude, 22.37)  # 原本內建的地點不受影響

    def test_page_carries_the_list_and_chinese_error_messages(self):
        from weekcast import site

        page = site.build_page([("台北", "", 25.0, 121.5)], datetime(2026, 1, 1, tzinfo=timezone.utc), [])
        self.assertIn('["屏東縣","東港鎮",22.461,120.474]', page)
        self.assertNotIn("__TOWNS__", page)
        self.assertIn("function localHits", page)
        self.assertIn("氣象資料來源暫時忙線", page)
        self.assertIn("isComposing", page)  # 注音選字中不觸發搜尋

    def test_engine_archive_includes_the_town_list(self):
        import zipfile
        from weekcast import site

        with tempfile.TemporaryDirectory() as tmp:
            site.write_engine(Path(tmp) / "w.zip")
            self.assertIn("weekcast/towns.py", zipfile.ZipFile(Path(tmp) / "w.zip").namelist())


if __name__ == "__main__":
    unittest.main()
