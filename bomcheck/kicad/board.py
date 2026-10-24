"""Snapshot the current board on KiCad's UI thread; no pcbnew objects escape."""
from ..inputs import Line


def read_board(board):
    groups = {}
    for footprint in board.GetFootprints():
        if footprint.IsDNP() or footprint.IsExcludedFromBOM():
            continue
        ref = footprint.GetReference()
        if not ref or ref.startswith('#'):
            continue
        fields = {f.GetName(): f.GetText() for f in footprint.GetFields()}
        value = footprint.GetValue()
        package = str(footprint.GetFPID().GetLibItemName())
        # Full custom fields preserve different MPNs even for equal values.
        custom = {k: v for k, v in fields.items() if k not in ('Reference', 'Value', 'Footprint', 'Datasheet', 'Description')}
        key = value, package, tuple(sorted(custom.items()))
        if key not in groups:
            groups[key] = Line(ref, value, package, 1, custom)
        else:
            groups[key].refs += ', ' + ref
            groups[key].quantity += 1
    return list(groups.values())
