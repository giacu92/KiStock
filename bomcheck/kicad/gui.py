"""wxPython UI hosted by KiCad; network work never blocks its event loop."""
import copy
import os
import threading
import time
import webbrowser
from collections import Counter
from pathlib import Path

import wx

from ..checker import check, identity, usable
from ..ai import CompatibleAI, analyze, apply_proposals, check_cancelled
from ..mouser import Mouser, prepare_cart
from ..inputs import read_bom
from ..inventree import InvenTree
from ..report import label, write_reports
from ..version import __version__
from .settings import assign, load_settings, save_settings, settings_path, skipped_rows, set_component_skipped
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
    def __init__(self, parent, client, line, filters=None, can_remove=False):
        super().__init__(parent, title='Associate with an InvenTree part', size=(990, 530), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.closed, self.parts, self.selected = False, [], None
        self.client = client
        self.filters = filters or {}
        self.location_request = 0
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
        for col, width in [('ID', 60), ('IPN', 150), ('Name', 245), ('Available (unallocated)', 155), ('Description', 300)]:
            self.list.AppendColumn(col, width=width)
        layout.Add(self.list, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(wx.StaticText(self, label='Stock locations — physical quantities (may include allocated stock)'), 0, wx.LEFT | wx.RIGHT, 12)
        self.locations = wx.TextCtrl(self, value='Select a part to see where it is stored.', style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 65))
        layout.Add(self.locations, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.status = wx.StaticText(self, label='Enter a search term, then select a result.')
        layout.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.remove_association = wx.Button(self, label='Remove current association')
        self.remove_association.Enable(can_remove)
        self.remove_association.SetToolTip('Remove the saved association for this value and footprint from local memory.')
        self.remove_association.Bind(wx.EVT_BUTTON, self.on_remove)
        layout.Add(self.remove_association, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.FindWindowById(wx.ID_OK).SetLabel('Use selected part')
        self.FindWindowById(wx.ID_OK).Disable()
        self.search.Bind(wx.EVT_BUTTON, self.on_search)
        self.query.Bind(wx.EVT_TEXT_ENTER, self.on_search)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_part_selection)
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
        self.location_request += 1
        self.locations.SetValue('Select a part to see where it is stored.')
        self.status.SetLabel('Searching…')
        def done(parts, error):
            if error:
                self.search.Enable()
                self.status.SetLabel(error)
                return
            self.parts = [p for p in parts if usable(p)]
            for part in self.parts:
                index = self.list.InsertItem(self.list.GetItemCount(), str(part['pk']))
                for column, text in enumerate([part.get('IPN') or '', part.get('name') or '', 'Loading…', part.get('description') or ''], 1):
                    self.list.SetItem(index, column, text)
            self.status.SetLabel(f'{len(self.parts)} eligible parts found. Loading unallocated stock…')
            parts_to_load = list(self.parts)
            def load_stock():
                quantities = []
                for part in parts_to_load:
                    if self.closed:
                        break
                    try:
                        quantities.append(str(self.client.available(part['pk'])))
                    except Exception:
                        quantities.append('Unknown')
                return quantities
            def show_stock(quantities, stock_error):
                self.search.Enable()
                for index, quantity in enumerate(quantities or []):
                    self.list.SetItem(index, 3, quantity)
                self.status.SetLabel(f'{len(self.parts)} eligible parts. Available means unallocated stock; Unknown means the stock request failed.')
            background(self, load_stock, show_stock)
        background(self, lambda: self.client.list('part/', search=query, active='true', **self.filters), done)

    def on_part_selection(self, event):
        index = event.GetIndex()
        if not 0 <= index < len(self.parts):
            return
        self.FindWindowById(wx.ID_OK).Enable()
        self.location_request += 1
        request = self.location_request
        pk = self.parts[index]['pk']
        self.locations.SetValue('Loading stock locations…')
        def done(locations, error):
            if request != self.location_request:
                return
            if error:
                self.locations.SetValue('Locations unavailable: ' + error)
            else:
                self.locations.SetValue('\n'.join(f'{item["path"]} — {item["quantity"]} units' for item in locations)
                                        or 'No physical stock locations found.')
        background(self, lambda: self.client.stock_locations(pk), done)

    def on_accept(self, event):
        index = self.list.GetFirstSelected()
        if index >= 0:
            self.selected = self.parts[index]
            self.closed = True
            self.EndModal(wx.ID_OK)

    def on_close(self, event):
        self.closed = True
        event.Skip()

    def on_remove(self, event):
        if self.remove_association.IsEnabled():
            self.closed = True
            self.EndModal(wx.ID_DELETE)


class CheckerDialog(wx.Dialog):
    def __init__(self, parent, lines, source, settings_file=None):
        super().__init__(parent, title=f'KiStock {__version__}', size=(1170, 760), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        icon_path = Path(__file__).with_name('icon.png')
        if icon_path.is_file():
            self.SetIcon(wx.Icon(str(icon_path), wx.BITMAP_TYPE_PNG))
        self.closed = False
        self.lines, self.source = lines, source or 'Unsaved PCB'
        self.settings_file = Path(settings_file) if settings_file else settings_path()
        self.config = load_settings(self.settings_file)
        self.exclusions = {}
        self.skips = skipped_rows(self.config, self.source, self.lines)
        self.ai_key = ''
        self.focus_row = None
        self.credential_error = ''
        try:
            saved_token = load_secret(self.settings_file, credential_key('inventree', self.config['url']))
        except Exception as error:
            saved_token = ''
            self.credential_error = str(error)
        self.results = None
        self.busy = False
        self.ai_cancel_event = None
        layout = wx.BoxSizer(wx.VERTICAL)
        self.source_label = wx.StaticText(self, label='Source: ' + self.source)
        layout.Add(self.source_label, 0, wx.EXPAND | wx.ALL, 12)
        self.inventory_token = saved_token or os.environ.get('INVENTREE_TOKEN', '')
        self.mouser_key = ''
        self.mouser_search_key = ''
        for service, attribute, url in [('ai', 'ai_key', self.config.get('ai', {}).get('base_url', '')),
                                         ('mouser', 'mouser_key', 'https://api.mouser.com'),
                                         ('mouser-search', 'mouser_search_key', 'https://api.mouser.com')]:
            try:
                setattr(self, attribute, load_secret(self.settings_file, credential_key(service, url)))
            except Exception:
                pass
        controls = wx.BoxSizer(wx.HORIZONTAL)
        self.settings_button = wx.Button(self, label='Settings…')
        controls.Add(self.settings_button, 0, wx.RIGHT, 16)
        controls.Add(wx.StaticText(self, label='Boards to assemble'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.boards = wx.SpinCtrl(self, min=1, max=100000, initial=1)
        controls.Add(self.boards)
        layout.Add(controls, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        ai_bar = wx.BoxSizer(wx.HORIZONTAL)
        self.ai_button = wx.Button(self, label='AI Assist: filter and match…')
        self.reset_exclusions = wx.Button(self, label='Reset AI exclusions')
        self.mouser_button = wx.Button(self, label='Create Mouser cart…')
        for button in (self.ai_button, self.reset_exclusions, self.mouser_button):
            ai_bar.Add(button, 0, wx.RIGHT, 8)
        layout.Add(ai_bar, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.run = wx.Button(self, label='Check availability')
        self.import_button = wx.Button(self, label='Import CSV / iBOM / XML…')
        self.associate = wx.Button(self, label='Associate selected row…')
        self.export = wx.Button(self, label='Export HTML + CSV…')
        self.build_button = wx.Button(self, label='Create build order…')
        for button in [self.run, self.import_button, self.associate, self.export, self.build_button]:
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
        activity = wx.BoxSizer(wx.HORIZONTAL)
        self.activity_bar = wx.Gauge(self, range=100, size=(-1, 16))
        self.activity_text = wx.StaticText(self, label='Working… 0:00 elapsed', size=(290, -1), style=wx.ST_NO_AUTORESIZE)
        self.cancel_activity = wx.Button(self, label='Cancel')
        self.cancel_activity.Bind(wx.EVT_BUTTON, self.on_cancel_ai)
        activity.Add(self.activity_bar, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN, 12)
        activity.Add(self.activity_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN, 8)
        activity.Add(self.cancel_activity, 0, wx.ALIGN_CENTER_VERTICAL | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        layout.Add(activity, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.activity_bar.Hide()
        self.activity_text.Hide()
        self.cancel_activity.Hide()
        self.activity_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_activity, self.activity_timer)
        layout.Add(self.CreateButtonSizer(wx.CLOSE), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((900, 600))
        self.run.Bind(wx.EVT_BUTTON, self.on_check)
        self.import_button.Bind(wx.EVT_BUTTON, self.on_import)
        self.associate.Bind(wx.EVT_BUTTON, self.on_associate)
        self.export.Bind(wx.EVT_BUTTON, self.on_export)
        self.build_button.Bind(wx.EVT_BUTTON, self.on_build)
        self.settings_button.Bind(wx.EVT_BUTTON, self.on_settings)
        self.ai_button.Bind(wx.EVT_BUTTON, self.on_ai)
        self.reset_exclusions.Bind(wx.EVT_BUTTON, self.on_reset_exclusions)
        self.mouser_button.Bind(wx.EVT_BUTTON, self.on_mouser)
        self.table.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_selection)
        self.table.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_associate)
        self.table.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_row_menu)
        self.Bind(wx.EVT_BUTTON, self.on_close, id=wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.paint_rows()
        self.export.Disable()
        self.build_button.Disable()
        if self.credential_error:
            self.status.SetLabel(self.credential_error + ' Enter the token again and save settings.')
        else:
            self.status.SetLabel('Saved associations restored. Availability has not been checked yet.')
        if self.inventory_token:
            wx.CallAfter(self.refresh_on_open)

    def refresh_on_open(self):
        if not self.closed and not self.busy:
            self.on_check(automatic=True)

    def paint_rows(self):
        self.table.DeleteAllItems()
        for i, line in enumerate(self.lines):
            result = self.results[i] if self.results else None
            association = identity(line, self.config)
            status = result.status if result else ('ASSOCIATED' if association else 'UNMAPPED')
            required = result.required if result else line.quantity * self.boards.GetValue()
            if not result and i in self.skips:
                status, required = 'SKIPPED', 0
            pick = '; '.join(f'{label(self.parts[pk])}: {qty}' for pk, qty in result.used.items()) if result else ''
            if not result and association and i not in self.skips:
                kind, code = association
                saved = next((m.get('part_label') for m in self.config.get('mappings', [])
                              if m['value'] == line.value and m['footprint'] == line.footprint), '')
                pick = (saved + ' | ' if saved else '') + f'{kind}: {code} (availability pending)'
            if result and not pick and result.primary:
                pick = label(result.primary)
            values = [status, line.refs, line.value, line.footprint, required,
                      sum(result.used.values()) if result else (0 if i in self.skips else '—'),
                      result.missing if result else (0 if i in self.skips else '—'), pick]
            index = self.table.InsertItem(i, str(values[0]))
            for column, value in enumerate(values[1:], 1):
                self.table.SetItem(index, column, str(value))
            if status in ('OK', 'OK_EQUIVALENT'):
                self.table.SetItemTextColour(index, wx.Colour(20, 110, 65))
            elif status in ('SHORTAGE', 'PARTIAL'):
                self.table.SetItemTextColour(index, wx.Colour(170, 60, 20))
            elif status == 'SKIPPED':
                self.table.SetItemTextColour(index, wx.Colour(115, 115, 115))

    def controls_config(self):
        return copy.deepcopy(self.config)

    def persist(self, config):
        save_secret(self.settings_file, credential_key('inventree', config['url']),
                    self.inventory_token if config.get('remember_token') else '')
        save_settings(self.settings_file, config)

    def on_settings(self, event=None, page=0):
        from .preferences import SettingsDialog
        session = {'inventree': self.inventory_token, 'ai': self.ai_key,
                   'mouser': self.mouser_key, 'mouser-search': self.mouser_search_key}
        dialog = SettingsDialog(self, self.config, self.settings_file, session, page)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return False
            config, keys = dialog.get_config(), dialog.get_keys()
            slots = [('inventree', config['url'], config.get('remember_token')),
                     ('ai', config['ai']['base_url'], config['ai']['remember_key']),
                     ('mouser', 'https://api.mouser.com', config['mouser']['remember_key']),
                     ('mouser-search', 'https://api.mouser.com', config['mouser']['remember_key'])]
            for service, url, remember in slots:
                save_secret(self.settings_file, credential_key(service, url), keys[service] if remember else '')
            save_settings(self.settings_file, config)
            refresh = config['url'] != self.config['url'] or config.get('related_as_equivalents') != self.config.get('related_as_equivalents') or keys['inventree'] != self.inventory_token
            self.config = config
            if refresh:
                self.results, self.exclusions = None, {}
                self.export.Disable()
                self.build_button.Disable()
                self.paint_rows()
            self.config = config
            self.inventory_token, self.ai_key = keys['inventree'], keys['ai']
            self.mouser_key, self.mouser_search_key = keys['mouser'], keys['mouser-search']
            self.status.SetLabel('Settings saved. Connections and credentials are configured under Settings.')
            if refresh and self.inventory_token:
                wx.CallAfter(self.refresh_on_open)
            return True
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot save settings', wx.OK | wx.ICON_ERROR)
            return False
        finally:
            for control in (dialog.token, dialog.ai_key, dialog.mouser_key, dialog.search_key):
                control.SetValue('')
            dialog.Destroy()

    def client(self, config):
        if not self.inventory_token:
            raise ValueError('Enter an InvenTree API token under Settings → InvenTree first.')
        return InvenTree(config['url'], self.inventory_token)

    def set_busy(self, busy, operation='Working', cancellable=False):
        self.busy = busy
        self.ai_cancel_event = threading.Event() if busy and cancellable else None
        self.cancel_activity.Show(busy and cancellable)
        if busy:
            self.activity_operation = operation
            self.activity_started = time.monotonic()
            self.activity_bar.Show()
            self.activity_text.Show()
            self.on_activity()
            self.activity_timer.Start(150)
        else:
            self.activity_timer.Stop()
            self.activity_bar.Hide()
            self.activity_text.Hide()
        self.Layout()
        for control in (self.run, self.import_button, self.associate, self.boards, self.settings_button, self.ai_button, self.reset_exclusions, self.mouser_button):
            control.Enable(not busy)
        self.export.Enable(not busy and self.results is not None)
        self.build_button.Enable(not busy and bool(self.results) and all(r.status in ('OK', 'OK_EQUIVALENT', 'EXCLUDED_AI', 'SKIPPED') for r in self.results))

    def on_cancel_ai(self, event=None):
        if self.ai_cancel_event is None:
            return
        self.ai_cancel_event.set()
        self.set_busy(False)
        self.status.SetLabel('AI operation cancelled. No proposals applied. An in-flight server request may finish; its result will be discarded.')

    def on_activity(self, event=None):
        if self.closed or not self.busy:
            return
        self.activity_bar.Pulse()
        elapsed = int(time.monotonic() - self.activity_started)
        self.activity_text.SetLabel(f'{self.activity_operation}… {elapsed // 60}:{elapsed % 60:02d} elapsed')

    def on_check(self, event=None, automatic=False):
        if self.closed or self.busy:
            return
        try:
            if not self.lines:
                raise ValueError('No components to check. Import a BOM or open a populated PCB.')
            config = self.controls_config()
            client = self.client(config)
            self.persist(config)
        except Exception as error:
            if automatic:
                self.status.SetLabel('Automatic availability check failed: ' + str(error))
            else:
                wx.MessageBox(str(error), 'Cannot start check', wx.OK | wx.ICON_ERROR)
            return
        boards = self.boards.GetValue()
        self.results = None
        self.config = config
        self.details.SetValue('')
        self.paint_rows()
        self.set_busy(True, 'Checking availability')
        self.status.SetLabel(f'Checking {len(self.lines)} BOM groups against InvenTree…')
        def done(value, error):
            if error:
                self.status.SetLabel('Availability check failed; saved associations remain. ' + error)
                self.set_busy(False)
                if not automatic:
                    wx.MessageBox(error, 'InvenTree request failed', wx.OK | wx.ICON_ERROR)
                return
            self.results, self.parts, self.stock = value
            self.checked_boards = boards
            self.paint_rows()
            if self.focus_row is not None and self.focus_row < len(self.lines):
                self.table.Select(self.focus_row)
                self.table.EnsureVisible(self.focus_row)
            counts = Counter(row.status for row in self.results)
            self.status.SetLabel(' | '.join(f'{state}: {count}' for state, count in sorted(counts.items())))
            self.set_busy(False)
        background(self, lambda: check(self.lines, client, config, boards, self.exclusions, self.skips), done)

    def on_import(self, event):
        with wx.FileDialog(self, 'Import a BOM', wildcard='BOM files (*.csv;*.html;*.xml)|*.csv;*.html;*.xml', style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            try:
                lines = read_bom(dialog.GetPath())
                if not lines:
                    raise ValueError('The BOM contains no populated components.')
                self.lines, self.source = lines, dialog.GetPath()
                self.exclusions = {}
                self.skips = skipped_rows(self.config, self.source, self.lines)
                self.focus_row = None
                self.source_label.SetLabel('Source: ' + self.source)
                self.results = None
                self.export.Disable()
                self.build_button.Disable()
                self.details.SetValue('')
                self.paint_rows()
                self.status.SetLabel('BOM loaded. Saved associations restored; availability pending.')
                if self.inventory_token:
                    self.on_check(automatic=True)
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
        if index in self.skips:
            self.status.SetLabel('This component is skipped. Right-click and choose Include component before associating it.')
            return
        try:
            config = self.controls_config()
            # Explicit design identifiers take precedence over value/package maps.
            key = identity(line, {**config, 'mappings': []})
            if key:
                raise ValueError('This row already has an explicit IPN/MPN. Correct that field in the design or export before associating by value and footprint.')
            can_remove = any(m['value'] == line.value and m['footprint'] == line.footprint for m in config.get('mappings', []))
            picker = PartPicker(self, self.client(config), line, can_remove=can_remove)
            try:
                outcome = picker.ShowModal()
                if outcome == wx.ID_DELETE:
                    self.on_remove_association()
                    return
                if outcome != wx.ID_OK:
                    return
                assign(config, line, picker.selected)
                save_settings(self.settings_file, config)
            finally:
                picker.closed = True
                picker.Destroy()
            self.config = config
            self.exclusions.pop(index, None)
            self.focus_row = index
            self.on_check()
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot associate part', wx.OK | wx.ICON_ERROR)

    def on_row_menu(self, event):
        if self.busy:
            return
        index = event.GetIndex()
        if not 0 <= index < len(self.lines):
            return
        self.table.Select(index)
        line = self.lines[index]
        explicit = identity(line, {**self.config, 'mappings': []})
        saved = any(m['value'] == line.value and m['footprint'] == line.footprint
                    for m in self.config.get('mappings', []))
        menu = wx.Menu()
        associate = menu.Append(wx.ID_ANY, 'Associate with an InvenTree part…')
        remove = menu.Append(wx.ID_ANY, 'Remove association')
        remove.Enable(saved and not explicit)
        associate.Enable(not explicit and index not in self.skips)
        menu.AppendSeparator()
        skip = menu.Append(wx.ID_ANY, 'Include component' if index in self.skips or index in self.exclusions else 'Skip component')
        menu.Bind(wx.EVT_MENU, self.on_associate, associate)
        menu.Bind(wx.EVT_MENU, self.on_remove_association, remove)
        menu.Bind(wx.EVT_MENU, self.on_toggle_skip, skip)
        try:
            self.PopupMenu(menu)
        finally:
            menu.Destroy()

    def on_toggle_skip(self, event=None):
        if self.busy:
            return
        index = self.table.GetFirstSelected()
        if not 0 <= index < len(self.lines):
            return
        try:
            skipping = index not in self.skips and index not in self.exclusions
            config = self.controls_config()
            set_component_skipped(config, self.source, self.lines[index], skipping)
            save_settings(self.settings_file, config)
            self.config = config
            if skipping:
                self.skips.add(index)
            else:
                self.skips.discard(index)
                self.exclusions.pop(index, None)
            self.results = None
            self.focus_row = index
            self.details.SetValue('Skipped components are omitted from stock checks, AI matching, build orders and Mouser carts. Saved associations are preserved.')
            self.paint_rows()
            self.table.Select(index)
            self.set_busy(False)
            self.status.SetLabel('Component skipped.' if skipping else 'Component included again.')
            if self.inventory_token:
                self.on_check(automatic=True)
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot update skipped component', wx.OK | wx.ICON_ERROR)

    def combined_exclusions(self):
        return {**self.exclusions, **{index: 'Skipped manually' for index in self.skips}}

    def on_remove_association(self, event=None):
        if self.busy:
            return
        index = self.table.GetFirstSelected()
        if not 0 <= index < len(self.lines):
            return
        line = self.lines[index]
        try:
            config = self.controls_config()
            if identity(line, {**config, 'mappings': []}):
                raise ValueError('This association comes from an IPN/MPN in the design. Edit that field in KiCad to remove it.')
            mappings = config.get('mappings', [])
            remaining = [m for m in mappings if (m['value'], m['footprint']) != (line.value, line.footprint)]
            if len(remaining) == len(mappings):
                return
            config['mappings'] = remaining
            save_settings(self.settings_file, config)
            self.config, self.results = config, None
            self.focus_row = index
            self.details.SetValue('Removed the saved association for this value and footprint, including other rows that share it.')
            self.paint_rows()
            self.table.Select(index)
            self.set_busy(False)
            self.status.SetLabel('Association removed from local memory. This component can be associated again manually or by AI.')
            if self.inventory_token:
                self.on_check(automatic=True)
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot remove association', wx.OK | wx.ICON_ERROR)

    def on_ai_settings(self, event=None):
        return self.on_settings(page=1)

    def on_ai(self, event=None):
        if self.busy:
            return
        if not any(not identity(line, self.config) and i not in self.combined_exclusions() for i, line in enumerate(self.lines)):
            self.status.SetLabel('No unassociated rows to review. Existing associations and exclusions were kept.')
            return
        if not self.config.get('ai', {}).get('base_url') and not self.on_ai_settings():
            return
        try:
            if not self.lines:
                raise ValueError('Load a BOM before running AI Assist.')
            config = self.controls_config()
            inventory = self.client(config)
            settings = config['ai']
            key = self.ai_key or load_secret(self.settings_file, credential_key('ai', settings['base_url']))
            ai = CompatibleAI(settings['base_url'], settings['model'], key, settings.get('json_mode', False), settings.get('protocol', 'openai'))
            boards = self.boards.GetValue()
            self.persist(config)
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot start AI Assist', wx.OK | wx.ICON_ERROR)
            return
        self.set_busy(True, 'AI Assist', cancellable=True)
        cancel_event = self.ai_cancel_event
        lines, exclusions = copy.deepcopy(self.lines), self.combined_exclusions()
        self.status.SetLabel('AI Assist is reviewing unassociated rows only. Existing associations are preserved.')
        def operation():
            proposals, catalog = analyze(lines, inventory, config, ai, boards, exclusions, cancel_event)
            for pk in {p.part_id for p in proposals if p.action == 'match'}:
                check_cancelled(cancel_event)
                try:
                    catalog[pk]['_available'] = str(inventory.available(pk))
                except Exception:
                    catalog[pk]['_available'] = 'Unknown'
            return proposals, catalog
        def done(value, error):
            if cancel_event.is_set():
                return
            self.set_busy(False)
            if error:
                self.status.SetLabel('AI Assist failed. No AI changes applied.')
                wx.MessageBox(error, 'AI Assist failed', wx.OK | wx.ICON_ERROR)
                return
            from .ai_dialogs import AIReview
            proposals, catalog = value
            review = AIReview(self, self.lines, proposals, catalog)
            try:
                if review.ShowModal() != wx.ID_OK:
                    self.status.SetLabel('AI proposals dismissed. No AI changes applied.')
                    return
                updated, exclusions = apply_proposals(self.lines, config, self.exclusions, review.selected(), catalog)
                save_settings(self.settings_file, updated)
                self.config, self.exclusions = updated, exclusions
                self.on_check()
            except Exception as failure:
                wx.MessageBox(str(failure), 'Cannot apply AI proposals', wx.OK | wx.ICON_ERROR)
            finally:
                review.Destroy()
        background(self, operation, done)

    def on_reset_exclusions(self, event):
        self.exclusions = {}
        self.on_check()

    def on_mouser(self, event=None):
        if self.busy:
            return
        if not self.mouser_key and not self.on_settings(page=2):
            return
        if not self.config.get('ai', {}).get('base_url') and not self.on_settings(page=1):
            return
        try:
            if not self.lines:
                raise ValueError('Load a BOM first.')
            settings = self.config.get('mouser', {})
            mouser = Mouser(self.mouser_key, self.mouser_search_key, settings.get('country', 'IT'), settings.get('currency', 'EUR'))
            settings = self.config['ai']
            ai = CompatibleAI(settings['base_url'], settings['model'], self.ai_key, settings.get('json_mode', False), settings.get('protocol', 'openai'))
            boards = self.boards.GetValue()
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot prepare Mouser cart', wx.OK | wx.ICON_ERROR)
            return
        self.set_busy(True, 'Preparing Mouser cart', cancellable=True)
        cancel_event = self.ai_cancel_event
        lines, exclusions = copy.deepcopy(self.lines), self.combined_exclusions()
        self.status.SetLabel('Searching Mouser and asking the configured AI to select verified candidates. No cart has been created yet.')
        def done(rows, error):
            if cancel_event.is_set():
                return
            self.set_busy(False)
            if error:
                self.status.SetLabel('Mouser preparation failed. No cart was created.')
                wx.MessageBox(error, 'Mouser cart', wx.OK | wx.ICON_ERROR)
                return
            from .cart_dialog import CartDialog
            dialog = CartDialog(self, mouser, rows, self.settings_file.parent / 'carts')
            try:
                dialog.ShowModal()
            finally:
                dialog.closed = True
                dialog.Destroy()
        background(self, lambda: prepare_cart(lines, exclusions, boards, mouser, ai, cancel_event), done)

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

    def on_build(self, event):
        from .build_dialog import BuildDialog
        try:
            config = self.controls_config()
            if not self.results or self.boards.GetValue() != self.checked_boards or config['url'] != self.config['url']:
                raise ValueError('Refresh availability for the current server and board quantity before creating a build.')
            dialog = BuildDialog(self, self.client(config), self.results, self.checked_boards, self.source)
            try:
                dialog.ShowModal()
                created = dialog.created
            finally:
                dialog.closed = True
                dialog.Destroy()
            if created:
                self.on_check()
        except Exception as error:
            wx.MessageBox(str(error), 'Cannot open build order', wx.OK | wx.ICON_ERROR)

    def on_close(self, event):
        self.closed = True
        if self.ai_cancel_event is not None:
            self.ai_cancel_event.set()
        self.activity_timer.Stop()
        self.inventory_token = ''
        self.mouser_key = ''
        self.mouser_search_key = ''
        self.ai_key = ''
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
