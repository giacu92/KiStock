# InvenTree BoM Checker for KiCad

A native KiCad PCB Editor plugin that checks whether the components on your board are available in InvenTree, including approved equivalent parts. All interface text, reports and command-line messages are in English.

## Use inside KiCad

The plugin is installed for KiCad 10 in your user plugin folder. Restart the **PCB Editor**, open a board, then choose **Tools > External Plugins > InvenTree BoM Checker**, or click its circuit/checkmark toolbar icon.

1. Enter the InvenTree server URL and API token. Replace the example server URL with your own InvenTree server address. The token is masked. Enable **Remember API token** and click **Save settings** to retain it across KiCad restarts using Windows account encryption. Uncheck the option and save to forget it.
2. Choose the number of boards to assemble. Enable **Use related parts as approved equivalents** if your InvenTree related-part links represent valid substitutions.
3. Click **Check availability**. Results appear directly in the KiCad plugin window.
4. For an unmapped row, select it and click **Associate selected row**, or double-click it. Search the inventory, check the candidate's package and ratings, then confirm the exact part.
5. Run the check again. Associations are saved locally and reused for rows with the same value and footprint that have no explicit IPN/MPN.
6. Optionally use **Export HTML + CSV** to save a report, or **Import CSV / iBOM / XML** to check an existing export instead of the current board.

The board snapshot comes from the currently open PCB, including unsaved component changes. DNP and excluded-from-BOM footprints are skipped. Fields such as IPN and MPN must be present on the PCB footprints; update the PCB from the schematic if those fields only exist in the schematic. The plugin does not modify either design or inventory. Reopen the plugin after changing the board.

## Install or distribute

The self-contained package is `dist/inventree-bom-checker-0.3.0.zip`. On another installation, open KiCad's **Plugin and Content Manager**, choose **Install from File**, select the ZIP and apply the installation. Restart the PCB Editor afterward.

Alternatively, on Windows run:

```powershell
.\install_plugin.ps1 -KiCadVersion 10.0
```

The installer writes only this plugin's folder under `%APPDATA%\kicad\10.0\scripting\plugins\inventree_bom_checker`. It does not modify KiCad's program files. Use one installation method to avoid duplicate menu entries. The local package is not published to the official plugin repository.

This is a **KiCad 9/10 PCB Action Plugin**, using KiCad's bundled Python and wxPython with no extra pip dependencies. It was tested with **KiCad 10.0.5**. This SWIG package is not compatible with KiCad 11. KiCad 10 does not support arbitrary Python plugins in the Schematic Editor; its external BOM generator remains available as described below. The official [KiCad Action Plugin documentation](https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/index.html) describes this interface and its deprecation; [IPC documentation](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/) describes the newer interface.

## Identity, stock and equivalents

Matching order:

1. Exact internal part number in `InvenTree IPN` or `IPN`.
2. Exact manufacturer part number in `MPN` or `Part Num`.
3. A manually confirmed value + footprint association, stored by InvenTree part ID. Parts without an IPN are supported.

Explicit design fields take precedence over local associations. Correct an incorrect or ambiguous IPN/MPN in the design rather than assigning a value/footprint override. Multiple exact matches produce `AMBIGUOUS`. Values such as `*`, `~` and `-` are not identifiers. Generic values like `10k` never establish identity automatically.

The plugin searches InvenTree for suggestions, but a suggestion is not counted as available until you confirm the association. Verify package, tolerance, voltage, power and other relevant specifications yourself.

Availability uses `unallocated_stock` from `/api/part/{id}/requirements/`. It excludes already allocated stock according to InvenTree and never substitutes supplier availability or stock on order. Inactive, virtual and template parts are excluded. Stock is counted across the entire inventory, not one storage location.

When enabled, **direct related parts** are treated as user-approved substitutes. Indirect relationships are not followed. Disable this option if your related-part links also contain accessories or other non-interchangeable items.

A maximum-flow calculation distributes stock over the whole BOM without counting a component twice. Fractional stock is rounded down to whole components. The algorithm maximizes covered component quantities; it does not guarantee a minimum number of substitutions or a particular priority among BOM rows. The proposed pick shows the chosen parts and quantities. Initial per-part stock totals are shared across rows and should not be added together.

Statuses: `OK`, `OK_EQUIVALENT`, `PARTIAL`, `SHORTAGE`, `UNMAPPED`, `NOT_FOUND`, `AMBIGUOUS`. Rows initially show `NOT_CHECKED` until a check completes.

Availability is a snapshot, not a stock reservation. All REST operations are GET requests. Network operations run in a background thread so KiCad stays responsive.

## Local settings and tokens

Plugin settings and associations are saved outside the installed package:

