# faktura-hent

Power Automate cloud flow that saves invoice PDFs from the shared mailbox
`faktura@alcora.dk` to OneDrive `/Fakturaer/yyyy-MM-dd/`. Everything lives in
`power-automate/`; the current package is `Gem-nye-fakturaer-solution-v5.zip`
(built by `build_shared_solution.py`, guide in `SHARED-MAILBOX-SETUP.md`).

## Facts
- Monitored folders: Inbox and the archive folder named exactly `Arkiv_10_2024_>`.
- `faktura@alcora.dk` is a licensed account with its own login (MFA via Authenticator),
  not only a shared mailbox. The live flow's OneDrive connection is signed in as it, so
  the PDFs land in faktura@alcora.dk's own OneDrive and are shared from there to
  colleagues (e.g. cl@alcora.dk).
- Suppliers often name attachments after the due date, not the invoice number
  (e.g. `inco Aarhus A_S_29.12.2026.pdf`, `Royal Unibrew A_S_30.11.2026.pdf`), so
  different invoices share filenames. The flow keeps original names and adds a
  short random suffix only when a different PDF already has that name. Identical
  PDFs are skipped by byte comparison.

- Microsoft Graph's "next page" link (`@odata.nextLink`) is not spelled like the URL
  the flow requested. 1.0.0.6 only followed links starting with its own mailbox URL, so
  from the day the archive held more than 100 attachment mails since the start time,
  every run failed with `MessageScanFailed` (run history 11–12 Nov 2026). Since 1.0.0.7
  the flow keeps its own path and reuses only the link's query string. Keep the test
  stand-in for Graph handing out links in a different spelling than the request.
- A run-history CSV export shows no inputs/outputs for steps inside loops, only the
  status and time of their last execution.
- Every run re-lists all attachment mails since the start time stored in
  `_automation-state.json` (28 Oct 2026 on the live flow), so runs get slower as mail
  accumulates. Not yet addressed.

- Importing a solution update puts the flow's OneDrive steps back on the solution's own
  connection reference `fkh_faktura_onedrive`. In the org environment that reference is
  bound to another account's OneDrive, not faktura@alcora.dk's (the account name is not
  visible from Lucas's login; the flow's primary owner is Camilla, cl@alcora.dk). After
  the 1.0.0.7 import on 12 Nov 2026 every run said "Succeeded" and saved the PDFs into
  that other OneDrive's `/Fakturaer`, so faktura@'s folder stopped at `2026-11-06`
  (noticed 17 Nov). After every import: open a OneDrive step (e.g. `Read saved state`)
  and make sure its connection is signed in as faktura@alcora.dk before trusting the runs.
  Quick check in any run, step `Load start time`: `2026-10-28T13:38:35Z` is faktura@'s
  OneDrive, `2026-10-28T13:11:46Z` is the other one (each OneDrive has its own
  `_automation-state.json`).
- "Succeeded" runs prove nothing about where the PDFs went. To see it, open a run that
  handled a mail and read the `Create file in OneDrive` output (`Path`), or compare the
  start time as above.
- Seen 16-17 Nov 2026: about three runs a day fail after 2-6.5 hours with
  `Read_all_message_pages` "until action is terminated ... Number of actual iterations: 1",
  so one step inside the first mailbox page hangs for hours (the loop's 1 hour timeout is
  only checked between rounds). Runs are one at a time, so nothing is fetched meanwhile.
  Cause not found yet; the per-run re-listing below is the first suspect.

- The v5 package has its own solution identity (`FakturaHentSharedMailbox`, publisher
  `FakturaHent`, prefix `fkh`, own workflow ID and connection references). The older
  `InvoiceAutomation` solution exists in the org environment owned by Lucas; reusing
  its identities makes other users' imports fail with a WriteAccess error on
  connectionreference.

## Quality gates (run from `power-automate/`)
```powershell
py -m unittest discover -v
py validate_package.py
py build_shared_solution.py
```
These do not replace a Microsoft import and a live mail test.

## Releases
The public repo `lucas-gundehug/faktura-hent` ships the import ZIP as a GitHub release
asset, tagged with the solution version (e.g. `v1.0.0.6`). After changing the flow:
bump `VERSION` in `build_shared_solution.py`, run the quality gates, then
`gh release create v<version> power-automate/Gem-nye-fakturaer-solution-v5.zip`.
The repo is public: never commit `.env` files, credentials or real invoices.
