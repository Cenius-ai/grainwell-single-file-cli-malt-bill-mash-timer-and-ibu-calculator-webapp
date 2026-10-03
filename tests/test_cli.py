"""End-to-end tests for the Grainwell CLI: views, exit codes, piping, countdown.

The live countdown is driven with an injected clock and sleep, so the schedule
logic is exercised without waiting for real minutes.
"""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grainwell as gw  # noqa: E402


def run_cli(argv):
    """Run the CLI in-process and return (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gw.main(argv)
    return code, out.getvalue(), err.getvalue()


def schedule(*steps, ratio=3.0):
    return gw.build_schedule(
        [gw.MashStep(name, target, minutes) for name, target, minutes in steps],
        ratio_l_per_kg=ratio,
    )


class FakeClock:
    """A deterministic monotonic clock: each read advances by ``step`` seconds."""

    def __init__(self, step=0.5):
        self.now = 0.0
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value

    def sleep(self, seconds):
        self.now += seconds


class RaisingClock:
    """A clock that raises KeyboardInterrupt after ``after`` reads."""

    def __init__(self, after):
        self.reads = 0
        self.after = after

    def __call__(self):
        self.reads += 1
        if self.reads >= self.after:
            raise KeyboardInterrupt
        return float(self.reads)


class ParserTreeTests(unittest.TestCase):
    """T1 - the argparse tree, help and exit codes."""

    def test_help_lists_every_command_and_exits_zero(self):
        code, out, _ = run_cli(["--help"])
        self.assertEqual(code, 0)
        for token in ("bill", "mash", "ibu", "--version"):
            self.assertIn(token, out)
        self.assertIn('--add "Pilsner,5,1.8"', out)

    def test_no_arguments_prints_usage_and_exits_two(self):
        code, out, err = run_cli([])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("usage:", err)
        self.assertEqual(out, "")

    def test_unknown_command_is_named_and_exits_two(self):
        code, _, err = run_cli(["banana"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("banana", err)

    def test_version_flag(self):
        code, out, _ = run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "grainwell {}".format(gw.__version__))

    def test_every_subcommand_has_its_own_help(self):
        for argv in (["bill", "--help"], ["mash", "--help"], ["mash", "strike", "--help"],
                     ["mash", "timer", "--help"], ["ibu", "--help"]):
            with self.subTest(argv=argv):
                code, out, _ = run_cli(argv)
                self.assertEqual(code, 0)
                self.assertIn("usage:", out)

    def test_mash_without_a_subcommand_exits_two(self):
        code, _, _ = run_cli(["mash"])
        self.assertEqual(code, gw.EXIT_INVALID)


class BillCommandTests(unittest.TestCase):
    """T3 - the malt bill view."""

    def test_bill_prints_the_table_totals_and_colour_word(self):
        code, out, _ = run_cli(
            ["bill", "--add", "Pilsner,5,1.8", "--add", "Crystal 60,0.5,60", "--volume", "23"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Pilsner", out)
        self.assertIn("Crystal 60", out)
        self.assertIn("90.9%", out)
        self.assertIn("9.1%", out)
        self.assertIn("5.50", out)
        self.assertIn("SRM", out)
        self.assertIn("EBC", out)
        self.assertIn("amber", out)

    def test_bill_names_a_bad_weight_and_exits_two(self):
        code, _, err = run_cli(["bill", "--add", "Pilsner,five,1.8", "--volume", "23"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--add", err)
        self.assertIn("greater than 0", err)

    def test_bill_names_a_bad_lovibond_and_exits_two(self):
        code, _, err = run_cli(["bill", "--add", "Pilsner,5,999", "--volume", "23"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("0 and 600", err)

    def test_bill_names_a_bad_volume_and_exits_two(self):
        code, _, err = run_cli(["bill", "--add", "Pilsner,5,1.8", "--volume", "900"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--volume", err)
        self.assertIn("0.5", err)
        self.assertIn("200", err)

    def test_bill_without_malts_invites_the_first_one(self):
        code, out, _ = run_cli(["bill"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 2)
        self.assertIn('--add "Pilsner,5,1.8"', out)

    def test_bill_json_is_machine_readable(self):
        code, out, _ = run_cli(
            ["bill", "--add", "Pilsner,5,1.8", "--add", "Crystal 60,0.5,60",
             "--volume", "23", "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["fermentables"][0]["share_pct"], 90.9)
        self.assertEqual(payload["colour_word"], "amber")
        self.assertTrue(payload["srgb_hex"].startswith("#"))

    def test_no_color_flag_keeps_the_output_plain(self):
        code, out, _ = run_cli(
            ["bill", "--add", "Pilsner,5,1.8", "--volume", "23", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", out)


class StrikeCommandTests(unittest.TestCase):
    """T5 - the strike temperature view."""

    def test_strike_prints_the_table_and_the_selected_row(self):
        code, out, _ = run_cli(
            ["mash", "strike", "--ratio", "3.0", "--target", "66", "--grain-temp", "20"]
        )
        self.assertEqual(code, 0)
        self.assertIn("ratio L/kg", out)
        self.assertIn("selected row 3.0 L/kg x 66.0 C", out)
        self.assertIn("strike water: 72.4 C", out)
        # the neighbouring table rows are on screen too
        for row in ("2.0", "2.5", "3.5", "4.0"):
            self.assertIn(row, out)

    def test_strike_bad_ratio_names_the_flag_and_range(self):
        code, _, err = run_cli(["mash", "strike", "--ratio", "9", "--target", "66"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--ratio", err)
        self.assertIn("1.50", err)
        self.assertIn("5.00", err)
        self.assertIn("L/kg", err)

    def test_strike_bad_target_names_the_flag_and_range(self):
        code, _, err = run_cli(["mash", "strike", "--ratio", "3.0", "--target", "95"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--target", err)
        self.assertIn("30", err)
        self.assertIn("80", err)

    def test_strike_bad_grain_temp_names_the_flag_and_range(self):
        code, _, err = run_cli(
            ["mash", "strike", "--ratio", "3.0", "--target", "66", "--grain-temp", "90"]
        )
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--grain-temp", err)
        self.assertIn("40", err)

    def test_bare_strike_prints_the_default_table_with_a_prompt(self):
        code, out, _ = run_cli(["mash", "strike"])
        self.assertEqual(code, 0)
        self.assertIn("grain 20.0 C", out)
        self.assertIn("mash strike --ratio 3.0 --target 66", out)

    def test_strike_json_reports_the_source_cell(self):
        code, out, _ = run_cli(
            ["mash", "strike", "--ratio", "3.0", "--target", "65", "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["source"], "tabulated")
        self.assertAlmostEqual(payload["strike_temp_c"], 71.3, delta=0.05)
        self.assertIn("3.0", payload["table"])


class TimerCommandTests(unittest.TestCase):
    """T6 - the mash schedule and --plan-only."""

    def test_plan_only_prints_the_schedule_and_returns_immediately(self):
        code, out, _ = run_cli(
            ["mash", "timer", "--step", "Protein rest,55,15",
             "--step", "Saccharification,66,60", "--step", "Mash out,76,10",
             "--ratio", "3.0", "--plan-only"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Protein rest", out)
        self.assertIn("Saccharification", out)
        self.assertIn("Mash out", out)
        self.assertIn("total mash time 85.00 min", out)
        self.assertIn("00:00-15:00", out)
        self.assertIn("15:00-75:00", out)
        self.assertIn("75:00-85:00", out)

    def test_out_of_range_step_target_is_named_and_exits_two(self):
        code, _, err = run_cli(["mash", "timer", "--step", "Mash out,90,10", "--plan-only"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("Mash out", err)
        self.assertIn("30", err)
        self.assertIn("80", err)

    def test_bad_step_shape_is_named_and_exits_two(self):
        code, _, err = run_cli(["mash", "timer", "--step", "Saccharification,66", "--plan-only"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--step", err)
        self.assertIn("name,target_C,minutes", err)

    def test_timer_without_steps_invites_one(self):
        code, out, _ = run_cli(["mash", "timer", "--ratio", "3.0"])
        self.assertEqual(code, 0)
        self.assertIn('--step "Saccharification,66,60"', out)

    def test_plan_only_json_lists_the_steps(self):
        code, out, _ = run_cli(
            ["mash", "timer", "--step", "Saccharification,66,60", "--plan-only", "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total_minutes"], 60.0)
        self.assertEqual(payload["steps"][0]["name"], "Saccharification")
        self.assertAlmostEqual(payload["steps"][0]["strike_temp_c"], 72.4, delta=0.3)

    def test_a_piped_run_never_blocks_on_the_countdown(self):
        code, _, err = run_cli(["mash", "timer", "--step", "Saccharification,66,60"])
        self.assertEqual(code, 0)
        self.assertIn("not a terminal", err)


class IbuCommandTests(unittest.TestCase):
    """T9 - the bitterness view."""

    def test_ibu_table_reports_per_hop_rows_and_a_total(self):
        code, out, _ = run_cli(
            ["ibu", "--hop", "Cascade,5.5,30,60", "--hop", "Saaz,3.5,20,15",
             "--volume", "23", "--gravity", "1.050"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Cascade", out)
        self.assertIn("Saaz", out)
        self.assertIn("23.1 %", out)
        self.assertIn("total kettle IBU", out)
        self.assertIn("82.6%", out)
        self.assertIn("17.4%", out)

    def test_short_boil_note_appears_but_still_exits_zero(self):
        code, out, _ = run_cli(
            ["ibu", "--hop", "Saaz,3.5,20,15", "--volume", "23", "--gravity", "1.050"]
        )
        self.assertEqual(code, 0)
        self.assertIn("under-estimates", out)

    def test_no_short_boil_note_for_a_single_60_minute_charge(self):
        _, out, _ = run_cli(
            ["ibu", "--hop", "Cascade,5.5,30,60", "--volume", "23", "--gravity", "1.050"]
        )
        self.assertNotIn("under-estimates", out)

    def test_out_of_range_alpha_is_named_and_exits_two(self):
        code, _, err = run_cli(
            ["ibu", "--hop", "Cascade,55,30,60", "--volume", "23", "--gravity", "1.050"]
        )
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--hop", err)
        self.assertIn("0 and 25", err)

    def test_bad_gravity_names_the_flag_and_range(self):
        code, _, err = run_cli(
            ["ibu", "--hop", "Cascade,5.5,30,60", "--volume", "23", "--gravity", "1.500"]
        )
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--gravity", err)
        self.assertIn("1.000", err)
        self.assertIn("1.150", err)

    def test_missing_volume_is_reported_rather_than_crashing(self):
        code, _, err = run_cli(["ibu", "--hop", "Cascade,5.5,30,60", "--gravity", "1.050"])
        self.assertEqual(code, gw.EXIT_INVALID)
        self.assertIn("--volume", err)

    def test_ibu_without_hops_invites_one(self):
        code, out, _ = run_cli(["ibu", "--volume", "23", "--gravity", "1.050"])
        self.assertEqual(code, 0)
        self.assertIn('--hop "Cascade,5.5,30,60"', out)

    def test_ibu_json_shares_sum_to_100(self):
        code, out, _ = run_cli(
            ["ibu", "--hop", "Cascade,5.5,30,60", "--hop", "Saaz,3.5,20,15",
             "--volume", "23", "--gravity", "1.050", "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertAlmostEqual(sum(a["share_pct"] for a in payload["additions"]), 100.0)
        self.assertTrue(payload["short_boil_note"])

    def test_zero_minute_dry_charge_is_accepted_as_zero(self):
        code, out, _ = run_cli(
            ["ibu", "--hop", "Cascade,5.5,30,0", "--volume", "23", "--gravity", "1.050"]
        )
        self.assertEqual(code, 0)
        self.assertIn("0.0", out)


class CountdownTests(unittest.TestCase):
    """T7 - the blocking countdown, its bells and its stop keys."""

    def test_countdown_rings_one_bell_per_step_and_completes(self):
        sched = schedule(("Saccharification", 66.0, 0.05), ("Mash out", 76.0, 0.05))
        out = io.StringIO()
        clock = FakeClock(step=0.5)
        status = gw.run_countdown(sched, out, clock=clock, sleep=clock.sleep, read_key=lambda: "")
        text = out.getvalue()
        self.assertEqual(status, "completed")
        self.assertEqual(text.count(gw.BELL), 2)
        self.assertIn("step 1/2", text)
        self.assertIn("step 2/2", text)
        self.assertIn("remaining", text)
        self.assertIn("mash complete", text)

    def test_first_bell_lands_on_the_first_step_boundary(self):
        sched = schedule(("Saccharification", 66.0, 0.05), ("Mash out", 76.0, 0.05))
        out = io.StringIO()
        clock = FakeClock(step=0.5)
        gw.run_countdown(sched, out, clock=clock, sleep=clock.sleep, read_key=lambda: "")
        text = out.getvalue()
        first_bell = text.index(gw.BELL)
        self.assertLess(text.index("step 1/2"), first_bell)
        self.assertLess(first_bell, text.index("step 2/2"))

    def test_q_stops_cleanly_on_a_fresh_line_without_a_bell(self):
        sched = schedule(("Saccharification", 66.0, 5.0))
        out = io.StringIO()
        clock = FakeClock(step=0.5)
        pressed = {"n": 0}

        def read_key():
            pressed["n"] += 1
            return "q" if pressed["n"] >= 2 else ""

        status = gw.run_countdown(sched, out, clock=clock, sleep=clock.sleep, read_key=read_key)
        text = out.getvalue()
        self.assertEqual(status, "stopped")
        self.assertEqual(text.count(gw.BELL), 0)
        self.assertIn("stopped on step 1/1", text)
        self.assertTrue(text.endswith("the mash clock is not running\n"))

    def test_ctrl_c_propagates_after_the_line_is_closed(self):
        sched = schedule(("Saccharification", 66.0, 30.0))
        out = io.StringIO()
        with self.assertRaises(KeyboardInterrupt):
            gw.run_countdown(
                sched, out, clock=RaisingClock(after=4), sleep=lambda _s: None, read_key=lambda: ""
            )
        self.assertTrue(out.getvalue().endswith("\n"))

    def test_cli_reports_130_when_the_countdown_is_interrupted(self):
        original_clock, original_sleep = gw.DEFAULT_CLOCK, gw.DEFAULT_SLEEP
        original_interactive = gw.timer_is_interactive
        gw.DEFAULT_CLOCK = RaisingClock(after=3)
        gw.DEFAULT_SLEEP = lambda _s: None
        gw.timer_is_interactive = lambda: True
        try:
            code, _, err = run_cli(
                ["mash", "timer", "--step", "Saccharification,66,30", "--ratio", "3.0"]
            )
        finally:
            gw.DEFAULT_CLOCK, gw.DEFAULT_SLEEP = original_clock, original_sleep
            gw.timer_is_interactive = original_interactive
        self.assertEqual(code, gw.EXIT_INTERRUPTED)
        self.assertIn("interrupted", err)

    def test_a_mixed_short_and_long_schedule_uses_the_running_clock(self):
        sched = schedule(("Protein rest", 55.0, 15.0), ("Saccharification", 66.0, 60.0))
        self.assertEqual(sched.total_minutes, 75.0)
        self.assertEqual(sched.steps[1].start_offset_min, 15.0)
        self.assertEqual(gw.mmss(sched.steps[1].end_offset_min * 60), "75:00")


class PipedOutputTests(unittest.TestCase):
    """T10 - piped output is plain ASCII carrying every number."""

    COMMANDS = (
        ["bill", "--add", "Pilsner,5,1.8", "--add", "Crystal 60,0.5,60", "--volume", "23"],
        ["mash", "strike", "--ratio", "3.0", "--target", "66", "--grain-temp", "20"],
        ["mash", "timer", "--step", "Saccharification,66,60", "--plan-only"],
        ["ibu", "--hop", "Cascade,5.5,30,60", "--volume", "23", "--gravity", "1.050"],
    )

    def test_no_escape_sequences_when_stdout_is_not_a_terminal(self):
        for argv in self.COMMANDS:
            with self.subTest(command=argv[0]):
                code, out, _ = run_cli(argv)
                self.assertEqual(code, 0)
                self.assertNotIn("\x1b", out)
                self.assertTrue(out.isprintable() or "\n" in out)

    def test_every_command_still_carries_its_numbers(self):
        codes = [run_cli(argv)[0] for argv in self.COMMANDS]
        self.assertEqual(codes, [0, 0, 0, 0])
        _, bill_out, _ = run_cli(self.COMMANDS[0])
        _, strike_out, _ = run_cli(self.COMMANDS[1])
        _, ibu_out, _ = run_cli(self.COMMANDS[3])
        self.assertIn("SRM   9.2", bill_out)
        self.assertIn("#E08E16", bill_out)
        self.assertIn("strike water: 72.4 C", strike_out)
        self.assertIn("total kettle IBU 16.5", ibu_out)


class ValidationSweepTests(unittest.TestCase):
    """T10 - all seven flags report their range and exit 2."""

    CASES = (
        (["bill", "--add", "Pilsner,five,1.8", "--volume", "23"], "--add"),
        (["bill", "--add", "Pilsner,5,1.8", "--volume", "900"], "--volume"),
        (["mash", "strike", "--ratio", "9", "--target", "66"], "--ratio"),
        (["mash", "strike", "--ratio", "3.0", "--target", "95"], "--target"),
        (["mash", "timer", "--step", "Mash out,90,10", "--plan-only"], "--step"),
        (["ibu", "--hop", "Cascade,55,30,60", "--volume", "23", "--gravity", "1.05"], "--hop"),
        (["ibu", "--hop", "Cascade,5.5,30,60", "--volume", "23", "--gravity", "1.5"], "--gravity"),
    )

    def test_each_bad_flag_names_itself_and_exits_two(self):
        for argv, flag in self.CASES:
            with self.subTest(flag=flag):
                code, out, err = run_cli(argv)
                self.assertEqual(code, gw.EXIT_INVALID)
                self.assertIn(flag, err)
                self.assertEqual(out, "")

    def test_error_messages_carry_a_range_or_an_example(self):
        for argv, _flag in self.CASES:
            with self.subTest(argv=argv):
                _, _, err = run_cli(argv)
                self.assertTrue(
                    "must be" in err or "expected" in err or "greater than" in err,
                    "unhelpful message: {!r}".format(err),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
