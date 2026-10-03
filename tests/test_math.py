"""Pure-math tests for Grainwell: Morey colour, the strike table and Tinseth.

Everything here runs without a terminal and without waiting for real minutes,
because the maths is separated from the I/O in grainwell.py.
"""

import io
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grainwell as gw  # noqa: E402


def bill(*entries, volume=23.0):
    return gw.MaltBill(
        batch_volume_l=volume,
        fermentables=tuple(gw.Fermentable(name, kg, lov) for name, kg, lov in entries),
    )


class MoreyColourTests(unittest.TestCase):
    """F1 - the malt bill maths."""

    def test_crystal_bill_matches_the_spec_numbers(self):
        b = bill(("Pilsner", 5.0, 1.8), ("Crystal 60", 0.5, 60.0))
        self.assertAlmostEqual(b.total_grist_kg, 5.50, places=6)
        self.assertGreater(b.srm, 8.5)
        self.assertLess(b.srm, 9.5)
        self.assertAlmostEqual(b.ebc, b.srm * 1.97, places=6)
        self.assertEqual(b.colour_word, "amber")

    def test_per_malt_shares_are_90_9_and_9_1_and_sum_to_100(self):
        b = bill(("Pilsner", 5.0, 1.8), ("Crystal 60", 0.5, 60.0))
        shares = [f.share_pct for f in gw.bill_shares(b)]
        self.assertEqual(shares, [90.9, 9.1])
        self.assertAlmostEqual(sum(shares), 100.0, places=6)

    def test_shares_of_three_equal_malts_still_sum_to_100(self):
        self.assertEqual(gw.rounded_shares([1.0, 1.0, 1.0]), [33.4, 33.3, 33.3])
        self.assertAlmostEqual(sum(gw.rounded_shares([1.0, 1.0, 1.0])), 100.0)

    def test_morey_uses_metric_inputs_without_conversion_error(self):
        # 1 kg of 1 L malt in 3.785 L of water is exactly 1 lb in 1 gal.
        b = bill(("Reference", 1.0, 1.0), volume=3.785411784)
        self.assertAlmostEqual(b.mcu, gw.KG_TO_LB, places=6)
        self.assertAlmostEqual(b.srm, gw.morey_srm(gw.KG_TO_LB), places=6)

    def test_a_bill_with_no_colour_scores_zero(self):
        b = gw.MaltBill(batch_volume_l=23.0, fermentables=())
        self.assertEqual(b.mcu, 0.0)
        self.assertEqual(b.srm, 0.0)
        self.assertEqual(b.colour_word, "pale straw")

    def test_colour_words_step_from_straw_to_black(self):
        expected = [
            (1.5, "pale straw"),
            (3.0, "straw"),
            (4.5, "pale gold"),
            (6.0, "gold"),
            (9.2, "amber"),
            (12.0, "deep amber"),
            (15.0, "copper"),
            (20.0, "brown"),
            (25.0, "dark brown"),
            (35.0, "very dark brown"),
            (55.0, "black"),
        ]
        for srm, word in expected:
            with self.subTest(srm=srm):
                self.assertEqual(gw.colour_word(srm), word)

    def test_srgb_hex_is_a_well_formed_colour(self):
        b = bill(("Pilsner", 5.0, 1.8), ("Crystal 60", 0.5, 60.0))
        self.assertRegex(b.srgb_hex, r"^#[0-9A-F]{6}$")

    def test_dark_beer_is_dark_brown_not_saturated_red(self):
        for srm in (41.0, 45.0, 60.0):
            with self.subTest(srm=srm):
                r, g, bl = gw.srm_to_rgb(srm)
                self.assertLess(r, 130)
                self.assertGreater(r, g)
                self.assertGreater(g, bl)

    def test_rgb_darkens_monotonically_with_srm(self):
        brightness = [sum(gw.srm_to_rgb(s)) for s in range(1, 60, 2)]
        self.assertEqual(brightness, sorted(brightness, reverse=True))


