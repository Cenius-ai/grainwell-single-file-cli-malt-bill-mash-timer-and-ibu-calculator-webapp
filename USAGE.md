# Using Grainwell

Four views, three calculators. Every state each one can be in is listed below
with the command that reaches it. Output shown here is real output, piped (so no
colour) unless the swatch is mentioned.

Ranges you can rely on: `--volume` 0.5-200 L, lovibond 0-600, malt weight > 0 kg,
`--ratio` 1.5-5.0 L/kg, mash target 30-80 °C, grain temperature 0-40 °C, step
hold > 0 and ≤ 600 min, hop alpha 0-25 %, hop weight > 0 g, boil time 0-300 min,
boil gravity 1.000-1.150. Out-of-range input names the flag, states the range and
exits 2.

---

## 1. `grainwell bill` - malt bill and colour preview

```
$ python3 grainwell.py bill --add "Pilsner,5,1.8" --add "Munich,1.5,9" --add "Crystal 60,0.5,60" --volume 23

Malt bill - 23.0 L batch
  Fermentable       kg   lovibond   % bill
------------------------------------------
  Pilsner        5.000        1.8    71.4%
  Munich         1.500        9.0    21.4%
  Crystal 60     0.500       60.0     7.2%
------------------------------------------
  Total grist     7.00

> SRM  11.3   EBC  22.2   deep amber
  sRGB #D0740F
```

Each `--add` is `"name,kg,lovibond"` and repeats as many times as your grist
needs (up to 50 comfortably). The colour is Morey: malt colour units are
`Σ(lb × lovibond) ÷ batch gallons`, computed from the metric inputs you typed,
then `SRM = 1.4922 × MCU^0.6859`. EBC is `SRM × 1.97`.

* **Colour swatch.** On a truecolour terminal a full-width band in the beer's
  colour is printed above the table, and the sRGB hex sits under the numbers.
  `COLORTERM=truecolor` (or `24bit`) selects 24-bit; any other colour terminal
  gets the nearest xterm-256 colour; `NO_COLOR=1`, `TERM=dumb` or a piped stdout
  drops the band and keeps the word descriptor plus the hex. Piped output is
  pure ASCII - no escape sequences ever.
* **Dark beers stay brown.** The sRGB approximation is anchored so a 40+ SRM
  stout prints a very dark brown (red channel below 130, R > G > B), not a
  saturated red.
* **Empty state.** `python3 grainwell.py bill` with no `--add` prints two lines
  inviting the first malt with the worked example `--add "Pilsner,5,1.8"`.
* **Errors.** `--add "Pilsner,five,1.8"` → `invalid --add "Pilsner,five,1.8":
  weight "five" must be a number of kg greater than 0 ...`, exit 2.
  `--volume 900` → names `--volume` and the 0.5-200 L range, exit 2.
  Leaving `--volume` off entirely says it is required and exits 2.
* **Empty batch, no crash.** There is no loading state: the maths is local and
  instant, so nothing ever shows a spinner.

`--json` prints the same numbers as an object (`fermentables` with `share_pct`,
`mcu`, `srm`, `ebc`, `colour_word`, `srgb_hex`).

---

## 2. `grainwell mash strike` - strike temperature from the table

```
$ python3 grainwell.py mash strike --ratio 3.0 --target 66 --grain-temp 12

Strike water temperature  (grain 12.0 C, ratio 3.00 L/kg, target 66.0 C)
  ratio L/kg |    55     60     65     70     75   C
  ----------------------------------------------
         2.0 |  62.3   68.3   74.4   80.4   86.5
         2.5 |  60.8   66.7   72.5   78.3   84.2
>        3.0 |  59.9   65.6   71.3   77.0   82.6
         3.5 |  59.2   64.8   70.4   76.0   81.6
         4.0 |  58.7   64.2   69.7   75.2   80.7

  selected row 3.0 L/kg x 66.0 C -> 73.6 C  (interpolated between table cells)
  grain correction: grain at 12.0 C vs the 20 C table reference, +1.1 C applied
> strike water: 73.6 C
```

The grid is the in-code table: ratios 2.0-4.0 L/kg against targets 55-75 °C, for
a 20 °C grain bed in a pre-heated tun. The `>` marks the row in use (with the
accent colour on a colour terminal - the `>` itself carries the meaning).

* **Selection is traceable.** The line under the grid names the row used, the
  target, the number of degrees applied for a grain bed that is not 20 °C, and
  whether the value came from a cell, between cells (`interpolated`) or outside
  the printed grid (`extrapolated`).
* **Physics.** `strike = 0.4172 / ratio × (target − 20) + target`, where 0.4172
  is malt's specific heat expressed per litre-per-kilogram. The tabulated cells
  are literals; a test asserts each one matches that formula.
* **Colder grain = hotter water.** At 12 °C grain the strike rises by `0.4172 ÷
  3.0 × 8 = 1.1 °C`; at 4.5 L/kg the correction is smaller for the same grain.
* **Empty state.** `python3 grainwell.py mash strike` prints the default
  3.0 L/kg × 66 °C lookup plus one line inviting your own ratio and target.
* **Errors.** `--ratio 9` names `--ratio` and the 1.5-5.0 L/kg range, exit 2;
  `--target 95` names `--target` and 30-80 °C; `--grain-temp 90` names
  `--grain-temp` and 0-40 °C.

