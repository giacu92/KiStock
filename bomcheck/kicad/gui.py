"""wxPython UI hosted by KiCad; network work never blocks its event loop."""
import copy
import os
import threading
import webbrowser
from collections import Counter
from pathlib import Path

import wx

from ..checker import check, identity, usable
from ..inputs import read_bom
from ..inventree import InvenTree
from ..report import label, write_reports
from .settings import assign, load_settings, save_settings, settings_path
from .credentials import credential_key, load_secret, save_secret


def background(window, operation, finished):
    def work():
        try:
            value, error = operation(), None
        except Exception as exc:
            value, error = None, str(exc)
        def deliver():
            if not window.closed:
                finished(value, error)
        wx.CallAfter(deliver)
    threading.Thread(target=work, daemon=True).start()


class PartPicker(wx.Dialog):
    def __init__(self, parent, client, line):
        super().__init__(parent, title='Associate with an InvenTree part', size=(820, 530), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.closed, self.parts, self.selected = False, [], None
        self.client = client
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label=f'{line.refs}  |  {line.value}  |  {line.footprint}'), 0, wx.ALL, 12)
        layout.Add(wx.StaticText(self, label='Choose an exact part. Check package, ratings and tolerance before associating.'), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.query = wx.TextCtrl(self, value=line.value, style=wx.TE_PROCESS_ENTER)
        self.search = wx.Button(self, label='Search inventory')
        bar.Add(self.query, 1, wx.RIGHT, 8)
        bar.Add(self.search)
        layout.Add(bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for col, width in [('ID', 60), ('IPN', 160), ('Name', 250), ('Description', 290)]:
            self.list.AppendColumn(col, width=width)
        layout.Add(self.list, 1, wx.EXPAND | wx.ALL, 12)
        self.status = wx.StaticText(self, label='Enter a search term, then select a result.')
        layout.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.FindWindowById(wx.ID_OK).SetLabel('Use selected part')
        self.FindWindowById(wx.ID_OK).Disable()
        self.search.Bind(wx.EVT_BUTTON, self.on_search)
        self.query.Bind(wx.EVT_TEXT_ENTER, self.on_search)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda event: self.FindWindowById(wx.ID_OK).Enable())
        self.Bind(wx.EVT_BUTTON, self.on_accept, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        wx.CallAfter(self.on_search)

    def on_search(self, event=None):
        if self.closed or not self.search.IsEnabled():
            return
        query = self.query.GetValue().strip()
        if not query:
            return
        self.search.Disable()
        self.FindWindowById(wx.ID_OK).Disable()
        self.list.DeleteAllItems()
        self.status.SetLabel('Searching…')
        def done(parts, error):
            self.search.Enable()
            if error:
                self.status.SetLabel(error)
                return
            self.parts = [p for p in parts if usable(p)]
            for part in self.parts:
                index = self.list.InsertItem(self.list.GetItemCount(), str(part['pk']))
                for column, text in enumerate([part.get('IPN') or '', part.get('name') or '', part.get('description') or ''], 1):
                    self.list.SetItem(index, column, text)
            self.status.SetLabel(f'{len(self.parts)} eligible parts found. Select one to confirm the association.')
        background(self, lambda: self.client.list('part/', search=query, active='true'), done)

    def on_accept(self, event):
        index = self.list.GetFirstSelected()
        if index >= 0:
            self.selected = self.parts[index]
            self.closed = True
            self.EndModal(wx.ID_OK)

    def on_close(self, event):
        self.closed = True
        event.Skip()


class CheckerDialog(wx.Dialog):
    def __init__(self, parent, lines, source, settings_file=None):
        super().__init__(parent, title='InvenTree BoM Checker', size=(1170, 760), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.closed = False
        self.lines, self.source = lines, source or 'Unsaved PCB'
        self.settings_file = Path(settings_file) if settings_file else settings_path()
        self.config = load_settings(self.settings_file)
        self.credential_error = ''
        try:
            saved_token = load_secret(self.settings_file, credential_key('inventree', self.config['url']))
        except Exception as error:
            saved_token = ''
            self.credential_error = str(error)
        self.results = None
        self.busy = False
        layout = wx.BoxSizer(wx.VERTICAL)
        self.source_label = wx.StaticText(self, label='Source: ' + self.source)
        layout.Add(self.source_label, 0, wx.EXPAND | wx.ALL, 12)
        grid = wx.FlexGridSizer(cols=4, hgap=8, vgap=10)
        grid.AddGrowableCol(1)
        grid.AddGrowableCol(3)
        self.url = wx.TextCtrl(self, value=self.config['url'])
        self.token = wx.TextCtrl(self, value=saved_token or os.environ.get('INVENTREE_TOKEN', ''), style=wx.TE_PASSWORD)
        grid.Add(wx.StaticText(self, label='InvenTree URL'), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.url, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label='InvenTree API token'), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.token, 1, wx.EXPAND)
        self.boards = wx.SpinCtrl(self, min=1, max=100000, initial=1)
        self.related = wx.CheckBox(self, label='Use related parts as approved equivalents')
        self.related.SetValue(self.config.get('related_as_equivalents', True))
        grid.Add(wx.StaticText(self, label='Boards to assemble'), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.boards, 0)
        grid.Add(self.related, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.AddSpacer(1)
        layout.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        credentials_bar = wx.BoxSizer(wx.HORIZONTAL)
        self.remember = wx.CheckBox(self, label='Remember API token on this computer (Windows account encryption)')
        self.remember.SetValue(self.config.get('remember_token', False))
        self.save_button = wx.Button(self, label='Save settings')
        credentials_bar.Add(self.remember, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        credentials_bar.Add(self.save_button)
        layout.Add(credentials_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.run = wx.Button(self, label='Check availability')
        self.import_button = wx.Button(self, label='Import CSV / iBOM / XML…')
        self.associate = wx.Button(self, label='Associate selected row…')
        self.export = wx.Button(self, label='Export HTML + CSV…')
        for button in [self.run, self.import_button, self.associate, self.export]:
            bar.Add(button, 0, wx.RIGHT, 8)
        layout.Add(bar, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.table = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for name, width in [('Status', 140), ('References', 150), ('Value', 100), ('Footprint', 200), ('Required', 75), ('Covered', 75), ('Missing', 75), ('Original part / proposed pick', 390)]:
            self.table.AppendColumn(name, width=width)
        layout.Add(self.table, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 75))
        layout.Add(self.details, 0, wx.EXPAND | wx.ALL, 12)
        self.status = wx.StaticText(self, label='Read-only inventory check. No stock is reserved or changed.')
        layout.Add(self.status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        layout.Add(self.CreateButtonSizer(wx.CLOSE), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((900, 600))
        self.run.Bind(wx.EVT_BUTTON, self.on_check)
        self.import_button.Bind(wx.EVT_BUTTON, self.on_import)
        self.associate.Bind(wx.EVT_BUTTON, self.on_associate)
        self.export.Bind(wx.EVT_BUTTON, self.on_export)
        self.save_button.Bind(wx.EVT_BUTTON, self.on_save)
        self.url.Bind(wx.EVT_KILL_FOCUS, self.on_server_changed)
        self.table.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_selection)
        self.table.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_associate)
        self.Bind(wx.EVT_BUTTON, self.on_close, id=wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.paint_rows()
        self.export.Disable()
        if self.credential_error:
            self.status.SetLabel(self.credential_error + ' Enter the token again and save settings.')

    def paint_rows(self):
        self.table.DeleteAllItems()
        for i, line in enumerate(self.lines):
            result = self.results[i] if self.results else None
            status = result.status if result else 'NOT_CHECKED'
            required = result.required if result else line.quantity * self.boards.GetValue()
            pick = '; '.join(f'{label(self.parts[pk])}: {qty}' for pk, qty in result.used.items()) if result else ''
            if result and not pick and result.primary:
                pick = label(result.primary)
            values = [status, line.refs, line.value, line.footprint, required,
                      sum(result.used.values()) if result else '—', result.missing if result else '—', pick]
            index = self.table.InsertItem(i, str(values[0]))
            for column, value in enumerate(values[1:], 1):
                self.table.SetItem(index, column, str(value))
            if status in ('OK', 'OK_EQUIVALENT'):
                self.table.SetItemTextColour(index, wx.Colour(20, 110, 65))
            elif status in ('SHORTAGE', 'PARTIAL'):
                self.table.SetItemTextColour(index, wx.Colour(170, 60, 20))

    def controls_config(self):
        config = copy.deepcopy(self.config)
        url = self.url.GetValue().strip().rstrip('/')
        if url != config['url'].rstrip('/'):
            # Part IDs are server-specific. Never carry associations to a new host.
            config['mappings'] = []
        config.update(url=url, related_as_equivalents=self.related.GetValue(), remember_token=self.remember.GetValue())
        return config

    def persist(self, config):
        save_secret(self.settings_file, credential_key('inventree', config['url']),
                    self.token.GetValue().strip() if config['remember_token'] else '')
        save_settings(self.settings_file, config)

    def on_save(self, event=None):
        try:
            config = self.controls_config()
            InvenTree(config['url'], '')  # Validate without a network request.
            self.persist(config)
            self.config = config
            self.status.SetLabel('Settings saved. API token remembered.' if config['remember_token'] else 'Settings saved. Saved API token removed for this server.')
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot save settings', wx.OK | wx.ICON_ERROR)

    def on_server_changed(self, event):
        if self.url.GetValue().strip().rstrip('/') != self.config['url'].rstrip('/'):
            try:
                self.token.SetValue(load_secret(self.settings_file, credential_key('inventree', self.url.GetValue())))
            except Exception:
                self.token.SetValue('')
        event.Skip()

    def client(self, config):
        token = self.token.GetValue().strip()
        if not token:
            raise ValueError('Enter an InvenTree API token first.')
        return InvenTree(config['url'], token)

    def set_busy(self, busy):
        self.busy = busy
        for control in (self.run, self.import_button, self.associate, self.url, self.token, self.boards, self.related, self.remember, self.save_button):
            control.Enable(not busy)
        self.export.Enable(not busy and self.results is not None)

    def on_check(self, event=None):
        if self.busy:
            return
        try:
            if not self.lines:
                raise ValueError('No components to check. Import a BOM or open a populated PCB.')
            config = self.controls_config()
            client = self.client(config)
            self.persist(config)
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot start check', wx.OK | wx.ICON_ERROR)
            return
        boards = self.boards.GetValue()
        self.results = None
        self.config = config
        self.details.SetValue('')
        self.paint_rows()
        self.set_busy(True)
        self.status.SetLabel(f'Checking {len(self.lines)} BOM groups against InvenTree…')
        def done(value, error):
            if error:
                self.status.SetLabel('Check failed. No current availability results.')
                self.set_busy(False)
                wx.MessageBox(error, 'InvenTree request failed', wx.OK | wx.ICON_ERROR)
                return
            self.results, self.parts, self.stock = value
            self.checked_boards = boards
            self.paint_rows()
            counts = Counter(row.status for row in self.results)
            self.status.SetLabel(' | '.join(f'{state}: {count}' for state, count in sorted(counts.items())))
            self.set_busy(False)
        background(self, lambda: check(self.lines, client, config, boards), done)

    def on_import(self, event):
        with wx.FileDialog(self, 'Import a BOM', wildcard='BOM files (*.csv;*.html;*.xml)|*.csv;*.html;*.xml', style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            try:
                lines = read_bom(dialog.GetPath())
                if not lines:
                    raise ValueError('The BOM contains no populated components.')
                self.lines, self.source = lines, dialog.GetPath()
                self.source_label.SetLabel('Source: ' + self.source)
                self.results = None
                self.export.Disable()
                self.details.SetValue('')
                self.paint_rows()
                self.status.SetLabel('BOM loaded. Run Check availability.')
            except Exception as error:
                wx.MessageBox(str(error), 'Cannot import BOM', wx.OK | wx.ICON_ERROR)

    def on_selection(self, event):
        if self.results:
            row = self.results[event.GetIndex()]
            text = row.note
            if row.suggestions:
                text += '\nUnconfirmed suggestions: ' + '; '.join(label(p) for p in row.suggestions)
            if row.candidates:
                text += '\nInitial unallocated stock (shared across rows): ' + '; '.join(f'{label(p)}: {self.stock[p["pk"]]}' for p in row.candidates)
            self.details.SetValue(text.strip())

    def on_associate(self, event):
        if self.busy:
            return
        index = self.table.GetFirstSelected()
        if index < 0:
            wx.MessageBox('Select a BOM row first.', 'Associate part')
            return
        line = self.lines[index]
        try:
            config = self.controls_config()
            # Explicit design identifiers take precedence over value/package maps.
            key = identity(line, {**config, 'mappings': []})
            if key:
                raise ValueError('This row already has an explicit IPN/MPN. Correct that field in the design or export before associating by value and footprint.')
            picker = PartPicker(self, self.client(config), line)
            try:
                if picker.ShowModal() != wx.ID_OK:
                    return
                assign(config, line, picker.selected)
                save_settings(self.settings_file, config)
            finally:
                picker.closed = True
                picker.Destroy()
            self.config = config
            self.results = None
            self.export.Disable()
            self.paint_rows()
            self.details.SetValue(f'Associated {line.value} / {line.footprint} with {label(picker.selected)}. Applies to rows with the same value and footprint that have no explicit IPN/MPN.')
            self.status.SetLabel('Association saved locally. Run Check availability again.')
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot associate part', wx.OK | wx.ICON_ERROR)

    def on_export(self, event):
        if not self.results:
            return
        with wx.DirDialog(self, 'Choose a report folder') as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            try:
                base = Path(dialog.GetPath()) / (Path(self.source).stem + '-stock')
                if Path(self.source).resolve() in {Path(str(base) + ext).resolve() for ext in ('.html', '.csv')}:
                    raise ValueError('Choose another folder to avoid overwriting the source BOM.')
                html_path, _ = write_reports(base, self.source, self.results, self.parts, self.stock, self.checked_boards)
                webbrowser.open(html_path.resolve().as_uri())
            except Exception as error:
                wx.MessageBox(str(error), 'Cannot export report', wx.OK | wx.ICON_ERROR)

    def on_close(self, event):
        self.closed = True
        self.token.SetValue('')
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
