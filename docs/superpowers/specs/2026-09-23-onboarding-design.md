# SponsorScan onboarding redesign

Date: 2026-09-23
Status: sections 1 and 2 implemented; sections 3 and 4 outstanding

## Problem

A new user needs eight steps before seeing a single job, and four of them are
slow, manual, or both:

1. Create a virtual environment and install dependencies.
2. Navigate to the DOL site, identify the correct quarterly file among several,
   and download 100-400 MB by hand.
3. `load-lca` (minutes).
4. `discover` (thousands of HTTP probes; can run for hours).
5. `fetch-jobs`.
6. `report`.
7. Copy an example profile and hand-edit roughly 25 fields, including skill
   weights on an unexplained 1-10 scale.
8. Optionally: a Google service account, a Gmail app password, a copied
   workflow file, and five repository secrets.

Supporting documentation runs to roughly 1,700 lines: a 456-line `README.md`
that mixes quickstart, full flag reference, and caveats, plus four setup guides
averaging about 325 lines each.

The pipeline's characteristic failure is silence. Every stage is long-running
and cached, and a misconfiguration surfaces as an empty CSV rather than an
error. A user cannot tell which of the eight steps went wrong.

Two concrete defects make this worse:

- `docs/OPT_SETUP.md` lines 17 and 24 instruct the user to copy
  `profiles/opt_profile.example.json`. The file is named
  `profiles/opt_profiles.example.json`. The first command in the guide fails.
- `README.md` presents `discover` as mandatory step 2, though `companies.yaml`
  already ships 19 verified boards. Users spend hours on a step they
  could defer.

A third defect, missing `requirements-dev.txt` and `requirements-sheets.txt`,
was fixed separately in commit `e8d38f8`.

## Goals

- Reduce time-to-first-result for a new user.
- Make failures legible: every empty result should be traceable to a named
  cause with a suggested command.
- Keep SponsorScan a local tool the user runs themselves. No hosted service, no
  fork-and-configure model.

## Non-goals

- Rewriting the scoring, filtering, or matching logic.
- Adding ATS providers beyond Greenhouse, Lever, and Ashby.
- Writing the Google Sheets uploader. It remains specified rather than shipped.
- Changing the profile JSON format. Existing profiles must continue to load
  unchanged.

## Section 1: documentation fixes

No code changes. These ship first because they are independently verifiable and
help every user immediately.

### 1.1 Rename the OPT example profile

Rename `profiles/opt_profiles.example.json` to
`profiles/opt_profile.example.json`.

Renaming the file is preferred over editing the two documentation references,
because the singular form matches both the existing
`profiles/citizen_profile.example.json` and every instruction already published.

The `.gitignore` rules at lines 38-39 (`profiles/*.json` with
`!profiles/*.example.json`) continue to apply unchanged.

Verify afterwards that no reference to the plural filename remains anywhere in
the repository.

### 1.2 Document that `discover` is optional

Add a note to the `discover` section of `README.md` stating that
`companies.yaml` ships with 19 confirmed boards, that a first run can
proceed directly to `fetch-jobs`, and that `discover` exists to widen coverage
later.

### 1.3 Split the README

`README.md` keeps only:

- what the tool does (the existing opening);
- install;
- the four pipeline steps as a minimal path, with the note from 1.2;
- a pointer to `docs/REFERENCE.md` and the setup guides.

Target length is roughly 120-140 lines, down from 456. An earlier estimate of
60-80 did not account for the install section, which needs both platforms and
the PowerShell execution-policy steps and does not compress below about 40
lines without hiding something a first-time user needs.

A new `docs/REFERENCE.md` receives the full flag tables, the scoring table, the
personalized reporting section, the work-authorization discussion, the optional
Sheets and email sections, the GitHub Actions section, and the caveats.

No prose is deleted in this step; it is moved.

## Section 2: the `doctor` command

`python sponsorscan.py doctor` inspects the environment and reports what is
wrong and what to run next.

It deliberately does not reproduce the per-stage drop counts already printed by
`sponsor_daily_report.py` lines 1014-1026. That funnel is good and stays where
it is. `doctor` covers what happens before it: the stages that currently fail
without saying anything.

