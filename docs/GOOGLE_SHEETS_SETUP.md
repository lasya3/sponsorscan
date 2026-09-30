# Google Sheets Setup

This guide explains how to synchronize SponsorScan report CSV files with a
Google Sheets spreadsheet.

The Google Sheets integration is optional. It should run after the personalized
report generates the all-matches and new-matches CSV files.

## 1. Requirements

You need:

- a Google Cloud project;
- the Google Sheets API enabled;
- a Google service account;
- a service-account JSON key;
- a Google spreadsheet shared with the service-account email;
- the client libraries: `pip install -r requirements-sheets.txt`;
- generated report CSV files.

The uploader ships as `scripts/update_google_sheet.py`. Its `--check` flag tests
every step below without writing to the sheet, so run it whenever you finish a
step and are unsure it worked.

Do not commit the service-account JSON file or its contents.

## 2. Create a Google Cloud project

Open the Google Cloud Console and create a new project for SponsorScan.

Use a descriptive name such as:

```text
SponsorScan Automation
```

Select that project before continuing.

## 3. Enable the Google Sheets API

In the Google Cloud project:

```text
APIs & Services
  -> Library
  -> Google Sheets API
  -> Enable
```

The uploader only needs the Sheets API unless you later add separate Google
Drive functionality.

## 4. Create a service account

In the Google Cloud project:

```text
IAM & Admin
  -> Service Accounts
  -> Create Service Account
```

Use a descriptive name such as:

```text
sponsorscan-sheet-bot
```

After creating the service account, create a JSON key:

```text
Service Account
  -> Keys
  -> Add Key
  -> Create new key
  -> JSON
```

Download the JSON file and store it securely.

The service-account email will look similar to:

```text
sponsorscan-sheet-bot@your-project-id.iam.gserviceaccount.com
```

## 5. Create and share the spreadsheet

Create a Google spreadsheet for SponsorScan.

Copy the spreadsheet ID from its URL.

For a URL like:

```text
https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit
```

the spreadsheet ID is:

```text
1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890
```

Share the spreadsheet with the service-account email and grant it Editor access.
Viewer access is not enough. Untick "Notify people", because the address has no
inbox.

The service account cannot update the sheet until it has access.

The uploader also accepts the full spreadsheet URL in place of the ID.

## 6. Add GitHub Actions secrets

In the repository, open:

```text
Settings
  -> Secrets and variables
  -> Actions
  -> New repository secret
```

Create:

| Secret | Purpose |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full contents of the downloaded JSON key |
| `GOOGLE_SPREADSHEET_ID` | Destination spreadsheet ID |

Paste the entire JSON object into `GOOGLE_SERVICE_ACCOUNT_JSON`.

Do not upload the JSON key file into the repository.

## 7. What the uploader does

`scripts/update_google_sheet.py`:

1. reads the all-matches and new-jobs CSV files;
2. authenticates with the service-account key;
3. creates the worksheet tabs if they do not exist, and enlarges them when a
   report outgrows them;
4. clears each tab and writes the CSV header and rows;
5. freezes the header row;
6. exits with the step that fixes it when something is misconfigured.

The default tab names are:

```text
All Matches 48h
New Jobs 48h
```

Set `ALL_MATCHES_SHEET` and `NEW_JOBS_SHEET` to use others.

Both tabs are replaced on every run, so anything typed into them is lost. Keep
notes, such as which jobs you applied to, in a tab of your own.

Numbers are written as numbers so the score columns sort correctly. Every other
value is written as plain text, so a job title that begins with `=` is never
run as a formula.

## 8. Add the workflow step

`workflows/sponsorscan.example.yml` already includes this step. It skips itself
until both secrets exist:

```yaml
- name: Update Google Sheet
  if: env.SHEETS_ENABLED == 'true'
  env:
    GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
    GOOGLE_SPREADSHEET_ID: ${{ secrets.GOOGLE_SPREADSHEET_ID }}
  run: |
    pip install -r requirements-sheets.txt
    python scripts/update_google_sheet.py
```

A step's `if:` cannot read `secrets` directly, so the job computes
`SHEETS_ENABLED` in its `env:` block. See the example workflow.

Keep the filenames consistent with the active profile.

