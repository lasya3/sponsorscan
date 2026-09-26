# Contributing to sponsorscan

Thanks for helping out. Bug reports, new company boards, and fixes are all
welcome.

## Quick ways to help

- **Add a company.** Open an issue with the "Add a company" template, or send a
  PR that edits `companies.yaml`. Verify the slug first using the URL patterns
  at the top of that file.
- **Report a bug.** Use the bug report template and include the output of
  `python sponsorscan.py doctor`.
- **Suggest a feature.** Open a feature request describing the problem before
  writing a large change, so we can agree on the approach.

## Development setup

```bash
git clone https://github.com/pruhnav/sponsorscan.git
cd sponsorscan
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Run the tests before opening a PR:

```bash
python -m pytest
python selftest.py
```

## Pull requests

- Keep each PR focused on one change.
- Add or update tests in `tests/` for behavior changes.
- Update the README or `docs/` when you change commands, flags, or setup steps.
- Use short, imperative commit messages with a type prefix, for example
  `fix: handle empty Lever boards` or `feat: add Workable support`.

## Never commit personal data

sponsorscan works with personal details such as your profile, visa status, and
API credentials. `.gitignore` already excludes profiles, state files, `.env`,
and credential files. Double check `git status` before committing, and only
commit the `*.example.json` profiles.

## Code of Conduct

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
