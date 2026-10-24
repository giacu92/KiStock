"""Loaded by KiCad's Python Action Plugin discovery."""
from .bomcheck.kicad.action import InvenTreeBomChecker

plugin = InvenTreeBomChecker()
plugin.register()
