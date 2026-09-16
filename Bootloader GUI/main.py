"""
main.py
-------
UART Bootloader — Firmware Sender (GUI)
Verilen mockup tasarımına uygun, koyu temalı PyQt5 arayüzü.

Çalıştırmak için:
    pip install pyqt5 pyserial
    python main.py
"""

import sys
import serial.tools.list_ports
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QFileDialog, QComboBox, QProgressBar, QTextEdit, QMessageBox, QFrame
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt

from serial_handler import BootloaderTransfer, TransferError
import protocol as proto


# ================= Koyu tema (mockup ile birebir uyumlu renkler) =================
STYLE_SHEET = """
QWidget {
    background-color: #0f1b2e;
    color: #cbd5e1;
    font-family: Segoe UI, Arial;
    font-size: 13px;
}
QFrame#card {
    background-color: #16233b;
    border-radius: 8px;
    border: 1px solid #1f2f4a;
}
QLabel#titleLabel {
    font-size: 22px;
    font-weight: bold;
    color: #ffffff;
}
QLabel#subtitleLabel {
    color: #7d8ba1;
    font-size: 12px;
}
QLabel#cardTitle {
    font-weight: bold;
    color: #ffffff;
    font-size: 13px;
}
QLabel#hintLabel {
    color: #7d8ba1;
    font-size: 11px;
}
QLabel#valueLabel {
    color: #7ee787;
    font-weight: bold;
}
QComboBox, QTextEdit {
    background-color: #0f1b2e;
    border: 1px solid #2a3b5c;
    border-radius: 4px;
    padding: 4px;
    color: #cbd5e1;
}
QPushButton {
    background-color: #1f2f4a;
    border: 1px solid #2a3b5c;
    border-radius: 4px;
    padding: 6px 14px;
    color: #cbd5e1;
}
QPushButton:hover { background-color: #28405f; }
QPushButton:disabled { color: #4b5a72; }
QPushButton#primaryBtn {
    background-color: #f2a93b;
    color: #1a1206;
    font-weight: bold;
    border: none;
}
QPushButton#primaryBtn:hover { background-color: #ffb84d; }
QPushButton#primaryBtn:disabled { background-color: #6b5a35; color: #9a8a63; }
QProgressBar {
    background-color: #0f1b2e;
    border: 1px solid #2a3b5c;
    border-radius: 4px;
    text-align: center;
    color: #cbd5e1;
}
QProgressBar::chunk { background-color: #f2a93b; border-radius: 3px; }
"""


def card_frame(title_text: str) -> tuple:
    """Mockup'taki '1. Kart bağlantısı' gibi başlıklı kart kutularını oluşturan yardımcı.
    (frame, içerik_layout) döner — çağıran taraf içeriği content_layout'a ekler."""
    frame = QFrame()
    frame.setObjectName("card")
    outer = QVBoxLayout(frame)
    title = QLabel(title_text)
    title.setObjectName("cardTitle")
    outer.addWidget(title)
    content_layout = QVBoxLayout()
    outer.addLayout(content_layout)
    return frame, content_layout


class HandshakeWorker(QThread):
    """Sadece handshake adımını yürütür (ICD Bölüm 5.1) — ayrı 'Bağlan' butonuna bağlı."""
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, object)  # (başarılı mı, mesaj, transfer nesnesi)

    def __init__(self, port: str, baudrate: int):
        super().__init__()
        self.port = port
        self.baudrate = baudrate

    def run(self):
        transfer = BootloaderTransfer(self.port, baudrate=self.baudrate, log_fn=self.log_signal.emit)
        try:
            transfer.connect()
            ok = transfer.handshake()
            if ok:
                self.finished_signal.emit(True, "Bağlantı kuruldu, kart güncelleme modunda.", transfer)
            else:
                transfer.close()
                self.finished_signal.emit(False, "Handshake başarısız — kart yanıt vermedi.", None)
        except Exception as e:
            transfer.close()
            self.finished_signal.emit(False, f"Bağlantı hatası: {e}", None)


