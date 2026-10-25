"""Run with KiCad's bundled Python. Creates hidden dialogs; never uses the network."""
import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pcbnew
import wx

from bomcheck.kicad.board import read_board
from bomcheck.kicad.action import InvenTreeBomChecker
from bomcheck.kicad.gui import CheckerDialog, PartPicker
from examples.demo import DemoInventory
from tools.build_plugin import build
from bomcheck.ai import Proposal
from bomcheck.kicad.ai_dialogs import AISettings, AIReview
from bomcheck.kicad.build_dialog import BuildDialog
from bomcheck.inputs import Line
from bomcheck.kicad.preferences import SettingsDialog
from bomcheck.kicad.cart_dialog import CartDialog
from bomcheck.mouser import CartRow
from bomcheck.version import __version__


def main():
    app = wx.App(False)
    board = pcbnew.BOARD()
    for ref, dnp, excluded in [('R1', False, False), ('R2', False, False), ('R3', True, False), ('R4', False, True)]:
        footprint = pcbnew.FOOTPRINT(board)
        footprint.SetReference(ref)
        footprint.SetValue('10k')
        footprint.SetFPIDAsString('Resistor_SMD:R_0603_1608Metric')
        footprint.SetField('IPN', 'P1')
        footprint.SetDNP(dnp)
        footprint.SetExcludedFromBOM(excluded)
        board.Add(footprint)
    lines = read_board(board)
    assert len(lines) == 1 and lines[0].quantity == 2, lines
    assert lines[0].fields['IPN'] == 'P1'
    assert sorted(lines[0].refs.split(', ')) == ['R1', 'R2']
    # KiCad embeds wx.AppConsole, which has no GetTopWindow. Exercise Run,
    # not just dialog construction, with both a native frame and no frame.
    frame = wx.Frame(None, name='PcbFrame')
    for parent in (frame, None):
        with patch.object(pcbnew, 'GetBoard', return_value=board), \
             patch('bomcheck.kicad.action.wx.GetApp', return_value=object()) as get_app, \
             patch('bomcheck.kicad.action.wx.FindWindowByName', return_value=parent), \
             patch('bomcheck.kicad.gui.CheckerDialog') as dialog_class, \
             patch('bomcheck.kicad.action.wx.MessageBox') as message:
            InvenTreeBomChecker().Run()
            assert dialog_class.call_args.args[0] is parent
            dialog_class.return_value.ShowModal.assert_called_once()
            dialog_class.return_value.Destroy.assert_called_once()
            get_app.assert_not_called()
            message.assert_not_called()
    frame.Destroy()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        dialog = CheckerDialog(None, lines, 'test.kicad_pcb', root / 'settings.json')
        assert __version__ in dialog.GetTitle()
        assert not dialog.activity_bar.IsShown()
        dialog.Layout()
        idle_table_rect = dialog.table.GetRect()
        idle_details_rect = dialog.details.GetRect()
        dialog.set_busy(True)
        assert dialog.table.GetRect() == idle_table_rect
        assert dialog.details.GetRect() == idle_details_rect
        assert dialog.activity_bar.IsShown() and dialog.activity_timer.IsRunning()
        with patch('bomcheck.kicad.gui.time.monotonic', return_value=dialog.activity_started + 65):
            dialog.on_activity()
        assert '1:05 elapsed' in dialog.activity_text.GetLabel()
        dialog.set_busy(False)
        assert not dialog.activity_bar.IsShown() and not dialog.activity_timer.IsRunning()
        assert dialog.table.GetRect() == idle_table_rect
        assert dialog.details.GetRect() == idle_details_rect
        dialog.inventory_token = 'test-token-never-persist'
        dialog.config['remember_token'] = True
        assert dialog.table.GetItemText(0) == 'ASSOCIATED'
        def immediate(window, operation, finished):
            try:
                result = operation()
            except Exception as error:
                finished(None, str(error))
            else:
                finished(result, None)
        with patch('bomcheck.kicad.gui.InvenTree', return_value=DemoInventory()), \
             patch('bomcheck.kicad.gui.background') as queued_check:
            dialog.on_check()
            assert dialog.activity_timer.IsRunning() and dialog.activity_bar.IsShown()
            assert 'Checking availability' in dialog.activity_text.GetLabel()
            window, operation, finished = queued_check.call_args.args
            immediate(window, operation, finished)
            assert not dialog.activity_timer.IsRunning() and not dialog.activity_bar.IsShown()
        with patch('bomcheck.kicad.gui.InvenTree', return_value=DemoInventory()), patch('bomcheck.kicad.gui.background', side_effect=immediate):
            dialog.on_check()
        assert dialog.table.GetItemText(0) == 'OK_EQUIVALENT', dialog.table.GetItemText(0)
        assert dialog.export.IsEnabled()
        assert 'test-token-never-persist' not in (root / 'settings.json').read_text()
        assert 'test-token-never-persist' not in (root / 'credentials.json').read_text()
        reopened = CheckerDialog(None, lines, 'test.kicad_pcb', root / 'settings.json')
        assert reopened.inventory_token == 'test-token-never-persist'
        reopened.closed = True
        reopened.Destroy()
        # Exercise part search UI, including inventories with no IPN.
        inv = DemoInventory()
        inv.list = lambda *args, **kwargs: list(inv.parts.values())
        picker = PartPicker(dialog, inv, lines[0])
        with patch('bomcheck.kicad.gui.background', side_effect=immediate):
            picker.on_search()
        assert picker.list.GetItemCount() == 5
        assert picker.list.GetItemText(0, 3) == '1'
        assert picker.list.GetItemText(4, 3) == '0'
        picker.closed = True
        picker.Destroy()
        # A failed stock lookup must remain Unknown rather than becoming zero.
        inv.available = lambda pk: (_ for _ in ()).throw(ValueError('Forbidden'))
        picker = PartPicker(dialog, inv, lines[0])
        with patch('bomcheck.kicad.gui.background', side_effect=immediate):
            picker.on_search()
        assert picker.list.GetItemText(0, 3) == 'Unknown'
        picker.closed = True
        picker.Destroy()
        # Confirming a part automatically runs availability, without a second click.
        lines[0].fields = {}
        dialog.table.Select(0)
        inv = DemoInventory()
        original_find = inv.find
        inv.find = lambda kind, code: [inv.parts[int(code)]] if kind == 'part_id' else original_find(kind, code)
        picked = MagicMock()
        picked.selected = inv.parts[1]
        picked.ShowModal.return_value = wx.ID_OK
        with patch('bomcheck.kicad.gui.PartPicker', return_value=picked), \
             patch('bomcheck.kicad.gui.InvenTree', return_value=inv), \
             patch('bomcheck.kicad.gui.background', side_effect=immediate):
            dialog.on_associate(None)
        assert dialog.table.GetItemText(0) == 'OK_EQUIVALENT'
        assert dialog.config['mappings'][0]['part_id'] == 1
        restored = CheckerDialog(None, lines, 'test.kicad_pcb', root / 'settings.json')
        assert restored.table.GetItemText(0) == 'ASSOCIATED'
        assert 'Resistor 10k' in restored.table.GetItemText(0, 7)
        with patch('bomcheck.kicad.gui.InvenTree', return_value=inv), \
             patch('bomcheck.kicad.gui.background', side_effect=immediate):
            wx.Yield()  # Startup refresh runs without clicking Check availability.
        assert restored.table.GetItemText(0) == 'OK_EQUIVALENT'
        with patch('bomcheck.kicad.gui.check', side_effect=ValueError('Server offline')), \
             patch('bomcheck.kicad.gui.background', side_effect=immediate), \
             patch('bomcheck.kicad.gui.wx.MessageBox') as popup:
            restored.on_check(automatic=True)
        popup.assert_not_called()
        assert restored.table.GetItemText(0) == 'ASSOCIATED'
        assert restored.config['mappings'][0]['part_id'] == 1
        with patch('bomcheck.kicad.gui.analyze') as no_ai:
            restored.on_ai()
            no_ai.assert_not_called()
        restored.table.Select(0)
        row_event = MagicMock()
        row_event.GetIndex.return_value = 0
        def inspect_menu(menu):
            remove = next(item for item in menu.GetMenuItems() if item.GetItemLabelText() == 'Remove association')
            assert remove.IsEnabled()
        with patch.object(restored, 'PopupMenu', side_effect=inspect_menu):
            restored.on_row_menu(row_event)
        removal_picker = PartPicker(restored, inv, lines[0], can_remove=True)
        with patch.object(removal_picker, 'EndModal') as end_modal:
            removal_picker.on_remove(None)
            end_modal.assert_called_once_with(wx.ID_DELETE)
        removal_picker.Destroy()
        restored.config['mappings'].append({'value': 'Unrelated', 'footprint': 'Other', 'part_id': 5})
        restored.inventory_token = ''
        removed = MagicMock()
        removed.ShowModal.return_value = wx.ID_DELETE
        with patch('bomcheck.kicad.gui.PartPicker', return_value=removed) as picker_class, \
             patch.object(restored, 'client', return_value=inv):
            restored.on_associate(None)
        assert picker_class.call_args.kwargs['can_remove']
        assert restored.table.GetItemText(0) == 'UNMAPPED'
        saved_mappings = __import__('json').loads((root / 'settings.json').read_text())['mappings']
        assert saved_mappings == [{'value': 'Unrelated', 'footprint': 'Other', 'part_id': 5}]
        assert not restored.export.IsEnabled() and not restored.build_button.IsEnabled()
        restored.closed = True
        restored.Destroy()
        settings_dialog = AISettings(dialog, {}, root / 'settings.json')
        assert settings_dialog.get_config()['base_url'] == ''
        settings_dialog.Destroy()
        preferences = SettingsDialog(dialog, dialog.config, root / 'settings.json', {'inventree': 'session-token'})
        assert preferences.book.GetPageText(1) == 'AI endpoint'
        assert preferences.book.GetPageText(2) == 'Mouser'
        preferences.protocol.SetSelection(1)
        assert preferences.get_config()['ai']['protocol'] == 'ollama'
        assert preferences.get_keys()['inventree'] == 'session-token'
        preferences.ai_url.SetValue('http://localhost:11434')
        preferences.model.SetValue('ministral-3:3b')
        client = MagicMock()
        client.model = 'ministral-3:3b'
        client.complete.return_value = {'ok': True}
        pending = []
        def queue_test(target, daemon):
            pending.append(target)
            return MagicMock()
        with patch('bomcheck.kicad.preferences.CompatibleAI', return_value=client), \
             patch('bomcheck.kicad.preferences.threading.Thread', side_effect=queue_test):
            preferences.on_test_ai(None)
            assert not preferences.test_button.IsEnabled()
            preferences.on_test_ai(None)
            assert len(pending) == 1
            pending.pop()()
            wx.Yield()
            assert 'Connection successful' in preferences.test_status.GetLabel()
            assert preferences.test_button.IsEnabled()
            client.complete.return_value = {'ok': False}
            preferences.on_test_ai(None)
            pending.pop()()
            wx.Yield()
            assert 'expected JSON' in preferences.test_status.GetLabel()
            client.complete.side_effect = ValueError('AI endpoint is unreachable or timed out.')
            preferences.on_test_ai(None)
            pending.pop()()
            wx.Yield()
            assert 'unreachable' in preferences.test_status.GetLabel()
        preferences.Destroy()
        proposals = [Proposal(0, 'match', 1, 0.95, 'Exact match')]
        review = AIReview(dialog, lines, proposals, inv.parts)
        assert review.selected() == proposals
        review.Destroy()
        # Exercise the AI apply path and its automatic refresh without a model call.
        dialog.lines = [Line('R1, R2', '10k', 'R0603', 2, {}), Line('LOGO1', 'Logo', 'Artwork', 1, {})]
        dialog.config['mappings'] = []
        dialog.config['ai'] = {'base_url': 'http://localhost:1234/v1', 'model': 'smoke-test'}
        proposals = [Proposal(0, 'match', 1, 0.95, 'Verified resistor'), Proposal(1, 'exclude', None, 0.99, 'Artwork')]
        ai_review = MagicMock()
        ai_review.ShowModal.return_value = wx.ID_OK
        ai_review.selected.return_value = proposals
        with patch('bomcheck.kicad.gui.background') as queued_ai, \
             patch('bomcheck.kicad.gui.InvenTree', return_value=inv), \
             patch('bomcheck.kicad.ai_dialogs.AIReview') as cancelled_review:
            dialog.on_ai()
            cancelled_event = dialog.ai_cancel_event
            assert dialog.cancel_activity.IsShown()
            late_done = queued_ai.call_args.args[2]
            dialog.on_cancel_ai()
            assert cancelled_event.is_set() and not dialog.busy
            assert not dialog.cancel_activity.IsShown()
            dialog.set_busy(True, 'Checking availability')
            late_done((proposals, inv.parts), None)
            late_done(None, 'A late network error')
            assert dialog.busy  # The old response must not finish the new operation.
            cancelled_review.assert_not_called()
            assert dialog.config['mappings'] == []
            dialog.set_busy(False)
        with patch('bomcheck.kicad.gui.analyze', return_value=(proposals, inv.parts)), \
             patch('bomcheck.kicad.ai_dialogs.AIReview', return_value=ai_review), \
             patch('bomcheck.kicad.gui.InvenTree', return_value=inv), \
             patch('bomcheck.kicad.gui.background', side_effect=immediate):
            dialog.on_ai()
        assert dialog.results[1].status == 'EXCLUDED_AI'
        assert dialog.results[0].status == 'OK_EQUIVALENT'
        # Build preview and allocation use a fake server, never a real build.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
        from test_builds import Server, results as build_results
        server = Server()
        build_dialog = BuildDialog(dialog, server, build_results(server), 2, 'test.kicad_pcb')
        build_dialog.assembly, build_dialog.pcb = server.parts[1], server.parts[3]
        build_dialog.lots = [server.lots[30]]
        build_dialog.stock.InsertItem(0, '30')
        build_dialog.stock.Select(0)
        build_dialog.reference.SetValue('BO-001')
        with patch('bomcheck.kicad.gui.background', side_effect=immediate), patch('bomcheck.kicad.build_dialog.webbrowser.open'):
            build_dialog.on_prepare()
            assert build_dialog.create_button.IsEnabled()
            assert 'stock lot: #30' in build_dialog.preview.GetValue()
            build_dialog.on_create(None)
        assert build_dialog.created['pk'] == 80
        build_dialog.closed = True
        build_dialog.Destroy()
        cart_api = MagicMock()
        cart_api.create_cart.return_value = {'CartKey': 'c0f9eb83-7881-4faa-b663-320710b25a11', 'CartItems': []}
        cart_rows = [CartRow(0, 'R1', '10k', 'R0603', 2, 2, '123-RES10K', 'RES10K', 0.99, 'Verified', 100)]
        cart_dialog = CartDialog(dialog, cart_api, cart_rows, root / 'carts')
        with patch('bomcheck.kicad.gui.background', side_effect=immediate):
            cart_dialog.on_create(None)
            cart_dialog.on_create(None)
        cart_api.create_cart.assert_called_once()
        assert cart_dialog.cart_key == cart_api.create_cart.return_value['CartKey']
        assert list((root / 'carts').glob('*.json'))
        cart_dialog.closed = True
        cart_dialog.Destroy()
        dialog.closed = True
        dialog.Destroy()
        # Verify the package registers under its installed relative namespace.
        package = root / 'inventree_bom_checker'
        with ZipFile(build()) as archive:
            for name in archive.namelist():
                if name.startswith('plugins/'):
                    target = package / name[len('plugins/'):]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        spec = importlib.util.spec_from_file_location('inventree_bom_checker', package / '__init__.py', submodule_search_locations=[str(package)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        # Native registration requires the PCB editor's application singleton.
        # Standalone Python can verify the packaged registration call and class.
        with patch.object(pcbnew.PYTHON_ACTION_PLUGINS, 'register_action') as register:
            spec.loader.exec_module(module)
            register.assert_called_once_with(module.plugin)
        assert module.plugin.name == f'InvenTree BoM Checker {__version__}'
        assert Path(module.plugin.icon_file_name).is_file()
        # The queued initial search must see the closed flag before doing work.
        wx.Yield()
    app.Destroy()
    print('KiCad', pcbnew.GetBuildVersion(), ': board snapshot, DNP/BOM flags, hidden wx UI, stock check, part search and packaged import passed. Native registration hook mocked outside the PCB editor.')


if __name__ == '__main__':
    main()
