# Installing Grainwell

This is the full, step-by-step setup. Grainwell is one file of standard-library
Python, so "installing" it means placing the checkout somewhere and running it.

## Prerequisites (listed, never installed by our scripts)

| Requirement | Why | How to check |
| --- | --- | --- |
| CPython 3.9 or newer | the only runtime Grainwell has | `python3 --version` |

That is the complete list. Grainwell needs no compilers, no `-dev` system
headers, no database server, no package index and no environment variables.
There is **one** package manager in play - `pip`, Python's own - and it is only
used for the optional console script described in step 1b. The committed
`requirements.txt` declares **no** third-party runtime dependencies, so nothing
in this project is ever fetched from a registry at run time.

## Step 1 - set up the project dependencies

Two equivalent options; pick one.

**1a. No install at all (recommended, works offline).**

```bash
cd grainwell
python3 grainwell.py --help
```

**1b. Run the setup script** (idempotent; installs no system packages):

```bash
bash install.sh
```

It upgrades `pip`/`setuptools`/`wheel`, honours `requirements.txt` (empty by
design), and - only if a package index is reachable - installs the optional
`grainwell` console script with `pip install -e .`. It always exits; it never
starts a server, because Grainwell has none. If the index is unreachable it
prints a note and the tool still runs as `python3 grainwell.py`.

Because the scripts must be invoked through their interpreter (packaging can
lose the executable bit), always call them as `bash install.sh` / `sh demo.sh`.

## Step 2 - database / migration / seed

There is none, and this is deliberate rather than an omission:

* **No database.** Every command computes its result from the flags you pass and
  prints it; Grainwell stores nothing between runs.
* **No migration step.** No schema exists.
* **No seed step.** There is no stored data to seed - the only "data" in the
  product is the in-code strike-temperature table, which is part of the source.
* **No configuration file.** Grainwell reads no `.env`; the display variables in
  `.env.example` are ordinary shell environment variables, and all are optional.

So after step 1 the tool is fully operational. Nothing to migrate, nothing to
seed, nothing to unlock.

## Step 3 - run it

```bash
python3 grainwell.py bill --add "Pilsner,5,1.8" --add "Crystal 60,0.5,60" --volume 23
python3 grainwell.py mash strike --ratio 3.0 --target 66 --grain-temp 12
python3 grainwell.py mash timer  --step "Saccharification,66,60" --plan-only
python3 grainwell.py ibu --hop "Cascade,5.5,30,60" --volume 23 --gravity 1.050
```

Or, if you installed the console script in step 1b:

```bash
grainwell ibu --hop "Cascade,5.5,30,60" --volume 23 --gravity 1.050
```

The live countdown (`mash timer` without `--plan-only`) needs an interactive
terminal; it puts stdin in cbreak mode so a single `q` stops it, and restores
the terminal on exit. Piped or redirected, it prints the plan and a note instead
of hijacking the pipe.

## Step 4 - verify the install

```bash
sh demo.sh                       # the three calculators, non-interactively
python3 -m pytest                # 94 tests (or: python3 -m unittest discover -s tests)
```

Both must finish green; the demo never prompts and never touches the network.

## Uninstalling

If you used option 1b, remove the console script with `pip uninstall grainwell`,
then delete the checkout. Option 1a leaves nothing behind at all: no packages,
no cache, no files written outside the directory you unpacked.