- Windows: `%APPDATA%\inventree-bom-checker\settings.json`
- macOS: `~/Library/Application Support/inventree-bom-checker/settings.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/inventree-bom-checker/settings.json`

The server URL, equivalent-parts option, mappings and remember-token preference are saved in settings.json. Remembered tokens are encrypted with Windows DPAPI in credentials.json in the same directory, bound to the current Windows account and server URL. Raw tokens are never written into settings, projects or packages. Persistent credentials currently require Windows; other systems support session-only tokens. Changing the server clears associations for the new check, since part IDs belong to a particular InvenTree server. Back up the settings file before switching between inventories if you want to retain the previous mappings.

Use an InvenTree account with read access to parts, manufacturer parts and stock. Enter its API token in the plugin window, or supply the `INVENTREE_TOKEN` environment variable before launching KiCad. Do not put the token in a BOM field or project configuration.

The earlier command-line tool uses its own `config.json` and optional mapping file; plugin settings do not overwrite them.

## Supported exported BOM formats

- **CSV:** comma, semicolon or tab separators. Supported headers include `Qty`/`Quantity`, `Parts`/`References`/`Reference`, `Value`, `Package`/`Footprint`, and optional IPN/MPN fields. Zero quantities and rows with an active DNP field are skipped. The first row must be the header of a single table. Reference ranges are not expanded: the declared quantity is authoritative.
- **InteractiveHtmlBom HTML:** reads JSON or LZ-String-compressed `pcbdata`, without executing embedded JavaScript. Uses the `both` groups or `F` + `B`, deduplicating footprint IDs. The original iBOM file is preserved; inventory reports are separate files.
- **KiCad XML:** accepts the intermediate netlist XML used by external BOM generators and skips DNP, excluded-from-BOM and virtual `#` references.





## Standalone command-line use

Requires Python 3.10+ and no external packages. Copy `config.example.json` to `config.json`, then set your token for the current shell:

```powershell
$secureToken = Read-Host 'InvenTree API token' -AsSecureString
$env:INVENTREE_TOKEN = [System.Net.NetworkCredential]::new('', $secureToken).Password
python .\bom_checker.py 'C:\path\bom.csv' '.\reports\board' --boards 5 --open
Remove-Item Env:\INVENTREE_TOKEN
```

You can also double-click `start_bom_checker.cmd` or drag a BOM onto it. This opens a file picker and prompts for a masked token, then opens the report in a browser.

Exit codes: 0 = report generated, including reports with shortages; 1 = processing/API error; 2 = invalid arguments. An old report retains its old timestamp if a new run fails.

Generate a mapping file offline:

```powershell
python .\bom_checker.py 'C:\path\ibom.html' board.local.json --prepare-mapping
```

Fill in each IPN, then add `"mapping_file": "board.local.json"` to `config.json`. A mapping can instead use `"part_id": 17` for an explicit InvenTree part ID. Mapping files are relative to the configuration file. Blank mappings are ignored; duplicate value/footprint keys are rejected.



Each report also writes a `.mapping.json` containing rows that remain unmapped. Complete it and link it in the CLI configuration to reuse those associations.

## Schematic external BOM generator

In KiCad, choose **Tools > Generate Legacy Bill of Materials**, add `bom_checker.py` and set its command to:

```text
python "C:\path\KiStock\bom_checker.py" "%I" "%O-stock" --config "C:\path\KiStock\config.json" --open
```

KiCad must inherit `INVENTREE_TOKEN` from its environment. Launch it from the PowerShell session where you set the token, or set the environment variable for your user and restart KiCad. Do not include the token in the generator command. See the [official BOM generator documentation](https://docs.kicad.org/9.0/en/eeschema/eeschema.html#_legacy_bom_generation).

## Build and verify

```powershell
python -m unittest discover -s tests -v
python .\tools\build_plugin.py
& 'C:\Program Files\KiCad\10.0\bin\python.exe' .\tools\smoke_kicad.py
python .\examples\demo.py
```

The automated suite covers matching, shared-stock distribution, equivalents, parsing, API pagination, settings, token exclusion and packaging. The smoke test uses real pcbnew objects and hidden wxPython dialogs to exercise PCB snapshots, exclusions, stock results and inventory search. Its native registration hook is mocked because registration requires the running PCB Editor; menu interaction inside a live editor is not automated by this test.

The demo uses simulated inventory and writes `reports/demo-stock.html`. Neither local settings, API tokens, BOMs nor reports are included in the distributable ZIP.

Authenticated read-only API access was verified on 24 October 2026 against your InvenTree 1.5.4 server (API 530): 179 active parts, stock requirements and related-part queries were accessible. Official API references: [parts and availability](https://docs.inventree.org/en/stable/api/schema/part/) and [manufacturer part numbers](https://docs.inventree.org/en/stable/api/schema/company/).