`--json` returns the selected temperature, the row used, the source label and the
whole table.

---

## 3. `grainwell mash timer` - schedule, then the live countdown

```
$ python3 grainwell.py mash timer --step "Protein rest,55,15" --step "Saccharification,66,60" --step "Mash out,76,10" --ratio 3.0 --plan-only

Mash schedule - 3.00 L/kg, grain 20.0 C
  Step                target      hold       strike  clock (mm:ss)
------------------------------------------------------------------
  1. Protein rest        55.0 C  15.00 m      59.9 C    00:00-15:00
  2. Saccharification    66.0 C  60.00 m      72.4 C    15:00-75:00
  3. Mash out            76.0 C  10.00 m      83.7 C    75:00-85:00
------------------------------------------------------------------
  total mash time 85.00 min across 3 steps
```

Each `--step` is `"name,target_C,minutes"`. Steps print in the order you typed
them with their own strike temperature (looked up for that step's target) and
their slot on the running clock; `--plan-only` prints this and returns to the
shell immediately.

Without `--plan-only`, on an interactive terminal, the same schedule is followed
by the countdown: one line is redrawn every second - step number, name, target
and `mm:ss` remaining - and the terminal bell rings at every step boundary and
again when the mash completes, so you can hear the schedule from across the
kitchen. Each redraw is derived from `time.monotonic()`, so it stays within a
tick of the wall clock even if a tick is late.

* **`q` stops it cleanly.** The clock stops, `stopped on step n/N` prints on a
  fresh line and the exit code is 0.
* **Ctrl-C exits 130** with the terminal restored and the cursor on a fresh line
  - no stray escape codes left behind.
* **Piped runs never block.** If stdout or stdin is not a terminal the schedule
  prints, a note goes to stderr and the process exits 0; a multi-minute silent
  hang in a pipeline is never what you wanted.
* **Empty state.** `python3 grainwell.py mash timer --ratio 3.0` prints two lines
  inviting a step with the example `--step "Saccharification,66,60"`.
* **Errors.** `--step "Mash out,90,10"` names that step and the 30-80 °C target
  range; a malformed step string names `--step` and the expected shape; a hold of
  0 minutes is rejected; all exit 2. `--ratio` and `--grain-temp` are checked the
  same way as in `mash strike`.

`--json` (with `--plan-only`) returns the ordered steps, their strike
temperatures, start offsets and the total mash minutes.

---

## 4. `grainwell ibu` - hop bitterness (Tinseth)

```
$ python3 grainwell.py ibu --hop "Cascade,5.5,30,60" --hop "Saaz,3.5,20,15" --volume 23 --gravity 1.050

Hop bitterness (Tinseth) - 23.0 L at OG 1.050
  Hop        alpha    grams   minutes    util      IBU  % of IBU
----------------------------------------------------------------
  Cascade    5.5 %   30.0 g      60 m   23.1 %     16.5    82.6%
  Saaz       3.5 %   20.0 g      15 m   11.4 %      3.5    17.4%
----------------------------------------------------------------
> total kettle IBU 20.0
  shares are each charge's percentage of that total; Tinseth is one model, not a lab measurement.
  note: Tinseth under-estimates very late additions - a charge boiled under 20 min reads lower here than it tastes.
```

Each `--hop` is `"variety,alpha%,grams,minutes"` - alpha acid straight off the
packet, no hop database involved. Per charge you get utilization and IBU; below
the table the kettle total and each charge's share of it (shares sum to exactly
100.0 %, rounded with the largest-remainder method so they never add up to
99.9 %).

The model, in the metric form of Tinseth:

```
bigness      = 1.65 × 0.000125^(boil gravity − 1)
boil factor  = (1 − e^(−0.04 × minutes)) ÷ 4.15
utilization  = bigness × boil factor
IBU          = grams × alpha% × utilization × 1000 ÷ litres
```

* **Short boils.** Any charge under 20 minutes adds the one-line caution, since
  Tinseth has no term for the very-late additions that taste stronger than it
  predicts. The run still exits 0.
* **Zero-minute charges** are legal and read exactly 0.0 utilization and 0.0 IBU
  - no division by zero, no negative number.
* **Higher gravity means less bitterness** from the same charge; a 1.100 boil
  shows a lower utilization and a lower IBU than 1.050.
* **Empty state.** `python3 grainwell.py ibu --volume 23 --gravity 1.050` prints
  two lines inviting a charge with the example `--hop "Cascade,5.5,30,60"`.
* **Errors.** `--hop "Cascade,55,30,60"` names the `--hop` value and the 0-25 %
  alpha range; `--gravity 1.5` names `--gravity` and 1.000-1.150; a missing
  `--volume` says it is required. All exit 2.

`--json` returns per-addition utilization, IBU and share plus the total and the
short-boil flag.

---

## Scripting it

Every view is also machine-readable, and diagnostics only ever go to stderr, so
pipes stay clean:

```bash
python3 grainwell.py ibu --hop "Cascade,5.5,30,60" --volume 23 --gravity 1.050 --json \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['total_ibu'])"
```

Empty input keeps the exit code honest: a hint for a missing `--add`/`--step`/
`--hop` is a successful run (0), while a value out of range is invalid input (2).
Ctrl-C during the countdown is 130.
