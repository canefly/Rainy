"""Rainy Native Desktop UI Module.
Globally neutralizes QFluentWidgets MaskDialogBase opacity fade animations
to provide instantaneous, high-refresh-rate window responsiveness and ensure
child widget hit-testing and click interactions are 100% reliable.
"""
from PySide6.QtWidgets import QDialog
from qfluentwidgets.components.dialog_box.mask_dialog_base import MaskDialogBase

def _global_mask_dialog_show_event(self, e):
    self.setGraphicsEffect(None)
    if self.parentWidget():
        self.resize(self.parentWidget().size())
    QDialog.showEvent(self, e)

def _global_mask_dialog_done(self, code):
    self.setGraphicsEffect(None)
    if hasattr(self, "widget") and self.widget:
        self.widget.setGraphicsEffect(None)
    QDialog.done(self, code)

MaskDialogBase.showEvent = _global_mask_dialog_show_event
MaskDialogBase.done = _global_mask_dialog_done
