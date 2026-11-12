import contextlib
import io
import json
import math
import statistics
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weekcast import analysis, client, history, report, upper
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
        self.assertIn("11/18 三", text)

    def test_padding_counts_cjk_as_double_width(self):
        self.assertEqual(report._width("11/12 四"), 8)
        self.assertEqual(report._width(report._pad("台北", 7)), 7)
        self.assertEqual(report._pad("5", 3, right=True), "  5")


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


def make_upper(days=2, z500=None, u250=None, v250=None, t850=None, site=None, latitude=40.0):
    """合成的高空資料。z500/u250/v250/t850 是 (軸, 偏移) → 數值的函式。"""
    times = [f"2026-01-0{d + 1}T{h:02d}:00" for d in range(days) for h in (0, 12)]
    n = len(times)
    const = lambda value: [value] * n
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
        self.assertIn("11/15 日", text)
        self.assertIn("雨量不大", text)

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
        self.assertIn("台北 一週天氣預報", page)
        self.assertIn("高空分析", page)
        self.assertEqual(page.count('class="day"'), 7)
        self.assertEqual(page.count("<svg"), 2)
        self.assertEqual(len(data["places"]["台北"]["days"]), 7)

    def test_text_is_escaped(self):
        from weekcast import site

        page = site.build_page([("<b>x</b>", "")], datetime(2026, 1, 1, tzinfo=timezone.utc), ["a&b"])
        self.assertNotIn("<b>x</b>", page)
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
        times = [f"2026-01-01T{h:02d}:00" for h in range(0, 24, 3)]
        hourly = {"time": times}
        for m in range(members):
            tag = "" if m == 0 else f"_member{m:02d}"
            hourly[f"precipitation{tag}_ncep_gefs025"] = list(rain_by_step)
            hourly[f"temperature_2m{tag}_ncep_gefs025"] = [18, 17, 19, 23, 26, 27, 24, 21]
            hourly[f"cloud_cover{tag}_ncep_gefs025"] = [20] * 8
        return {"hourly": hourly}

    def test_rain_lands_in_the_right_part_of_the_day(self):
        from weekcast import periods

        rows = {r.name: r for r in periods.period_forecast(self.raw([0, 0, 0, 0, 2, 3, 0, 0]))}
        self.assertEqual(list(rows), ["凌晨", "上午", "下午", "晚上"])
        self.assertEqual([rows[n].pop for n in rows], [0, 0, 1, 0])
        self.assertAlmostEqual(rows["下午"].precip.p50, 5)
        self.assertAlmostEqual(rows["下午"].temp.p50, 26.5)
        self.assertAlmostEqual(rows["凌晨"].temp.p50, 17.5)
        self.assertEqual(rows["上午"].sky, "晴")

    def test_period_totals_add_up_to_the_day(self):
        from weekcast import periods

        rows = periods.period_forecast(self.raw([0.4, 0.3, 0.1, 0.0, 0.1, 0.1, 0.1, 0.3]))
        self.assertAlmostEqual(sum(r.precip.p50 for r in rows), 1.4)

    def test_real_data_has_four_periods_for_each_of_seven_days(self):
        from weekcast import periods

        rows = periods.period_forecast(client.load_raw(FIXTURE)["periods"], 7)
        self.assertEqual(len(rows), 28)
        for r in rows:
            self.assertTrue(0 <= r.pop <= 1)
            self.assertTrue(r.temp.p10 <= r.temp.p50 <= r.temp.p90)
        afternoons = [r.temp.p50 for r in rows if r.name == "下午"]
        dawns = [r.temp.p50 for r in rows if r.name == "凌晨"]
        # 平常下午比凌晨暖;11/18 冷空氣南下,白天升不了溫,所以只檢查前六天
        self.assertTrue(all(a > d for a, d in zip(afternoons[:6], dawns[:6])))
        self.assertLess(afternoons[6] - dawns[6], 2)

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


if __name__ == "__main__":
    unittest.main()
