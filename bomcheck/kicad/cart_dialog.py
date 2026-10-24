"""Explicit review and cart-only submission. No order operation is available."""
import json
import webbrowser
from collections import defaultdict
from pathlib import Path

import wx

from ..mouser import CartError


class CartDialog(wx.Dialog):
    def __init__(self, parent, mouser, rows, receipt_folder):
        super().__init__(parent, title='Review Mouser cart — no order submission', size=(1180, 650), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.closed, self.creating, self.attempted = False, False, False
        self.mouser, self.rows, self.receipt_folder = mouser, rows, Path(receipt_folder)
        self.cart_key = ''
        layout = wx.BoxSizer(wx.VERTICAL)
        note = wx.StaticText(self, label='Review verified Mouser candidates before creating a new cart. Quantities cover the full included project BOM, even for parts already in your warehouse. MOQ/multiple increases are shown. Unresolved rows are not added. No checkout, payment or order is submitted.')
        note.Wrap(1100)
        layout.Add(note, 0, wx.EXPAND | wx.ALL, 14)
        self.table = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.table.EnableCheckBoxes()
        for name, width in [('References', 120), ('Value / footprint', 190), ('Required', 70), ('Cart qty', 70), ('Mouser SKU', 180), ('Manufacturer PN', 180), ('Stock', 80), ('Confidence', 80), ('Reason', 350)]:
            self.table.AppendColumn(name, width=width)
        for i, row in enumerate(rows):
            values = [row.refs, f'{row.value} / {row.footprint}', row.requested, row.quantity or '—', row.sku or 'UNRESOLVED', row.mpn, row.stock if row.sku else '—', f'{row.confidence:.0%}', row.reason]
            self.table.InsertItem(i, str(values[0]))
            for column, value in enumerate(values[1:], 1):
                self.table.SetItem(i, column, str(value))
            self.table.CheckItem(i, bool(row.sku) and row.confidence >= 0.9)
        layout.Add(self.table, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        unresolved = sum(not row.sku for row in rows)
        self.status = wx.StaticText(self, label=f'{len(rows) - unresolved} matched rows; {unresolved} unresolved. Only checked rows will be added.')
        layout.Add(self.status, 0, wx.EXPAND | wx.ALL, 14)
        self.key_text = wx.TextCtrl(self, style=wx.TE_READONLY)
        layout.Add(self.key_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.create_button = wx.Button(self, label='Create new cart — do not order')
        self.open_button = wx.Button(self, label='Open Mouser website')
        self.close_button = wx.Button(self, wx.ID_CANCEL, 'Close')
        for button in (self.create_button, self.open_button, self.close_button):
            bar.Add(button, 0, wx.RIGHT, 10)
        layout.Add(bar, 0, wx.ALL, 14)
        self.SetSizer(layout)
        self.create_button.Bind(wx.EVT_BUTTON, self.on_create)
        self.open_button.Bind(wx.EVT_BUTTON, lambda event: webbrowser.open('https://www.mouser.com/'))
        self.close_button.Bind(wx.EVT_BUTTON, self.on_close)
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def selected_items(self):
        selected = []
        totals, stock = defaultdict(int), {}
        for i, row in enumerate(self.rows):
            if not self.table.IsItemChecked(i):
                continue
            if not row.sku:
                raise ValueError('Uncheck unresolved rows before creating the cart.')
            totals[row.sku] += row.quantity
            stock[row.sku] = min(stock.get(row.sku, row.stock), row.stock)
            selected.append({'MouserPartNumber': row.sku, 'Quantity': row.quantity})
        if not selected:
            raise ValueError('Check at least one verified Mouser part.')
        if any(quantity > stock[sku] for sku, quantity in totals.items()):
            raise ValueError('Combined rows exceed the reported stock of a shared Mouser SKU. Adjust the selection before creating a cart.')
        return selected

    def on_create(self, event):
        from .gui import background
        if self.attempted:
            return
        try:
            items = self.selected_items()
        except ValueError as error:
            wx.MessageBox(str(error), 'Mouser cart', wx.OK | wx.ICON_ERROR)
            return
        self.attempted, self.creating = True, True
        self.create_button.Disable()
        self.close_button.Disable()
        self.table.Disable()
        self.status.SetLabel('Creating a new Mouser cart. No order will be submitted. Do not retry while this request is running.')
        def operation():
            try:
                return self.mouser.create_cart(items), None
            except CartError as error:
                return None, error
        def done(value, error):
            self.creating = False
            self.close_button.Enable()
            if error:
                self.status.SetLabel('Cart creation could not be confirmed. Check Mouser before creating another cart.')
                wx.MessageBox(error, 'Mouser cart', wx.OK | wx.ICON_ERROR)
                return
            response, failure = value
            if failure:
                self.cart_key = failure.cart_key or ''
                self.key_text.SetValue(self.cart_key)
                self.status.SetLabel(str(failure))
                wx.MessageBox(str(failure), 'Mouser cart needs review', wx.OK | wx.ICON_ERROR)
                return
            self.cart_key = response['CartKey']
            self.key_text.SetValue('Cart key: ' + self.cart_key)
            try:
                self.receipt_folder.mkdir(parents=True, exist_ok=True)
                path = self.receipt_folder / (self.cart_key + '.json')
                path.write_text(json.dumps(response, indent=2), encoding='utf-8')
                self.status.SetLabel(f'Cart created; no order submitted. Receipt saved to {path}. The API cart is identified by the key below.')
            except OSError:
                self.status.SetLabel('Cart created; no order submitted. Copy the key below; the local receipt could not be saved.')
        background(self, operation, done)

    def on_close(self, event):
        if self.creating:
            if hasattr(event, 'Veto'):
                event.Veto()
            return
        self.closed = True
        self.EndModal(wx.ID_CANCEL)