class TransferWorker(QThread):
    """Handshake ZATEN yapılmış bir bağlantı üzerinden asıl firmware aktarımını yürütür."""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, transfer: BootloaderTransfer, fw_bytes: bytes):
        super().__init__()
        self.transfer = transfer
        self.fw_bytes = fw_bytes
        self._stop_requested = False
        self.transfer.log = self.log_signal.emit  # log hedefini bu worker'a bağla

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.transfer.run_full_transfer(
                self.fw_bytes,
                progress_fn=lambda cur, total: self.progress_signal.emit(cur, total),
                should_stop=lambda: self._stop_requested,
                do_handshake=False,  # handshake zaten 'Bağlan' butonuyla yapıldı
            )
            self.finished_signal.emit(
                True,
                "Güncelleme başarılı! Kart application ile otomatik yeniden başlatıldı."
            )
        except TransferError as e:
            self.finished_signal.emit(False, str(e))
        except Exception as e:
            self.finished_signal.emit(False, f"Beklenmeyen hata: {e}")


class ResetWorker(QThread):
    """UART üzerinden STM32'ye NVIC_SystemReset isteği gönderir."""
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, port: str, baudrate: int):
        super().__init__()
        self.port = port
        self.baudrate = baudrate

    def run(self):
        transfer = BootloaderTransfer(self.port, baudrate=self.baudrate, log_fn=self.log_signal.emit)
        try:
            transfer.connect()
            transfer.request_soft_reset()
            transfer.close()
            self.finished_signal.emit(True, "Kart yazılımsal olarak yeniden başlatıldı.")
        except Exception as exc:
            transfer.close()
            self.finished_signal.emit(False, f"Yazılımsal reset hatası: {exc}")


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.fw_bytes = None
        self.transfer = None          # handshake sonrası bağlı BootloaderTransfer
        self.hs_worker = None
        self.tx_worker = None
        self.reset_worker = None
        self._build_ui()

    # ================= ARAYÜZ =================
    def _build_ui(self):
        self.setWindowTitle("STM32 UART Bootloader | Firmware Sender")
        self.setMinimumWidth(920)
        self.setStyleSheet(STYLE_SHEET)

        root = QVBoxLayout()
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # --- Başlık ---
        title = QLabel("STM32 UART Bootloader")
        title.setObjectName("titleLabel")
        subtitle = QLabel("Firmware Sender  ·  STM32F0308-DISCO  ·  Protocol v1")
        subtitle.setObjectName("subtitleLabel")
        root.addWidget(title)
        root.addWidget(subtitle)

        # --- Üst satır: Kart bağlantısı + Firmware dosyası ---
        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        top_row.addWidget(self._build_connection_card(), stretch=1)
        top_row.addWidget(self._build_file_card(), stretch=1)
        root.addLayout(top_row)

        # --- Alt: Aktarım durumu ---
        root.addWidget(self._build_status_card(), stretch=1)

        # --- Butonlar ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.start_btn = QPushButton("Aktarımı Başlat")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_transfer)
        self.stop_btn = QPushButton("Durdur")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_transfer)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        root.addLayout(btn_row)

        self.setLayout(root)
        self.refresh_ports()

    def _build_connection_card(self) -> QFrame:
        frame, layout = card_frame("1. Kart Bağlantısı")

        grid = QGridLayout()
        grid.addWidget(QLabel("COM port"), 0, 0)
        self.port_combo = QComboBox()
        grid.addWidget(self.port_combo, 0, 1)
        self.scan_btn = QPushButton("Tara")
        self.scan_btn.clicked.connect(self.refresh_ports)
        grid.addWidget(self.scan_btn, 0, 2)

        grid.addWidget(QLabel("Baud"), 1, 0)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200", "9600", "57600", "230400"])
        grid.addWidget(self.baud_combo, 1, 1)
        layout.addLayout(grid)

        hint = QLabel("USB-UART: 3.3 V TTL / PA9-PA10")
        hint.setObjectName("hintLabel")
        layout.addWidget(hint)

        connect_row = QHBoxLayout()
        self.connect_btn = QPushButton("Bağlan (Handshake)")
        self.connect_btn.clicked.connect(self.start_handshake)
        self.reset_btn = QPushButton("Kartı Yeniden Başlat")
        self.reset_btn.clicked.connect(self.request_soft_reset)
        self.connect_status_label = QLabel("Bağlı değil")
        self.connect_status_label.setObjectName("hintLabel")
        connect_row.addWidget(self.connect_btn)
        connect_row.addWidget(self.reset_btn)
        connect_row.addWidget(self.connect_status_label, stretch=1)
        layout.addLayout(connect_row)

        return frame

    def _build_file_card(self) -> QFrame:
        frame, layout = card_frame("2. Firmware Dosyası")

        row = QHBoxLayout()
        self.file_status_label = QLabel("Henüz .bin dosyası seçilmedi")
        self.select_file_btn = QPushButton(".bin Seç")
        self.select_file_btn.clicked.connect(self.select_file)
        row.addWidget(self.file_status_label, stretch=1)
        row.addWidget(self.select_file_btn)
        layout.addLayout(row)

        info_grid = QGridLayout()
        info_grid.addWidget(QLabel("Boyut"), 0, 0)
        self.size_value_label = QLabel("–")
        self.size_value_label.setObjectName("valueLabel")
        info_grid.addWidget(self.size_value_label, 0, 1)

        info_grid.addWidget(QLabel("CRC32"), 1, 0)
        self.crc_value_label = QLabel("–")
        self.crc_value_label.setObjectName("valueLabel")
        info_grid.addWidget(self.crc_value_label, 1, 1)

        info_grid.addWidget(QLabel("Paket"), 2, 0)
        self.chunk_value_label = QLabel("–")
        self.chunk_value_label.setObjectName("valueLabel")
        info_grid.addWidget(self.chunk_value_label, 2, 1)

        layout.addLayout(info_grid)
        return frame

    def _build_status_card(self) -> QFrame:
        frame, layout = card_frame("3. Aktarım Durumu")

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(160)
        layout.addWidget(self.log_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v / %m paket")
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Hazır. COM portu seçip 'Bağlan' ile kartı doğrulayın.")
        self.status_label.setObjectName("hintLabel")
        layout.addWidget(self.status_label)

        self.log("Hazır. COM portu ve .bin dosyasını seçin.")
        return frame

    # ================= Olaylar =================
    def refresh_ports(self):
        self.port_combo.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo.addItems(ports if ports else ["(port bulunamadı)"])
        self.log(f"Port taraması tamamlandı: {', '.join(ports) if ports else 'bulunamadı'}")

    def select_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Firmware Dosyası Seç", "", "Binary Files (*.bin)")
        if not path:
            return

        with open(path, "rb") as f:
            fw_bytes = f.read()

        if len(fw_bytes) > proto.APP_CAPACITY:
            QMessageBox.warning(
                self, "Dosya çok büyük",
                f"Seçilen dosya {len(fw_bytes)} byte, kapasite {proto.APP_CAPACITY} byte "
                f"({proto.APP_CAPACITY // 1024} KB). Sığmıyor."
            )
            return

        self.fw_bytes = fw_bytes
        chunk_count = len(proto.split_into_chunks(fw_bytes))
        fw_crc32 = proto.crc32_standard(fw_bytes)

        self.file_status_label.setText(path.split('/')[-1].split('\\')[-1])
        self.size_value_label.setText(f"{len(fw_bytes)} byte ({len(fw_bytes)/1024:.1f} KB)")
        self.crc_value_label.setText(f"0x{fw_crc32:08X}")
        self.chunk_value_label.setText(f"{chunk_count} paket ({proto.CHUNK_SIZE} B / paket)")

        self.log(f"Dosya yüklendi: {len(fw_bytes)} byte, {chunk_count} chunk, CRC32=0x{fw_crc32:08X}")
        self._update_start_button_state()

    # --- Adım 1: Bağlan / Handshake ---
    def start_handshake(self):
        # Bir önceki aktarımın seri portu açık kalmış olmasın. Windows aynı
        # COM portunu ikinci kez açmaya izin vermez (PermissionError 13).
        if self.transfer is not None:
            self.transfer.close()
            self.transfer = None

        port = self.port_combo.currentText()
        if not port or "(port" in port:
            QMessageBox.warning(self, "Port seçilmedi", "Lütfen geçerli bir seri port seçin.")
            return
        baud = int(self.baud_combo.currentText())

        self.connect_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.connect_status_label.setText("Bağlanıyor... (handshake gönderiliyor)")

        self.hs_worker = HandshakeWorker(port, baud)
        self.hs_worker.log_signal.connect(self.log)
        self.hs_worker.finished_signal.connect(self.on_handshake_finished)
        self.hs_worker.start()

    def on_handshake_finished(self, success: bool, message: str, transfer):
        self.connect_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)
        self.log(message)
        if success:
            self.transfer = transfer
            self.connect_status_label.setText("Bağlandı ✔")
        else:
            self.transfer = None
            self.connect_status_label.setText("Bağlı değil")
            QMessageBox.critical(self, "Bağlantı Hatası", message)
        self._update_start_button_state()

    def request_soft_reset(self):
        """Kartın fiziksel RESET düğmesine dokunmadan yeniden başlatılması."""
        if ((self.tx_worker is not None and self.tx_worker.isRunning()) or
                (self.hs_worker is not None and self.hs_worker.isRunning())):
            QMessageBox.warning(self, "Aktarım sürüyor", "Aktarım sırasında reset gönderilemez.")
            return

        if self.transfer is not None:
            self.transfer.close()
            self.transfer = None

        port = self.port_combo.currentText()
        if not port or "(port" in port:
            QMessageBox.warning(self, "Port seçilmedi", "Lütfen geçerli bir seri port seçin.")
            return

        self.reset_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.connect_status_label.setText("Kart yeniden başlatılıyor...")
        self.reset_worker = ResetWorker(port, int(self.baud_combo.currentText()))
        self.reset_worker.log_signal.connect(self.log)
        self.reset_worker.finished_signal.connect(self.on_soft_reset_finished)
        self.reset_worker.start()

    def on_soft_reset_finished(self, success: bool, message: str):
        self.log(message)
        self.reset_btn.setEnabled(True)
        self.connect_btn.setEnabled(True)
        if not success:
            self.connect_status_label.setText("Bağlı değil")
            QMessageBox.critical(self, "Reset Hatası", message)
            return

        self.connect_status_label.setText("Yeniden başlatıldı — bağlı değil")
        self.status_label.setText("Kart yeniden başlatıldı.")

    # --- Adım 2: Aktarımı Başlat / Durdur ---
    def _update_start_button_state(self):
        self.start_btn.setEnabled(self.transfer is not None and self.fw_bytes is not None)

    def start_transfer(self):
        if self.transfer is None or self.fw_bytes is None:
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.select_file_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Aktarım sürüyor...")

        self.tx_worker = TransferWorker(self.transfer, self.fw_bytes)
        self.tx_worker.log_signal.connect(self.log)
        self.tx_worker.progress_signal.connect(self.update_progress)
        self.tx_worker.finished_signal.connect(self.on_transfer_finished)
        self.tx_worker.start()

    def stop_transfer(self):
        if self.tx_worker:
            self.tx_worker.request_stop()
            self.status_label.setText("Durduruluyor...")

    def update_progress(self, current: int, total: int):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_transfer_finished(self, success: bool, message: str):
        self.log(message)
        self.status_label.setText(message)
        if success:
            QMessageBox.information(self, "Başarılı", message)
        else:
            QMessageBox.critical(self, "Hata", message)

        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.select_file_btn.setEnabled(True)
        self.connect_btn.setEnabled(True)
        # Aktarım bittiğinde/durduğunda portu gerçekten serbest bırak.
        # END komutundan sonra kart uygulamayı çalıştırmak üzere resetlenir;
        # sonraki "Bağlan" uygulamanın 0x55 dinleyicisiyle tekrar bootloader'a geçer.
        if self.transfer is not None:
            self.transfer.close()
        self.transfer = None
        self.connect_status_label.setText("Bağlı değil")
        self._update_start_button_state()

    def log(self, message: str):
        self.log_box.append(f'<span style="color:#cbd5e1;">{message}</span>')


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
