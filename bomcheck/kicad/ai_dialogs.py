"""Endpoint settings and review of AI proposals, in native KiCad dialogs."""
import wx

from ..ai import CompatibleAI
from ..report import label
from .credentials import credential_key, load_secret


class AISettings(wx.Dialog):
    def __init__(self, parent, config, settings_file):
        super().__init__(parent, title='AI endpoint settings', size=(710, 430), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.settings_file = settings_file
        self.original_url = config.get('base_url', '')
        self.url = wx.TextCtrl(self, value=self.original_url)
        self.model = wx.TextCtrl(self, value=config.get('model', ''))
        try:
            key = load_secret(settings_file, credential_key('ai', self.original_url)) if self.original_url else ''
        except Exception:
            key = ''
        self.key = wx.TextCtrl(self, value=key, style=wx.TE_PASSWORD)
        self.remember = wx.CheckBox(self, label='Remember AI key using Windows account encryption')
        self.remember.SetValue(config.get('remember_key', False))
        self.json_mode = wx.CheckBox(self, label='Request JSON mode (only if supported by your endpoint)')
        self.json_mode.SetValue(config.get('json_mode', False))
        layout = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, hgap=12, vgap=12)
        grid.AddGrowableCol(1)
        for title, control in [('API base URL (include /v1)', self.url), ('Model name', self.model), ('API key (optional for local servers)', self.key)]:
            grid.Add(wx.StaticText(self, label=title), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(control, 1, wx.EXPAND)
        layout.Add(grid, 0, wx.EXPAND | wx.ALL, 16)
        layout.Add(self.remember, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)
        layout.Add(self.json_mode, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)
        notice = wx.StaticText(self, label='AI Assist sends BOM references, values, footprints and fields, plus inventory names, descriptions and parameters, to this endpoint. InvenTree credentials are never sent. Proposals are reviewed before application.')
        notice.Wrap(650)
        layout.Add(notice, 1, wx.EXPAND | wx.ALL, 16)
        layout.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)
        self.SetSizer(layout)
        self.FindWindowById(wx.ID_OK).SetLabel('Save AI settings')
        self.Bind(wx.EVT_BUTTON, self.on_save, id=wx.ID_OK)
        self.url.Bind(wx.EVT_KILL_FOCUS, self.on_url_changed)

    def on_url_changed(self, event):
        if self.url.GetValue().strip().rstrip('/') != self.original_url.rstrip('/'):
            try:
                self.key.SetValue(load_secret(self.settings_file, credential_key('ai', self.url.GetValue())))
            except Exception:
                self.key.SetValue('')
            self.original_url = self.url.GetValue().strip()
        event.Skip()

    def get_config(self):
        return {'base_url': self.url.GetValue().strip().rstrip('/'), 'model': self.model.GetValue().strip(),
                'remember_key': self.remember.GetValue(), 'json_mode': self.json_mode.GetValue()}

    def on_save(self, event):
        try:
            CompatibleAI(self.url.GetValue(), self.model.GetValue(), self.key.GetValue())
        except ValueError as error:
            wx.MessageBox(str(error), 'Invalid AI settings', wx.OK | wx.ICON_ERROR)
            return
        self.EndModal(wx.ID_OK)


class AIReview(wx.Dialog):
    def __init__(self, parent, lines, proposals, catalog):
        super().__init__(parent, title='Review AI proposals', size=(1150, 610), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.proposals = proposals
        layout = wx.BoxSizer(wx.VERTICAL)
        notice = wx.StaticText(self, label='Review each proposed exclusion or part match. Only checked proposals will be applied. Excluded rows remain visible in results and reports. Confirm electrical ratings and package before accepting a match.')
        notice.Wrap(1080)
        layout.Add(notice, 0, wx.EXPAND | wx.ALL, 14)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.list.EnableCheckBoxes()
        for name, width in [('References', 130), ('Value / footprint', 200), ('Action', 90), ('Proposed part', 220), ('Available', 80), ('Confidence', 85), ('Reason', 430)]:
            self.list.AppendColumn(name, width=width)
        for index, proposal in enumerate(proposals):
            line = lines[proposal.row_id]
            part = catalog.get(proposal.part_id)
            values = [line.refs, f'{line.value} / {line.footprint}', proposal.action.upper(),
                      label(part) if part else '', part.get('_available', 'Unknown') if part else '',
                      f'{proposal.confidence:.0%}', proposal.reason]
            self.list.InsertItem(index, str(values[0]))
            for column, value in enumerate(values[1:], 1):
                self.list.SetItem(index, column, str(value))
            self.list.CheckItem(index, proposal.action != 'keep' and proposal.confidence >= 0.9)
        layout.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        layout.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 14)
        self.SetSizer(layout)
        self.FindWindowById(wx.ID_OK).SetLabel('Apply checked proposals')

    def selected(self):
        return [proposal for i, proposal in enumerate(self.proposals) if self.list.IsItemChecked(i) and proposal.action != 'keep']