### 2.1 Checks

Each check returns a status of `OK`, `WARN`, or `FAIL`, a message, and an
optional remedy line.

| # | Check | FAIL condition | Remedy shown |
|---|---|---|---|
| 1 | Required modules importable | `requests` or `yaml` missing | `pip install -r requirements.txt` |
| 2 | Database present | `DB_PATH` does not exist | `python sponsorscan.py load-lca <file>` |
| 3 | LCA data loaded | `employers` table empty or absent | `python sponsorscan.py load-lca <file> --replace` |
| 4 | Jobs fetched | `jobs` table empty or absent | `python sponsorscan.py fetch-jobs --replace` |
| 5 | `companies.yaml` usable | missing, unparseable, or zero boards | `python sponsorscan.py discover` |
| 6 | Profile valid | `load_profile` raises `ProfileError` | the exception text, verbatim |

Warnings, which never fail the run:

| Check | WARN condition | Message |
|---|---|---|
| Optional modules | `openpyxl` or `rapidfuzz` missing | names each and what degrades. `rapidfuzz` is guarded by `HAVE_RAPIDFUZZ` and `openpyxl` is imported lazily for `.xlsx` only, so neither is fatal |
| Job freshness | newest `jobs.posted` older than 7 days | postings are stale; re-run `fetch-jobs` |
| Score threshold | `minimum_score` above 130 | few jobs reach this; most land between 95 and 120 |
| Empty targeting | `target_roles` or `skills` empty | every posting will be treated the same way |
| Authorization mismatch | `authorization_warnings(profile)` returns entries | each warning, verbatim |
| Notification config | `notifications.email_enabled` or `google_sheets_enabled` true but required environment variables unset | names the missing variables |

`DB_PATH` is read the same way `sponsorscan.py` line 43 reads it, honoring the
`SPONSORSCAN_DB` environment variable.

Checks 2 through 4 are ordered so the first failure is the earliest broken
stage. Later checks still run and report, so one invocation shows the whole
picture.

### 2.2 Arguments

- `--profile PATH`: profile to validate. When omitted, checks 6 and the
  profile-dependent warnings are skipped and reported as such, rather than
  failing.
- `--db PATH`: overrides `DB_PATH`.

### 2.3 Output

```
$ python sponsorscan.py doctor --profile profiles/my_profile.json

  OK    Dependencies installed
  OK    Database found (sponsorscan.db, 14.2 MB)
  OK    LCA data loaded (48,201 employers)
  FAIL  No jobs fetched
        -> python sponsorscan.py fetch-jobs --replace
  OK    companies.yaml valid (19 boards across 3 providers)
  OK    Profile valid (my_profile, opt)
  WARN  minimum_score is 140; most matches score 95-120

1 failed, 1 warning
```

Exit code is 1 if any check fails, otherwise 0, so the command is usable as a
CI preflight step.

## Section 3: the `setup` command

`python sponsorscan.py setup` asks a short series of questions and writes a
valid profile.

### 3.1 Questions

| Order | Question | Field | Notes |
|---|---|---|---|
| 1 | Your name | `name` | free text |
| 2 | Short id for filenames | `profile_id` | defaults to a slug of the name |
| 3 | Work authorization | `work_authorization` | numbered menu of the five values in `VALID_WORK_AUTHORIZATION` |
| 4 | Target roles | `target_roles` | comma-separated; a numbered menu of the six defaults is offered |
| 5 | Skills, then a rating each | `skills` | see 3.2 |
| 6 | Preferred locations | `preferred_locations` | comma-separated; blank means anywhere in the US |
| 7 | Maximum years of experience | `max_required_experience` | integer, defaults to 1 |
| 8 | Report window in hours | `report_hours` | defaults to 48 |

`minimum_score` is not asked. It defaults to 95, and `doctor` warns when a
hand-edited value is set too high.

`output_files` are derived from `profile_id`:
`<id>_matches_48h.csv`, `<id>_new_jobs_48h.csv`, `.sponsorscan_<id>_state.json`.

`notifications` defaults to both `false`.