class StrikeTableTests(unittest.TestCase):
    """F2 - the strike temperature table."""

    def test_every_tabulated_cell_matches_the_documented_formula(self):
        for _, ratio, cells in gw.STRIKE_TABLE:
            for target, cell in zip(gw.STRIKE_TARGETS, cells):
                expected = (
                    gw.GRAIN_HEAT_FACTOR / ratio * (target - gw.GRAIN_TEMP_REFERENCE) + target
                )
                with self.subTest(ratio=ratio, target=target):
                    self.assertLess(abs(cell - expected), 0.06)

    def test_off_grid_target_is_interpolated_between_table_cells(self):
        lookup = gw.strike_temperature(3.0, 66.0, 20.0)
        self.assertAlmostEqual(lookup.strike_temp_c, 72.4, delta=0.3)
        self.assertEqual(lookup.row_ratio, 3.0)
        self.assertEqual(lookup.row_source, "interpolated")
        self.assertAlmostEqual(lookup.grain_correction_c, 0.0, places=6)

    def test_exact_cell_is_reported_as_tabulated(self):
        lookup = gw.strike_temperature(3.0, 65.0, 20.0)
        self.assertEqual(lookup.row_source, "tabulated")
        self.assertAlmostEqual(lookup.strike_temp_c, 71.3, delta=0.05)

    def test_colder_grain_needs_hotter_strike_water(self):
        cold = gw.strike_temperature(3.0, 66.0, 10.0)
        warm = gw.strike_temperature(3.0, 66.0, 20.0)
        self.assertGreater(cold.strike_temp_c, warm.strike_temp_c)
        self.assertAlmostEqual(cold.grain_correction_c, 1.39, delta=0.02)

    def test_a_thinner_mash_needs_less_strike_heat(self):
        thin = gw.strike_temperature(4.0, 66.0, 20.0)
        thick = gw.strike_temperature(2.0, 66.0, 20.0)
        self.assertLess(thin.strike_temp_c, thick.strike_temp_c)

    def test_values_outside_the_grid_are_extrapolated_not_crashed(self):
        lookup = gw.strike_temperature(4.8, 78.0, 5.0)
        self.assertTrue(math.isfinite(lookup.strike_temp_c))
        self.assertEqual(lookup.row_source, "extrapolated")

    def test_rejects_a_ratio_outside_the_accepted_range(self):
        with self.assertRaises(gw.InvalidInput) as ctx:
            gw.strike_temperature(9.0, 66.0)
        self.assertIn("--ratio", str(ctx.exception))
        self.assertIn("1.50", str(ctx.exception))
        self.assertIn("5.00", str(ctx.exception))

    def test_rejects_a_target_and_a_grain_temperature_out_of_range(self):
        with self.assertRaises(gw.InvalidInput) as target_ctx:
            gw.strike_temperature(3.0, 95.0)
        self.assertIn("--target", str(target_ctx.exception))
        with self.assertRaises(gw.InvalidInput) as grain_ctx:
            gw.strike_temperature(3.0, 66.0, 90.0)
        self.assertIn("--grain-temp", str(grain_ctx.exception))


class MashScheduleTests(unittest.TestCase):
    """F2 - step validation and the running clock."""

    def setUp(self):
        self.steps = [
            gw.MashStep("Protein rest", 55.0, 15.0),
            gw.MashStep("Saccharification", 66.0, 60.0),
            gw.MashStep("Mash out", 76.0, 10.0),
        ]

    def test_schedule_orders_steps_and_totals_85_minutes(self):
        schedule = gw.build_schedule(self.steps, ratio_l_per_kg=3.0, grain_temp_c=20.0)
        self.assertEqual(schedule.total_minutes, 85.0)
        self.assertEqual([s.start_offset_min for s in schedule.steps], [0.0, 15.0, 75.0])
        self.assertAlmostEqual(schedule.steps[1].strike_temp_c, 72.4, delta=0.3)
        self.assertAlmostEqual(schedule.steps[2].end_offset_min, 85.0, places=6)

    def test_step_errors_name_the_offending_step(self):
        with self.assertRaises(gw.InvalidInput) as ctx:
            gw.build_schedule([gw.MashStep("Mash out", 90.0, 10.0)])
        self.assertIn("Mash out", str(ctx.exception))
        self.assertIn("30", str(ctx.exception))
        self.assertIn("80", str(ctx.exception))

    def test_zero_minute_step_is_rejected(self):
        with self.assertRaises(gw.InvalidInput) as ctx:
            gw.build_schedule([gw.MashStep("Saccharification", 66.0, 0.0)])
        self.assertIn("more than 0", str(ctx.exception))

    def test_build_schedule_needs_at_least_one_step(self):
        with self.assertRaises(gw.InvalidInput):
            gw.build_schedule([])

    def test_mmss_formats_a_running_clock(self):
        self.assertEqual(gw.mmss(0), "00:00")
        self.assertEqual(gw.mmss(59.4), "01:00")
        self.assertEqual(gw.mmss(85 * 60), "85:00")
        self.assertEqual(gw.mmss(-5), "00:00")


