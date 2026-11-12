import contextlib
import io
import json
import math
import statistics
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weekcast import analysis, client, history, report
from weekcast.__main__ import main

FIXTURE = str(Path(__file__).parent / "data" / "taipei_20261112.json.gz")


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
        ens = analysis.parse(make_raw(["2026-01-01"], {"icon_global_eps": system}))
        self.assertNotIn("wind_gusts_10m_max", ens.daily["ICON"])
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
        cls.ens = analysis.parse(client.load_raw(FIXTURE))
        cls.forecast = analysis.daily_forecast(cls.ens, 7)

    def test_all_five_systems_and_members_present(self):
        counts = {s: self.ens.member_count(s) for s in self.ens.systems}
        self.assertEqual(counts, {"ECMWF": 51, "GEFS": 31, "GEPS": 21, "ICON": 40, "AIFS": 51})

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
        self.assertIn("194 個系集成員", text)
        self.assertIn("11/18 三", text)

    def test_padding_counts_cjk_as_double_width(self):
        self.assertEqual(report._width("11/12 四"), 8)
        self.assertEqual(report._width(report._pad("台北", 7)), 7)
        self.assertEqual(report._pad("5", 3, right=True), "  5")


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.forecast = analysis.daily_forecast(analysis.parse(client.load_raw(FIXTURE)), 7)
        self.now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)

    def test_compares_against_an_older_run_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertIsNone(history.compare(d, 25, 121.5, self.forecast, self.now))
            history.record(d, 25, 121.5, self.forecast, self.now - timedelta(hours=12))
            history.record(d, 25, 121.5, self.forecast, self.now - timedelta(hours=1))  # 太近,同一次起報
            warmer = analysis.daily_forecast(analysis.parse(client.load_raw(FIXTURE)), 7)
            warmer[0].tmax.p50 += 2
            result = history.compare(d, 25, 121.5, warmer, self.now)
            self.assertAlmostEqual(result.age_hours, 12)
            self.assertAlmostEqual(result.changes[0].tmax, 2, places=1)
            self.assertAlmostEqual(result.changes[1].tmax, 0, places=1)
            text = report.render("台北", analysis.parse(client.load_raw(FIXTURE)), warmer, [], result)
            self.assertIn("高溫上修 2°C", text)

    def test_other_locations_are_not_mixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            history.record(Path(tmp), 25, 121.5, self.forecast, self.now - timedelta(hours=12))
            self.assertIsNone(history.compare(Path(tmp), 35, 139.7, self.forecast, self.now))


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

    def test_known_places_need_no_network(self):
        place = client.geocode("臺北市")
        self.assertAlmostEqual(place.latitude, 25.04)
        self.assertEqual(client.geocode("高雄").longitude, 120.30)


if __name__ == "__main__":
    unittest.main()
