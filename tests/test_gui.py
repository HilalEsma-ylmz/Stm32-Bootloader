import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Bootloader GUI'))
from PyQt5.QtWidgets import QApplication
from main import MainWindow


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('serial.tools.list_ports.comports', return_value=[]):
            self.window = MainWindow()

    def tearDown(self):
        self.window.close()

    def test_start_requires_image_and_connection(self):
        w = self.window
        self.assertFalse(w.start_btn.isEnabled())

        w.fw_bytes = bytes(256)
        connection = Mock()
        w.on_handshake_finished(True, 'ready', connection)
        self.assertTrue(w.start_btn.isEnabled())
        w.busy = True
        w._update_start_button_state()
        self.assertFalse(w.start_btn.isEnabled())

    def test_speed_options(self):
        w = self.window
        self.assertEqual(w.speed_combo.currentData(), 1000000)
        w.speed_combo.setCurrentIndex(1)
        self.assertEqual(w.speed_combo.currentData(), 115200)

    def test_port_change_invalidates_handshake(self):
        w = self.window
        connection = Mock()
        w.transfer = connection
        w.connection_changed()
        connection.close.assert_called_once()
        self.assertIsNone(w.transfer)
        self.assertFalse(w.start_btn.isEnabled())

    def test_transfer_completion_releases_port(self):
        w = self.window
        connection = Mock()
        w.transfer = connection
        w.busy = True
        with patch('main.QMessageBox.information'):
            w.on_transfer_finished(True, 'stored')
        connection.close.assert_called_once()
        self.assertFalse(w.busy)
        self.assertFalse(w.start_btn.isEnabled())
        self.assertTrue(w.reset_btn.isEnabled())


if __name__ == '__main__':
    unittest.main()