class TinsethTests(unittest.TestCase):
    """F3 - the bitterness maths."""

    def test_reference_addition_utilization_and_ibu(self):
        util_pct, ibu = gw.ibu_for_addition(30.0, 5.5, 60.0, 23.0, 1.050)
        self.assertGreater(util_pct, 20.0)
        self.assertLess(util_pct, 26.0)
        self.assertGreater(ibu, 15.0)
        self.assertLess(ibu, 18.0)

    def test_higher_boil_gravity_lowers_both_utilization_and_ibu(self):
        thin_util, thin_ibu = gw.ibu_for_addition(30.0, 5.5, 60.0, 23.0, 1.050)
        thick_util, thick_ibu = gw.ibu_for_addition(30.0, 5.5, 60.0, 23.0, 1.100)
        self.assertLess(thick_util, thin_util)
        self.assertLess(thick_ibu, thin_ibu)

    def test_zero_boil_minutes_is_zero_without_dividing_by_zero(self):
        util_pct, ibu = gw.ibu_for_addition(30.0, 5.5, 0.0, 23.0, 1.050)
        self.assertEqual(util_pct, 0.0)
        self.assertEqual(ibu, 0.0)
        self.assertTrue(math.isfinite(util_pct))

    def test_a_longer_boil_keeps_gaining_bitterness(self):
        _, short = gw.ibu_for_addition(30.0, 5.5, 20.0, 23.0, 1.050)
        _, long = gw.ibu_for_addition(30.0, 5.5, 90.0, 23.0, 1.050)
        self.assertGreater(long, short)

    def test_ibu_total_sums_and_shares_reach_100_percent(self):
        additions = [
            gw.HopAddition("Cascade", 5.5, 30.0, 60.0),
            gw.HopAddition("Saaz", 3.5, 20.0, 15.0),
        ]
        total = gw.build_ibu_total(additions, 23.0, 1.050)
        self.assertAlmostEqual(
            total.total_ibu, sum(a.ibu for a in total.additions), places=9
        )
        shares = gw.ibu_shares(total)
        self.assertEqual(len(shares), 2)
        self.assertAlmostEqual(sum(shares), 100.0, places=6)
        self.assertGreater(shares[0], shares[1])

    def test_short_boil_note_flag_tracks_additions_under_20_minutes(self):
        with_short = gw.build_ibu_total(
            [gw.HopAddition("Saaz", 3.5, 20.0, 15.0)], 23.0, 1.050
        )
        without = gw.build_ibu_total(
            [gw.HopAddition("Cascade", 5.5, 30.0, 60.0)], 23.0, 1.050
        )
        self.assertTrue(with_short.short_boil)
        self.assertFalse(without.short_boil)

    def test_rejects_volume_and_gravity_outside_their_ranges(self):
        additions = [gw.HopAddition("Cascade", 5.5, 30.0, 60.0)]
        with self.assertRaises(gw.InvalidInput) as volume_ctx:
            gw.build_ibu_total(additions, 400.0, 1.050)
        self.assertIn("--volume", str(volume_ctx.exception))
        with self.assertRaises(gw.InvalidInput) as gravity_ctx:
            gw.build_ibu_total(additions, 23.0, 1.4)
        self.assertIn("--gravity", str(gravity_ctx.exception))

    def test_shares_of_an_all_zero_boil_do_not_divide_by_zero(self):
        total = gw.build_ibu_total([gw.HopAddition("Cascade", 5.5, 30.0, 0.0)], 23.0, 1.050)
        self.assertEqual(gw.ibu_shares(total), [0.0])


