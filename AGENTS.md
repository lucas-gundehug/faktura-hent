# faktura-hent

Power Automate cloud flow that saves invoice PDFs from the shared mailbox
`faktura@alcora.dk` to OneDrive `/Fakturaer/yyyy-MM-dd/`. Everything lives in
`power-automate/`; the current package is `Gem-nye-fakturaer-solution-v5.zip`
(built by `build_shared_solution.py`, guide in `SHARED-MAILBOX-SETUP.md`).

## Facts
- Monitored folders: Inbox and the archive folder named exactly `Arkiv_10_2024_>`.
- `faktura@alcora.dk` is a licensed account with its own login (MFA via Authenticator),
  not only a shared mailbox.
- The PDFs belong in Camilla's (cl@alcora.dk) OneDrive, folder `/Fakturaer` (Lucas,
  17 Nov 2026). That is where the flow saved until 6 Nov 2026. An earlier note here said
  faktura@alcora.dk's own OneDrive; that described the solution's connection reference,
  not where the working flow saved.
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
  `_automation-state.json` (28 Oct 2026 on the live flow), and the processed-mail list
  in that file grows without bound. Both are still open. Since 1.0.0.8 the cost of the
  re-listing is per page of 500 mails, not per mail.

- Importing a solution update puts the flow's OneDrive steps back on the solution's own
  connection reference `fkh_faktura_onedrive`. In the org environment that reference is
  bound to faktura@alcora.dk's own OneDrive, not cl@alcora.dk's. After the 1.0.0.7 import
  on 12 Nov 2026 every run said "Succeeded" and saved the PDFs into faktura@'s
  `/Fakturaer`, so cl@'s folder stopped at `2026-11-06` (noticed 17 Nov). After every
  import the OneDrive steps must be put back on a connection signed in as cl@alcora.dk.
  Only Camilla's login can pick her connection; Lucas's login is offered his own
  OneDrive connections only.
- "Change connection reference" in the designer changes that one step only. The flow has
  eight OneDrive steps: `Read saved state`, `Create state file`, `Get state metadata`,
  `Get destination metadata`, `List destination children`, `Read existing PDF`,
  `Create file in OneDrive`, `Persist processed mail`. On 17 Nov only `Read saved state`
  was switched: the run read cl@'s bookkeeping, compared against faktura@'s files, found
  every PDF "already saved", wrote nothing and still ended "Succeeded" (201 mails). It
  also overwrote the bookkeeping file in faktura@'s OneDrive with cl@'s start time, so
  the start time no longer tells the two OneDrives apart. Later on 17 Nov 2026 all eight
  steps were switched to the cl@ connection (the reference shows as "OneDrive for
  Business"), and the 10:08 run that day saved `/Fakturaer/2026-11-16/Kreditnota_K-9077289.pdf`
  into cl@'s OneDrive. Importing 1.0.0.8 or any later package puts the steps back on
  `fkh_faktura_onedrive` (faktura@'s OneDrive), and the switch has to be redone.
- To see which OneDrive a step used, open it in a run and read `LastModifiedBy` in its
  output: `Studenterbolaget - Faktura` is faktura@'s OneDrive. Check `Get state metadata`
  and a `Create file in OneDrive` that actually ran.
- "Succeeded" runs prove nothing about where the PDFs went, or that any were saved.
- Seen 16-17 Nov 2026: about three runs a day fail after 2-6.5 hours with
  `Read_all_message_pages` "until action is terminated because the specified limit was
  reached ... Specified timeout: 01:00:00 ... Number of actual iterations: 1". In such a
  run every step inside that one loop round finished within 41 seconds of the run start
  (`Get_message_page` 0.1 s, `Collect_each_mail` 5 s, `Next_message_page` 0 s); the loop
  then sat idle for hours before the platform evaluated it again, by which time its
  1 hour timeout had passed. None of the flow's own steps is slow: the platform delays the
  flow. Likely (not proven) cause: Power Platform action request limits. The flow runs
  96 times a day and each idle run executed about two actions per attachment mail since
  28 Oct 2026 (~900-1,000 actions, ~90,000 a day and growing), plus about three per
  existing file in the destination when there was new mail. 1.0.0.8 replaces the
  per-mail loop (`Collect_each_mail`) and the per-file loop (`Index_each_child`) with
  whole-page Filter array + Compose + Set variable steps, so a run's action count grows
  only per page of 500 mails and per folder (tests count actions in `LocalRuntime`).
  1.0.0.8 was imported on 17 Nov 2026 (signed in as cl@, flow turned off first). The
  import warned that labels for language 1033 could not be imported and that flows may
  not have turned on; both are harmless. As predicted it put OneDrive back on faktura@,
  so all eight steps were switched to cl@ again before turning the flow on. The first
  run took 12 seconds instead of 2-4 minutes, `Get state metadata` showed
  `LastModifiedBy: Camilla Hansen`, and `Unseen mails on page` received the inbox's 13
  mails and returned none (all handled). Not yet seen on 1.0.0.8: a run that saves a new
  PDF, a mail folder with more than one page, and whether the hours-long hangs are gone.

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
