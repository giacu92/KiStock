# InvenTree BoM Checker for KiCad

A native KiCad PCB Editor plugin that checks whether the components on your board are available in InvenTree, including approved equivalent parts. All interface text, reports and command-line messages are in English.

## Use inside KiCad

The plugin is installed for KiCad 10 in your user plugin folder. Restart the **PCB Editor**, open a board, then choose **Tools > External Plugins > InvenTree BoM Checker**, or click its circuit/checkmark toolbar icon.

1. Enter the InvenTree server URL and API token. Replace the example server URL with your own InvenTree server address. The token is masked. Enable **Remember API token** and click **Save settings** to retain it across KiCad restarts using Windows account encryption. Uncheck the option and save to forget it.
2. Choose the number of boards to assemble. Enable **Use related parts as approved equivalents** if your InvenTree related-part links represent valid substitutions.
3. Click **Check availability**. Results appear directly in the KiCad plugin window.
4. For an unmapped row, select it and click **Associate selected row**, or double-click it. Search the inventory, compare the **Available (unallocated)** column, check the package and ratings, then confirm the exact part. Failed quantity lookups are shown as Unknown, never zero.
5. Availability refreshes automatically after **Use selected part**, and the selected row is populated. Shared stock is recalculated across the whole BOM. Associations are saved locally and reused for rows with the same value and footprint that have no explicit IPN/MPN.
6. Optionally use **Export HTML + CSV** to save a report, or **Import CSV / iBOM / XML** to check an existing export instead of the current board.

The board snapshot comes from the currently open PCB, including unsaved component changes. DNP and excluded-from-BOM footprints are skipped. Fields such as IPN and MPN must be present on the PCB footprints; update the PCB from the schematic if those fields only exist in the schematic. The plugin never modifies the KiCad design. Inventory changes are limited to the explicit build-order workflow described below. Reopen the plugin after changing the board.

## Install or distribute

The self-contained package is `dist/inventree-bom-checker-0.4.0.zip`. On another installation, open KiCad's **Plugin and Content Manager**, choose **Install from File**, select the ZIP and apply the installation. Restart the PCB Editor afterward.

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

Statuses: `OK`, `OK_EQUIVALENT`, `PARTIAL`, `SHORTAGE`, `UNMAPPED`, `NOT_FOUND`, `AMBIGUOUS`, `EXCLUDED_AI`. Rows initially show `NOT_CHECKED` until a check completes.

Availability is a snapshot, not a stock reservation. Availability checks and searches use GET requests. Only the explicit build-order creation workflow writes BOM, build and allocation records. Network operations run in a background thread so KiCad stays responsive.

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

## AI assistance

Click **AI settings** to configure an OpenAI-compatible API base URL, model name and optional API key. Include the API prefix in the base URL, for example `http://localhost:1234/v1`. The plugin appends `/chat/completions`. There is no default endpoint or model, and local servers may leave the key empty. Enable JSON mode only if supported by your server. An optional remembered AI key uses the same Windows account encryption as the InvenTree token.

Click **AI Assist: filter and match** to analyze the BOM against the active inventory catalog. Default system instructions tell the model to:

- Exclude non-purchasable artwork, logos, fiducials and bare mounting holes; retain purchasable hardware, spacers, connectors and heatsinks.
- Recognize values such as `4k7 = 4.7k = 4700 ohm`, `0.1uF = 100nF`, and `4n7 = 4.7nF`. Normalized values and total required quantities are also supplied as data.
- Verify package dimensions, pin count, pitch, pinout, mounting type and imperial/metric package notation, plus voltage, power, dielectric, polarity and tolerance where relevant.
- Prefer sufficient unallocated stock among technically suitable candidates. Never pick a stocked item merely because its nominal value matches.
- Leave uncertain, unknown-stock or exhausted candidates unresolved instead of inventing compatibility. Code also converts a proposed match with unknown or zero verified stock into an unresolved decision.
- Preserve existing explicit IPN/MPN fields and confirmed associations.

The review dialog shows the proposed action, part, available quantity, confidence and explanation. Match/exclusion proposals with model confidence of at least 90% start checked; review and change those selections before clicking **Apply checked proposals**. A model confidence value is not proof of compatibility. Only catalog part IDs can be selected, invalid/missing/duplicate decisions are rejected, and conflicting assignments do not get applied.

Confirmed matches become local associations; availability refreshes automatically. Confirmed exclusions remain in the results and reports as `EXCLUDED_AI`, with the original requirement and reason recorded in the notes. They use no stock. **Reset AI exclusions** restores all excluded rows. Exclusions last for the current loaded BOM/window; associations persist. You can correct an AI association with the normal association dialog.

AI Assist sends BOM references, values, footprints and custom fields, plus eligible inventory part names, descriptions, parameters and unallocated quantities, to your configured endpoint. It does not send InvenTree credentials. Catalogs up to 1000 eligible parts are supported, with requests in batches of 10 BOM rows. No external AI service has been configured or called during development; the request and apply paths were tested with simulated responses. Model quality and your endpoint's context limit still determine how well it can propose matches.

The request format follows the official [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). JSON mode is optional for compatibility with local implementations.

## Create an InvenTree build order

**Create build order** is enabled when all included physical rows are associated and fully covered. Excluded rows do not contribute. The token needs permission to create BOM items, builds and stock allocations; a read-only account can still use all ordinary stock checks but cannot create builds.

1. Click **Create build order** and search for the existing assembly part to produce. It must be an active InvenTree assembly.
2. Search for the **bare PCB part** and select its specific stock lot. The plugin requires one PCB per assembled board and one lot with enough unallocated boards. The bare PCB part must be different from the assembled output part.
3. Enter a unique build reference and click **Prepare build preview**. This step reads InvenTree without writing anything.
4. The plugin checks for the assembly BOM in InvenTree. A matching existing BOM is reused. Only if no BOM exists is a new one prepared from the confirmed KiCad primary-part associations plus the selected bare PCB. Approved related parts used in a pick are registered as BOM substitutes when creating a new BOM.
5. Review the exact source stock IDs, locations, batches and quantities. Click **Create and allocate** to write the missing BOM, create the build order and allocate the planned component lots and the selected PCB lot.

An existing BOM is never overwritten, duplicated or silently extended. If it differs in components, quantities, consumption rules or approved substitutes, the preview stops and reports the mismatch. Duplicate BOM component lines and serial-tracked components requiring per-output allocation must currently be handled directly in InvenTree.

Stock is rechecked before writes and shared source lots are counted only once. Build references are checked for duplicates. A network failure is not automatically retried: the error identifies a created build when known, or asks you to inspect the reference and BOM before retrying if the server's acceptance is uncertain. Multi-step API writes are not one database transaction, so a failure can leave a partially created BOM or order that needs review.

Creating the order **allocates/reserves** stock, including the selected bare-PCB lot. It does not consume stock or mark the build complete. Continue and complete the build in InvenTree to record actual consumption. The PCB lot is recorded as the source of the BOM allocation, not just mentioned in a note.

## Release checkpoints

- `v0.3.0-persistent-token`: the pre-AI checkpoint, including the AppConsole fix and persistent Windows-encrypted InvenTree tokens.
- `v0.4.0-ai-build-orders`: configurable AI assistance, engineering-value instructions, automatic refresh after association, stock quantities in part search, and build/BOM preparation with exact PCB-lot allocation.

Both version ZIPs are retained under `dist`. The AI version is developed on `codex/ai-bom-assistance`.

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
