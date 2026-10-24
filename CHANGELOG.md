# Changelog

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
