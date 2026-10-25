# Changelog

## 0.5.5 — Remove saved associations

- Remove a local value/footprint association from the row context menu or the association picker.
- Save removal immediately and invalidate availability results shared across rows; refresh stock when credentials are available.
- Protect explicit design IPN/MPN fields, which remain editable in KiCad.
- Label the activity indicator with the running operation, including Check availability.

## 0.5.4 — Visible background activity

- Show an animated activity bar and elapsed time during AI Assist, Mouser preparation and availability checks.
- Keep the current operation description visible; stop the indicator on completion, failure or closing.

## 0.5.3 — Tolerate irrelevant AI part IDs

- Discard superfluous part IDs on keep/exclude actions instead of rejecting the whole AI response.
- Continue validating actual matches against eligible inventory IDs before review and application.

## 0.5.2 — Preserve and restore associations

- Send only unassociated, non-excluded BOM rows to AI, preserving original row IDs.
- Show saved associations immediately, including their local part labels, before stock is checked.
- Automatically refresh availability on opening/importing a BOM when credentials are available.
- Keep associations visible if the server is offline; stock remains explicitly pending.

## 0.5.1 — AI connection test

- Test the current AI endpoint and model directly from Settings without saving.
- Run the request in the background and show JSON validation, elapsed time or connection errors.

## 0.5.0 — Mouser carts, unified Settings and Ollama

- Move InvenTree, AI and Mouser configuration into a tabbed Settings dialog.
- Support local Ollama's native chat API as well as OpenAI-compatible endpoints.
- Rank inventory candidates locally and send small batches suited to local model contexts.
- Search real Mouser candidates, ask AI to select suitable parts and review the proposed quantities.
- Create a new Mouser cart only on explicit confirmation; no order, checkout or payment operation is implemented.
- Handle MOQ/order multiples, shared SKU stock, unresolved rows, partial cart errors and duplicate-click prevention.
- Store Mouser credentials with Windows account encryption and keep keys out of model prompts and packages.

## 0.4.1 — Visible version

- Display the loaded version in the PCB Editor plugin menu entry and plugin window title.

## 0.4.0 — AI assistance and build orders

- Configure an OpenAI-compatible base URL, model and optional encrypted API key inside KiCad.
- Default AI instructions normalize engineering notation, check packages and ratings, prefer suitable available stock and leave uncertain parts unresolved.
- Review proposed non-component exclusions and inventory matches before applying them.
- Refresh the associated row and shared-stock calculation automatically after confirming a part.
- Display unallocated quantities in inventory searches, with Unknown for failed stock lookups.
- Search assembly and bare-PCB parts; select the exact PCB stock lot.
- Reuse an existing matching InvenTree BOM; create a BOM only when absent.
- Preview and explicitly create a build order with exact component and PCB stock allocations.
- Detect changed stock, mismatched BOMs, duplicate references and partial API failures.

## 0.3.0 — Pre-AI checkpoint

- Native KiCad 9/10 PCB Action Plugin with the AppConsole launch fix.
- Optional persistent tokens encrypted for the current Windows account using DPAPI.
- Save settings and forget remembered credentials per server.
- CSV, iBOM and KiCad XML import, inventory associations, shared-stock comparison and report export.