class InputParsingTests(unittest.TestCase):
    """Every validation message names the flag and its accepted range."""

    def test_fermentable_string_must_have_three_fields(self):
        with self.assertRaises(gw.InvalidInput) as ctx:
            gw.parse_fermentable("Pilsner,5")
        self.assertIn("--add", str(ctx.exception))
        self.assertIn("name,kg,lovibond", str(ctx.exception))

    def test_fermentable_weight_must_be_positive_number(self):
        with self.assertRaises(gw.InvalidInput) as ctx:
            gw.parse_fermentable("Pilsner,five,1.8")
        self.assertIn("--add", str(ctx.exception))
        self.assertIn("weight", str(ctx.exception))
        self.assertIn("greater than 0", str(ctx.exception))

    def test_lovibond_range_is_enforced(self):
        with self.assertRaises(gw.InvalidInput) as ctx:
            gw.parse_fermentable("Pilsner,5,900")
        self.assertIn("0 and 600", str(ctx.exception))

    def test_step_and_hop_shapes_and_fields(self):
        with self.assertRaises(gw.InvalidInput) as step_ctx:
            gw.parse_step("Saccharification,66")
        self.assertIn("--step", str(step_ctx.exception))
        with self.assertRaises(gw.InvalidInput) as hop_ctx:
            gw.parse_hop("Cascade,5.5,30")
        self.assertIn("--hop", str(hop_ctx.exception))

    def test_hop_alpha_grams_and_minutes_ranges(self):
        with self.assertRaises(gw.InvalidInput) as alpha_ctx:
            gw.parse_hop("Cascade,55,30,60")
        self.assertIn("0 and 25", str(alpha_ctx.exception))
        with self.assertRaises(gw.InvalidInput) as grams_ctx:
            gw.parse_hop("Cascade,5.5,0,60")
        self.assertIn("greater than 0", str(grams_ctx.exception))
        with self.assertRaises(gw.InvalidInput) as minutes_ctx:
            gw.parse_hop("Cascade,5.5,30,900")
        self.assertIn("0 and 300", str(minutes_ctx.exception))

    def test_missing_volume_is_named_as_required(self):
        with self.assertRaises(gw.InvalidInput) as ctx:
            gw.parse_range_float("--volume", None, *gw.VOLUME_RANGE, "L")
        self.assertIn("--volume", str(ctx.exception))
        self.assertIn("required", str(ctx.exception))