### 3.2 Skill ratings

The wizard asks for a rating using three labels rather than a 1-10 number:

```
Which skills are on your resume? (comma-separated)
> Python, SQL, React

How would you rate Python?
  1) Strong - a core skill
  2) Comfortable
  3) Familiar
> 1
```

The labels map to the weights already used by the shipped examples:

| Label | Weight |
|---|---|
| Strong | 7 |
| Comfortable | 5 |
| Familiar | 3 |

Rationale: `score_skills` (line 556) sums matched weights and caps the total at
`MAX_SKILL_POINTS = 42`. Against a default `minimum_score` of 95, the gap
between a 6 and a 7 is roughly one point, which is noise. Self-assessment
inflation is the real risk: a user who rates every listed skill 8 or above
reaches the 42-point cap after five matches, at which point every posting
scores identically and ranking stops discriminating. Three labels force the
relative spread the scoring depends on.

The written file still stores plain integers, so the format is unchanged and
any value remains editable by hand afterwards.

### 3.3 Writing the file

- Default destination is `profiles/<profile_id>.json`, which `.gitignore`
  already excludes.
- If the file exists, the wizard asks before overwriting and offers a different
  name.
- The assembled profile is passed through `validate_profile` before writing. A
  validation failure is a bug in the wizard, not user error, and is reported as
  such.
- On success the wizard prints the exact next command, including the profile
  path.
- Every question accepts a blank response to take the default. `Ctrl+C` exits
  without writing.

## Section 4: `load-lca --latest`

`load-lca` already accepts an `https://` URL and streams it to disk
(`sponsorscan.py` lines 171-183). This section adds only link discovery.

`--latest` fetches the DOL performance-data page, finds candidate links to LCA
disclosure files, selects the most recent by fiscal year and quarter, prints
the resolved URL and asks for confirmation, then hands the URL to the existing
download path.

`--latest` and a positional path are mutually exclusive.

When no candidate link is found, the command exits with a message naming the
page to visit and the manual command to run, rather than a traceback. This is
the expected failure mode when the DOL changes its page, and the manual path
remains fully supported and documented.

## Section 5: code structure and testing

A new module, `onboarding.py`, holds the wizard and the checks.
`sponsorscan.py` gains two thin subcommands, `setup` and `doctor`, that parse
arguments and delegate.

`sponsorscan.py` is already 847 lines across four commands. Keeping the new
code separate avoids growing it further and lets the checks be tested without
the CLI.

Reused rather than reimplemented:

- `profile_loader.load_profile` and `validate_profile` for profile checks;
- `authorization_warnings` from `sponsor_daily_report.py` for the mismatch
  warning;
- `VALID_WORK_AUTHORIZATION` for the authorization menu;
- `DEFAULT_PROFILE` for every field the wizard does not ask about.

### Structure

Each check is a function taking an already-open connection and an optional
profile dictionary, returning a result object. No check opens a database, reads
an environment variable, or prints. A separate runner collects results and
formats them. This keeps checks testable against an in-memory SQLite database
with no network and no filesystem.

The wizard separates prompting from assembly. A pure function builds a profile
dictionary from collected answers, so the assembly and the label-to-weight
mapping are testable without simulating input.

### Tests

`tests/test_onboarding.py` covers:

- each check against both a passing and a failing fixture;
- exit code 1 when any check fails, 0 otherwise;
- the three skill labels mapping to 7, 5, and 3;
- profile assembly producing a dictionary that `validate_profile` accepts;
- `profile_id` slugging, including names with spaces and punctuation;
- derived `output_files` names;
- `--latest` link selection against a saved copy of the DOL page markup, and the
  clean exit when no link matches.

The existing suite is 116 tests and must continue to pass.

## Build order

1. Section 1, documentation. Independently verifiable, helps immediately.
2. Section 2, `doctor`. Explains failures, and is useful even to a user who
   configured everything by hand.
3. Section 3, `setup`. Builds on the validation `doctor` already exercises.
4. Section 4, `--latest`. Most external dependency, least certain lifespan.

Each step is a separate commit on a branch off `main`, and each leaves the
repository in a working state.
