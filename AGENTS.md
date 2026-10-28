# faktura-hent

Power Automate cloud flow that saves invoice PDFs from the shared mailbox
`faktura@alcora.dk` to OneDrive `/Fakturaer/yyyy-MM-dd/`. Everything lives in
`power-automate/`; the current package is `Gem-nye-fakturaer-solution-v5.zip`
(built by `build_shared_solution.py`, guide in `SHARED-MAILBOX-SETUP.md`).

## Facts
- Monitored folders: Inbox and the archive folder named exactly `Arkiv_10_2024_>`.
- Suppliers often name attachments after the due date, not the invoice number
  (e.g. `inco Aarhus A_S_29.12.2026.pdf`, `Royal Unibrew A_S_30.11.2026.pdf`), so
  different invoices share filenames. The flow keeps original names and adds a
  short random suffix only when a different PDF already has that name. Identical
  PDFs are skipped by byte comparison.

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