For example, a citizen profile may use:

```text
citizen_matches_48h.csv
citizen_new_jobs_48h.csv
```

An OPT profile may use:

```text
opt_matches_48h.csv
opt_new_jobs_48h.csv
```

Set `ALL_MATCHES_CSV` and `NEW_JOBS_CSV` accordingly.

## 9. Install the client libraries

They are kept out of `requirements.txt` so a plain install stays small:

```powershell
pip install -r requirements-sheets.txt
```

## 10. Test locally

Save the downloaded key in the repository root as `service-account.json`.
`.gitignore` already excludes that name. `GOOGLE_SERVICE_ACCOUNT_JSON` accepts
either the key's contents or a path to it.

In PowerShell:

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON = "service-account.json"
$env:GOOGLE_SPREADSHEET_ID = "your-spreadsheet-id-or-url"

python .\scripts\update_google_sheet.py --check
```

`--check` prints the service-account email, confirms the key, the API, the
spreadsheet ID and Editor access, and writes nothing. When it passes, point it
at your report files and run it for real:

```powershell
$env:ALL_MATCHES_CSV = "matches_48h.csv"
$env:NEW_JOBS_CSV = "new_jobs_48h.csv"

python .\scripts\update_google_sheet.py
```

A personalized report names its files after the profile, for example
`casey_matches_48h.csv`; the profile's `output_files` lists them.

These values apply only to the current PowerShell session.

On macOS or Linux:

```bash
export GOOGLE_SERVICE_ACCOUNT_JSON="service-account.json"
export GOOGLE_SPREADSHEET_ID="your-spreadsheet-id-or-url"
python scripts/update_google_sheet.py --check

export ALL_MATCHES_CSV="matches_48h.csv"
export NEW_JOBS_CSV="new_jobs_48h.csv"
python scripts/update_google_sheet.py
```

Do not print the service-account JSON in logs.

## 11. Test through GitHub Actions

Open the repository's Actions tab and manually run the SponsorScan workflow.

Verify:

- the report step succeeds;
- the Google Sheets step is green;
- the expected worksheet tabs exist;
- headers appear in row 1;
- old contents are replaced;
- the all-matches tab contains the full report;
- the new-jobs tab contains only newly discovered jobs.

## 12. Multiple profiles

Each profile can write to:

- a separate spreadsheet;
- separate worksheet tabs in one spreadsheet;
- or separate CSV-specific tabs.

For a shared spreadsheet, use distinct tab names such as:

```text
Pranav - All Matches
Pranav - New Jobs
Citizen - All Matches
Citizen - New Jobs
```

Do not let one profile overwrite another profile's worksheet tabs.

## Troubleshooting

Run `python scripts/update_google_sheet.py --check` first. Its error message
names the cause and the fix for each case below.

### Permission denied

Confirm that the spreadsheet is shared with the exact service-account email and
that the service account has Editor access.

### The spreadsheet ID is invalid

Copy only the value between `/d/` and `/edit` in the spreadsheet URL.

### The workflow reports invalid JSON

Confirm that `GOOGLE_SERVICE_ACCOUNT_JSON` contains the complete JSON object.

Do not paste only part of the file.

### The script cannot find a CSV

Confirm the report step runs before the Google Sheets step and that the
environment-variable filenames match the generated files.

For debugging:

```yaml
- name: List generated files
  run: ls -la
```

### The Sheets API is not enabled

The API was enabled in a different Google Cloud project from the one that owns
the service account. The error message links to the enable page for the right
project.

### The key is not a service-account key

An OAuth client ID file (it starts with `{"installed":` or `{"web":`) looks
similar but does not work. Create the key from the service account's Keys tab.

### The service-account email is being used for Gmail

The Google Sheets service account is only for API access. It is not the Gmail
account used to send SponsorScan notifications.

Use a real Gmail account and Gmail App Password for email alerts.

## Security

Never commit:

- service-account JSON keys;
- the raw `GOOGLE_SERVICE_ACCOUNT_JSON` value;
- spreadsheet IDs for private spreadsheets when avoidable;
- `.env` files containing credentials;
- generated reports containing personal data.

Use GitHub Actions secrets or local environment variables for all sensitive
values.
