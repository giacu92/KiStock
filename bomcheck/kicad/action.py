from pathlib import Path
import pcbnew
import wx
from ..version import __version__


class InvenTreeBomChecker(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = f'KiStock {__version__}'
        self.category = 'Bill of Materials'
        self.description = 'Check InvenTree stock and substitutes for the open PCB'
        self.show_toolbar_button = True
        self.icon_file_name = str(Path(__file__).with_name('icon.png'))

    def Run(self):
        from .gui import CheckerDialog
        from .board import read_board
        try:
            board = pcbnew.GetBoard()
            lines = read_board(board)
            if not lines:
                wx.MessageBox('The PCB has no populated components. You can import a BOM in the plugin window.', self.name)
            # Embedded KiCad may expose wx.AppConsole rather than wx.App.
            # Find the native frame directly; a parentless dialog is also valid.
            dialog = CheckerDialog(wx.FindWindowByName('PcbFrame'), lines, board.GetFileName())
            try:
                dialog.ShowModal()
            finally:
                dialog.Destroy()
        except Exception as error:
            wx.MessageBox(str(error), self.name, wx.OK | wx.ICON_ERROR)
