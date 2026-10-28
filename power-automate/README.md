# Technical notes

Source and build scripts for the Power Automate flow. For setup, see the
[main README](../README.md) and [SHARED-MAILBOX-SETUP.md](SHARED-MAILBOX-SETUP.md).

## Rebuild and verify

Run from this folder (Python 3):

```powershell
py -m unittest discover -v
py validate_package.py
py build_shared_solution.py
```

`build_shared_solution.py` writes `Gem-nye-fakturaer-solution-v5.zip`; the flow
logic itself lives in `build_shared_flow.py`.

`build-input/` holds what the v5 build depends on: the Microsoft-generated solution
container (keeps the flow ID, publisher and connection references) and the legacy
package checked by `validate_package.py`. Do not import those files.

Local checks interpret the flow with synthetic connector responses. They do not
replace Microsoft's import validation or a live mail/OneDrive test.