class TerminalCapabilityTests(unittest.TestCase):
    """D8 - colour detection and the swatch fallback."""

    class _Stream:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    def test_no_color_and_dumb_terminals_disable_colour(self):
        tty = self._Stream(True)
        self.assertTrue(gw.colour_enabled(tty, {"TERM": "xterm-256color"}))
        self.assertFalse(
            gw.colour_enabled(tty, {"TERM": "xterm-256color", "NO_COLOR": "1"})
        )
        self.assertFalse(gw.colour_enabled(tty, {"TERM": "dumb"}))
        self.assertFalse(gw.colour_enabled(tty, {}))

    def test_piped_stream_disables_colour(self):
        self.assertFalse(gw.colour_enabled(self._Stream(False), {"TERM": "xterm-256color"}))

    def test_truecolour_needs_a_colour_terminal_marker(self):
        tty = self._Stream(True)
        env = {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
        self.assertTrue(gw.truecolour_enabled(tty, env))
        self.assertFalse(gw.truecolour_enabled(tty, {"TERM": "xterm-256color"}))
        self.assertFalse(gw.truecolour_enabled(self._Stream(False), env))

    def test_swatch_emits_a_truecolour_bar_and_reset(self):
        band = gw.render_swatch((217, 130, 17), width=12, truecolour=True)
        self.assertIn("\x1b[48;2;217;130;17m", band)
        self.assertTrue(band.endswith("\x1b[0m"))
        self.assertIn(" " * 12, band)

    def test_swatch_falls_back_to_the_256_colour_cube(self):
        band = gw.render_swatch((217, 130, 17), width=8, truecolour=False)
        self.assertIn("\x1b[48;5;", band)

    def test_ansi256_index_is_inside_the_palette(self):
        for rgb in ((0, 0, 0), (255, 255, 255), (217, 130, 17), (55, 12, 3)):
            with self.subTest(rgb=rgb):
                self.assertTrue(16 <= gw.rgb_to_ansi256(rgb) <= 255)

    def test_accent_marker_text_survives_without_colour(self):
        marker, escape = gw.accent_prefix(False, False)
        self.assertEqual(marker, ">")
        self.assertEqual(escape, "")
        marker, escape = gw.accent_prefix(True, True)
        self.assertEqual(marker, ">")
        self.assertTrue(escape.startswith("\x1b[38;2;"))

    def test_render_bill_without_colour_names_the_word_and_the_hex(self):
        b = bill(("Pilsner", 5.0, 1.8), ("Crystal 60", 0.5, 60.0))
        text = gw.render_bill(b, use_colour=False, truecolour=False)
        self.assertNotIn("\x1b", text)
        self.assertIn("amber", text)
        self.assertIn(b.srgb_hex, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class InProcessContractTests(unittest.TestCase):
    """The plan's in-process API contract, mapped to real callables."""

    EXPECTED_PATHS = (
        "/api/malt-bill",
        "/api/malt-bill/color",
        "/api/mash/strike-table",
        "/api/mash/schedule",
        "/api/mash/timer",
        "/api/hops/ibu",
        "/api/hops/ibu/total",
    )

    def test_every_documented_path_maps_to_a_callable_handler(self):
        self.assertEqual(tuple(gw.IN_PROCESS_CONTRACT), self.EXPECTED_PATHS)
        for path, handler in gw.IN_PROCESS_CONTRACT.items():
            with self.subTest(path=path):
                self.assertTrue(callable(handler))

    def test_no_socket_or_web_framework_is_imported(self):
        self.assertNotIn("socket", vars(gw))
        self.assertNotIn("http", vars(gw))
        self.assertNotIn("flask", vars(gw))

    def test_malt_bill_handler_validates_volume_and_needs_a_fermentable(self):
        with self.assertRaises(gw.InvalidInput) as empty_ctx:
            gw.build_bill(23.0, [])
        self.assertIn("at least one fermentable", str(empty_ctx.exception))
        with self.assertRaises(gw.InvalidInput) as volume_ctx:
            gw.build_bill(0.1, [gw.Fermentable("Pilsner", 5.0, 1.8)])
        self.assertIn("--volume", str(volume_ctx.exception))
        built = gw.build_bill(23.0, [gw.Fermentable("Pilsner", 5.0, 1.8)])
        self.assertEqual(built.batch_volume_l, 23.0)
        self.assertAlmostEqual(built.total_grist_kg, 5.0)

    def test_colour_handler_reports_the_hex_rgb_and_a_plain_terminal(self):
        report = gw.colour_report(
            bill(("Pilsner", 5.0, 1.8), ("Crystal 60", 0.5, 60.0)),
            stream=io.StringIO(),
            env={"TERM": "xterm-256color"},
        )
        self.assertEqual(report["colour_word"], "amber")
        self.assertEqual(report["srgb_hex"], "#E08E16")
        self.assertEqual(report["rgb"], [224, 142, 22])
        self.assertFalse(report["colour"])
        self.assertFalse(report["truecolour"])

    def test_colour_handler_reports_a_truecolour_terminal(self):
        class Tty:
            def isatty(self):
                return True

        report = gw.colour_report(
            bill(("Pilsner", 5.0, 1.8), ("Crystal 60", 0.5, 60.0)),
            stream=Tty(),
            env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        )
        self.assertTrue(report["colour"])
        self.assertTrue(report["truecolour"])

    def test_strike_and_ibu_handlers_are_the_same_functions_the_cli_uses(self):
        lookup = gw.IN_PROCESS_CONTRACT["/api/mash/strike-table"](3.0, 66.0, 20.0)
        self.assertAlmostEqual(lookup.strike_temp_c, 72.4, delta=0.3)
        util_pct, ibu = gw.IN_PROCESS_CONTRACT["/api/hops/ibu"](30.0, 5.5, 60.0, 23.0, 1.050)
        self.assertGreater(util_pct, 20.0)
        self.assertGreater(ibu, 15.0)
