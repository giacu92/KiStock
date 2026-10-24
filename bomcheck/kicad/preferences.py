"""All plugin configuration lives in one native Settings dialog."""
import copy
import re
import threading
import time

import wx

from ..ai import CompatibleAI
from ..inventree import InvenTree
from .credentials import credential_key, load_secret


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, config, settings_file, session, page=0):
        super().__init__(parent, title='Settings', size=(780, 590), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.original = copy.deepcopy(config)
        self.settings_file = settings_file
        book = wx.Notebook(self)
        self.book = book
        inventory = wx.Panel(book)
        ai = wx.Panel(book)
        mouser = wx.Panel(book)
        book.AddPage(inventory, 'InvenTree')
        book.AddPage(ai, 'AI endpoint')
        book.AddPage(mouser, 'Mouser')
        self.url = wx.TextCtrl(inventory, value=config['url'])
        self.token = wx.TextCtrl(inventory, value=session.get('inventree', ''), style=wx.TE_PASSWORD)
        self.remember = wx.CheckBox(inventory, label='Remember token using Windows account encryption')
        self.remember.SetValue(config.get('remember_token', False))
        self.related = wx.CheckBox(inventory, label='Treat direct related parts as approved equivalents')
        self.related.SetValue(config.get('related_as_equivalents', True))
        self.panel(inventory, [('Server URL', self.url), ('API token', self.token)], [self.remember, self.related],
                   'Stock checks are read-only. The build-order button writes records only after you review and create a build.')
        settings = config.get('ai', {})
        self.ai_url = wx.TextCtrl(ai, value=settings.get('base_url', ''))
        self.protocol = wx.Choice(ai, choices=['OpenAI-compatible', 'Ollama native'])
        self.protocol.SetSelection(1 if settings.get('protocol') == 'ollama' else 0)
        self.model = wx.TextCtrl(ai, value=settings.get('model', ''))
        self.ai_key = wx.TextCtrl(ai, value=session.get('ai', ''), style=wx.TE_PASSWORD)
        self.ai_remember = wx.CheckBox(ai, label='Remember AI key using Windows account encryption')
        self.ai_remember.SetValue(settings.get('remember_key', False))
        self.json_mode = wx.CheckBox(ai, label='Request JSON mode (if supported by the endpoint)')
        self.json_mode.SetValue(settings.get('json_mode', False))
        self.test_button = wx.Button(ai, label='Test connection')
        self.test_status = wx.StaticText(ai, label='Test the current endpoint and model without saving settings.')
        self.test_button.Bind(wx.EVT_BUTTON, self.on_test_ai)
        self.panel(ai, [('API protocol', self.protocol), ('API base URL', self.ai_url), ('Model name', self.model), ('API key (optional for local servers)', self.ai_key)],
                   [self.ai_remember, self.json_mode, self.test_button, self.test_status], 'OpenAI-compatible example: http://localhost:11434/v1. Ollama native example: http://localhost:11434. Enter an installed model name. AI Assist sends BOM/inventory metadata; Mouser sends BOM/search candidates. API keys are never included in model messages.')
        settings = config.get('mouser', {})
        self.mouser_key = wx.TextCtrl(mouser, value=session.get('mouser', ''), style=wx.TE_PASSWORD)
        self.search_key = wx.TextCtrl(mouser, value=session.get('mouser-search', ''), style=wx.TE_PASSWORD)
        self.mouser_remember = wx.CheckBox(mouser, label='Remember Mouser keys using Windows account encryption')
        self.mouser_remember.SetValue(settings.get('remember_key', False))
        self.country = wx.TextCtrl(mouser, value=settings.get('country', 'IT'))
        self.currency = wx.TextCtrl(mouser, value=settings.get('currency', 'EUR'))
        self.panel(mouser, [('Cart API key', self.mouser_key), ('Search API key (blank = use Cart key)', self.search_key), ('Country code', self.country), ('Currency code', self.currency)],
                   [self.mouser_remember], 'The key needs Cart API access and Search API access (or a separate Search key). Only a new cart is created. There is no checkout, payment or order-submission operation.')
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(book, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.FindWindowById(wx.ID_OK).SetLabel('Save settings')
        self.Bind(wx.EVT_BUTTON, self.on_save, id=wx.ID_OK)
        self.url.Bind(wx.EVT_KILL_FOCUS, lambda event: self.changed_url(event, 'inventree', self.url, self.token))
        self.ai_url.Bind(wx.EVT_KILL_FOCUS, lambda event: self.changed_url(event, 'ai', self.ai_url, self.ai_key))
        self.last_urls = {'inventree': config['url'].rstrip('/'), 'ai': config.get('ai', {}).get('base_url', '').rstrip('/')}
        book.SetSelection(page)

    def panel(self, panel, fields, options, note):
        layout = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, hgap=12, vgap=12)
        grid.AddGrowableCol(1)
        for label, control in fields:
            grid.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(control, 1, wx.EXPAND)
        layout.Add(grid, 0, wx.EXPAND | wx.ALL, 16)
        for option in options:
            layout.Add(option, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)
        text = wx.StaticText(panel, label=note)
        text.Wrap(670)
        layout.Add(text, 0, wx.ALL, 16)
        panel.SetSizer(layout)

    def changed_url(self, event, service, control, secret):
        url = control.GetValue().strip().rstrip('/')
        if url != self.last_urls[service]:
            try:
                secret.SetValue(load_secret(self.settings_file, credential_key(service, url)))
            except Exception:
                secret.SetValue('')
            self.last_urls[service] = url
        event.Skip()

    def on_test_ai(self, event):
        if not self.test_button.IsEnabled():
            return
        try:
            client = CompatibleAI(self.ai_url.GetValue(), self.model.GetValue(), self.ai_key.GetValue(),
                                  self.json_mode.GetValue(), 'ollama' if self.protocol.GetSelection() == 1 else 'openai')
        except ValueError as error:
            self.test_status.SetLabel(str(error))
            self.test_status.Wrap(650)
            self.book.GetPage(1).Layout()
            return
        self.test_button.Disable()
        self.test_status.SetLabel('Testing model response... First load can take up to 120 seconds.')
        self.book.GetPage(1).Layout()

        def finish(message):
            if not self:
                return
            self.test_button.Enable()
            self.test_status.SetLabel(message)
            self.test_status.Wrap(650)
            self.book.GetPage(1).Layout()

        def work():
            started = time.monotonic()
            try:
                result = client.complete({'test': 'connection'}, system='This is a connection test. Return only the JSON object {"ok": true}.')
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise ValueError('Model responded, but did not return the expected JSON object {"ok": true}.')
                message = f'Connection successful: {client.model} returned valid JSON in {time.monotonic() - started:.1f}s.'
            except Exception as error:
                # Transport errors are sanitized by CompatibleAI; never display raw credentials.
                message = str(error) if isinstance(error, ValueError) else 'Connection test failed. Check the server and model.'
            wx.CallAfter(finish, message)

        threading.Thread(target=work, daemon=True).start()

    def get_config(self):
        config = copy.deepcopy(self.original)
        url = self.url.GetValue().strip().rstrip('/')
        if url != config['url'].rstrip('/'):
            config['mappings'] = []
        config.update(url=url, remember_token=self.remember.GetValue(), related_as_equivalents=self.related.GetValue())
        config['ai'] = {'base_url': self.ai_url.GetValue().strip().rstrip('/'), 'model': self.model.GetValue().strip(),
                        'remember_key': self.ai_remember.GetValue(), 'json_mode': self.json_mode.GetValue(), 'protocol': 'ollama' if self.protocol.GetSelection() == 1 else 'openai'}
        config['mouser'] = {'country': self.country.GetValue().strip().upper(), 'currency': self.currency.GetValue().strip().upper(),
                            'remember_key': self.mouser_remember.GetValue()}
        return config

    def get_keys(self):
        return {'inventree': self.token.GetValue().strip(), 'ai': self.ai_key.GetValue().strip(),
                'mouser': self.mouser_key.GetValue().strip(), 'mouser-search': self.search_key.GetValue().strip()}

    def on_save(self, event):
        try:
            config = self.get_config()
            InvenTree(config['url'], '')
            if config['ai']['base_url'] or config['ai']['model']:
                CompatibleAI(config['ai']['base_url'], config['ai']['model'], protocol=config['ai']['protocol'])
            if not re.fullmatch('[A-Z]{2}', config['mouser']['country']) or not re.fullmatch('[A-Z]{3}', config['mouser']['currency']):
                raise ValueError('Use a two-letter country code and a three-letter currency code.')
            self.EndModal(wx.ID_OK)
        except ValueError as error:
            wx.MessageBox(str(error), 'Invalid settings', wx.OK | wx.ICON_ERROR)
