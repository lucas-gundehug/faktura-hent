# Faktura-hent

Automatically saves supplier invoices and credit notes (PDFs) from the shared mailbox
**faktura@alcora.dk** into OneDrive, sorted into one folder per day.

It runs in Microsoft Power Automate (in the cloud), so nothing is installed on your PC
and it keeps working when your PC is off.

## Download

Get **`Gem-nye-fakturaer-solution-v5.zip`** from the
[latest release](https://github.com/lucas-gundehug/faktura-hent/releases/latest).
Do not unzip it; Power Automate imports the ZIP as it is.

## What it does

- Every 15 minutes it looks in **Indbakke** and **Arkiv_10_2024_>** in
  faktura@alcora.dk.
- It picks up PDF attachments from inco and Royal Unibrew:
  - filenames that start with `inco` or `Kreditnota_`
  - filenames that contain `Royal Unibrew`
  - `Faktura_<number>.pdf` when the mail subject contains "fra inco"
- It saves them in OneDrive under `Fakturaer/<date the mail arrived>/`,
  e.g. `Fakturaer/2026-10-28/Faktura_9583195.pdf`.
- Files keep their original name. inco and Royal Unibrew often name files after the
  due date, so two different invoices can have the same name. The second one then gets
  a short code added (e.g. `inco Aarhus A_S_29.12.2026_3f9a1c2b.pdf`), so nothing is
  overwritten.
- The exact same PDF is only saved once, even if it arrives twice or the mail is
  moved to the archive.
- It only reads mail. It never moves, deletes or marks mail as read.
- It only handles mail that arrives **after** it is switched on. Older mail is not
  fetched.

## Setting it up

You need: a Microsoft 365 account with **full access** (not only "send as") to
faktura@alcora.dk, and OneDrive for Business.

1. In your OneDrive, create a folder named **`Fakturaer`**.
2. Go to [make.powerautomate.com](https://make.powerautomate.com) → **Solutions** →
   **Import solution**, and choose the downloaded ZIP.
3. When asked for connections, pick (or sign in to create) your **Office 365 Outlook**
   and **OneDrive for Business** connections. Leave the flow switched off for now.
4. Open **Faktura-hent (shared mailbox)** → **Gem fakturaer fra faktura@alcora.dk**, check that
   both connections show a green tick, then **Save** and **Turn on**.
5. Click **Test → Manually** once. The run should succeed and create
   `Fakturaer/_automation-state.json`. Do not delete this file: it remembers which
   mails have been handled.
6. Send a test mail to faktura@alcora.dk with a harmless PDF named e.g.
   `inco TEST.pdf`. Within 15 minutes it should appear in today's folder in OneDrive.

Only **one** person should have the flow switched on, otherwise PDFs are saved twice.
Import it **once** per Power Automate environment. The copy belongs to whoever imported
it, so if someone else imports the same release afterwards, they get a permission error
("does not have WriteAccess … Forbindelsesreference"). Updates must then be imported
by the owner. An older **Invoice PDF automation** solution is a separate, older flow
and is not changed by this import.
Files are saved in the OneDrive of the account the **OneDrive connection** is signed in
with (shown under **Connections** on the flow's details page), not in Outlook. If that
is faktura@alcora.dk, sign in to OneDrive as faktura@alcora.dk and **share** the
`Fakturaer` folder with colleagues; they can then use **Add shortcut to My files**.

Detailed guide (Danish): [power-automate/SHARED-MAILBOX-SETUP.md](power-automate/SHARED-MAILBOX-SETUP.md).

## If something goes wrong

Open the flow in Power Automate and look at **Run history**. A failed run shows a
message in plain words:

| Message | What to do |
| --- | --- |
| `ArchiveFolderNotUnique` | The folder `Arkiv_10_2024_>` was not found (or exists twice). Check the exact name in Outlook. |
| `StateReadFailed` | The OneDrive connection cannot read `Fakturaer`. Re-check the connection. |
| Error on reading mail | The account lacks full access to faktura@alcora.dk. |

Failed mails are retried automatically on the next run.

## For developers

Source, build scripts and tests are in [power-automate/](power-automate/README.md).
