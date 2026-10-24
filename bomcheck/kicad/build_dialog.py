"""Select an assembly and a specific bare-PCB lot, then review before writing."""
import webbrowser
from pathlib import Path

import wx

from ..builds import create_build, prepare_build, stock_items, free_stock, BuildCreationError
from ..inputs import Line
from ..report import label


class BuildDialog(wx.Dialog):
    def __init__(self, parent, client, results, boards, source):
        super().__init__(parent, title='Create InvenTree build order', size=(1050, 720), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.closed, self.client, self.results, self.boards = False, client, results, boards
        self.assembly, self.pcb, self.lots, self.plan, self.created = None, None, [], None, None
        self.query = Path(source).stem
        self.writing = False
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label=f'Build {boards} assembled boards. Select the assembly output and the bare PCB stock to consume.'), 0, wx.ALL, 12)
        self.assembly_label = wx.StaticText(self, label='Assembly: not selected')
        self.pcb_label = wx.StaticText(self, label='Bare PCB: not selected')
        self.assembly_button = wx.Button(self, label='Find assembly to produce…')
        self.pcb_button = wx.Button(self, label='Find bare PCB part…')
        for button, text in ((self.assembly_button, self.assembly_label), (self.pcb_button, self.pcb_label)):
            bar = wx.BoxSizer(wx.HORIZONTAL)
            bar.Add(button, 0, wx.RIGHT, 12)
            bar.Add(text, 1, wx.ALIGN_CENTER_VERTICAL)
            layout.Add(bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        bar.Add(wx.StaticText(self, label='New build reference'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        self.reference = wx.TextCtrl(self)
        bar.Add(self.reference, 1)
        layout.Add(bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        layout.Add(wx.StaticText(self, label='Choose one bare-PCB lot with enough free stock (one PCB per assembly):'), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.stock = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for title, width in [('Stock ID', 100), ('Location', 320), ('Batch', 230), ('Unallocated', 140)]:
            self.stock.AppendColumn(title, width=width)
        layout.Add(self.stock, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.preview = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 170))
        layout.Add(self.preview, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.status = wx.StaticText(self, label='Prepare a preview first. No records are written until Create and allocate is clicked.')
        layout.Add(self.status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.prepare_button = wx.Button(self, label='Prepare build preview')
        self.create_button = wx.Button(self, label='Create and allocate')
        self.create_button.Disable()
        self.close_button = wx.Button(self, wx.ID_CANCEL, 'Close')
        for button in (self.prepare_button, self.create_button, self.close_button):
            bar.Add(button, 0, wx.RIGHT, 10)
        layout.Add(bar, 0, wx.ALL, 12)
        self.SetSizer(layout)
        self.assembly_button.Bind(wx.EVT_BUTTON, lambda event: self.choose_part(True))
        self.pcb_button.Bind(wx.EVT_BUTTON, lambda event: self.choose_part(False))
        self.prepare_button.Bind(wx.EVT_BUTTON, self.on_prepare)
        self.create_button.Bind(wx.EVT_BUTTON, self.on_create)
        self.reference.Bind(wx.EVT_TEXT, self.invalidate)
        self.stock.Bind(wx.EVT_LIST_ITEM_SELECTED, self.invalidate)
        self.close_button.Bind(wx.EVT_BUTTON, self.on_close)
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def invalidate(self, event=None):
        self.plan = None
        self.create_button.Disable()

    def busy(self, value):
        self.writing = value
        for control in (self.assembly_button, self.pcb_button, self.reference, self.stock, self.prepare_button, self.close_button):
            control.Enable(not value)
        self.create_button.Enable(not value and self.plan is not None and self.created is None)

    def choose_part(self, assembly):
        from .gui import PartPicker, background
        picker = PartPicker(self, self.client, Line('', self.query, '', 1, {}), filters={'assembly': 'true'} if assembly else {'component': 'true'})
        try:
            if picker.ShowModal() != wx.ID_OK:
                return
            selected = picker.selected
        finally:
            picker.closed = True
            picker.Destroy()
        self.invalidate()
        if assembly:
            self.assembly = selected
            self.assembly_label.SetLabel('Assembly: ' + label(selected))
            return
        self.pcb = selected
        self.pcb_label.SetLabel('Bare PCB: ' + label(selected))
        self.stock.DeleteAllItems()
        self.busy(True)
        self.status.SetLabel('Loading bare-PCB stock lots…')
        def done(lots, error):
            self.busy(False)
            if error:
                self.status.SetLabel(error)
                return
            self.lots = lots
            for i, lot in enumerate(lots):
                self.stock.InsertItem(i, str(lot['pk']))
                self.stock.SetItem(i, 1, str((lot.get('location_detail') or {}).get('pathstring') or lot.get('location') or ''))
                self.stock.SetItem(i, 2, lot.get('batch') or '')
                try:
                    available = str(free_stock(lot))
                except ValueError:
                    available = 'Unknown'
                self.stock.SetItem(i, 3, available)
            self.status.SetLabel('Select a bare-PCB lot, enter a unique build reference, then prepare the preview.')
        background(self, lambda: stock_items(self.client, selected['pk']), done)

    def on_prepare(self, event=None):
        from .gui import background
        index = self.stock.GetFirstSelected()
        if not self.assembly or not self.pcb or index < 0:
            wx.MessageBox('Choose the assembly, bare-PCB part and PCB stock lot first.', 'Build order')
            return
        self.invalidate()
        self.busy(True)
        self.status.SetLabel('Checking the existing InvenTree BOM and exact stock allocations…')
        reference = self.reference.GetValue().strip()
        def done(plan, error):
            self.busy(False)
            if error:
                self.status.SetLabel('Build preview failed. No records were written.')
                wx.MessageBox(error, 'Cannot prepare build', wx.OK | wx.ICON_ERROR)
                return
            self.plan = plan
            lines = [f'Assembly: {label(plan.assembly)}', f'Build reference: {plan.reference}; quantity: {plan.boards}',
                     'BOM: reuse existing matching BOM' if plan.bom else 'BOM: create missing BOM from KiCad associations and selected bare PCB',
                     f'Bare PCB source stock lot: #{plan.pcb_stock_id}', '', 'Allocations:']
            lines += [f'Part #{a["actual_part"]} → stock #{a["stock_item"]}, quantity {a["quantity"]}; {a["location"]}; batch {a["batch"]}' for a in plan.allocations]
            lines += ['', 'Create and allocate writes the missing BOM (if any), creates the build and reserves these stock lots.',
                      'Nothing is consumed or completed. Finish the build in InvenTree to consume its allocations.']
            self.preview.SetValue('\n'.join(lines))
            self.create_button.Enable()
            self.status.SetLabel('Preview ready. Review the BOM and stock lots, then click Create and allocate.')
        background(self, lambda: prepare_build(self.client, self.results, self.assembly['pk'], self.pcb['pk'], self.lots[index]['pk'], self.boards, reference), done)

    def on_create(self, event):
        from .gui import background
        if not self.plan or self.created:
            return
        plan = self.plan
        self.busy(True)
        self.create_button.Disable()
        self.status.SetLabel('Creating the build and allocating stock. Do not retry until this request finishes.')
        def operation():
            try:
                return create_build(self.client, plan), None
            except BuildCreationError as error:
                return None, error
        def done(value, error):
            self.plan = None  # Every retry requires a new preview.
            self.busy(False)
            if error:
                self.status.SetLabel('Build creation could not be confirmed. Inspect InvenTree before retrying.')
                wx.MessageBox(error, 'Build order', wx.OK | wx.ICON_ERROR)
                return
            build, failure = value
            if failure:
                self.status.SetLabel(str(failure))
                wx.MessageBox(str(failure), 'Build order needs attention', wx.OK | wx.ICON_ERROR)
                if failure.build_id:
                    self.created = {'pk': failure.build_id}
                    webbrowser.open(self.client.base + f'web/manufacturing/build-order/{failure.build_id}/details')
                return
            self.created = build
            self.prepare_button.Disable()
            self.status.SetLabel(f'Build {build.get("reference", build["pk"])} created and stock allocated, including PCB lot #{plan.pcb_stock_id}.')
            webbrowser.open(self.client.base + f'web/manufacturing/build-order/{build["pk"]}/details')
        background(self, operation, done)

    def on_close(self, event):
        if self.writing:
            self.status.SetLabel('Wait for the current request to finish before closing.')
            if hasattr(event, 'Veto'):
                event.Veto()
            return
        self.closed = True
        self.EndModal(wx.ID_CANCEL)
