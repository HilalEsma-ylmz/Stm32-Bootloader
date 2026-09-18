"""
serial_handler.py
------------------
UART üzerinden bootloader ile konuşan düşük seviyeli katman.
GUI (main.py) bu sınıfı bir arka plan thread'i içinde çağırır,
böylece uzun süren aktarım pencereyi kilitlemez.
"""

import struct
import time
import serial
from functools import wraps

import protocol as proto


class TransferError(Exception):
    """Aktarım sırasında kurtarılamayan bir hata oluştuğunda fırlatılır."""
    pass


class ConnectionLost(TransferError):
    """Transport failure; it must not be retried as a firmware NACK."""


def serial_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (serial.SerialException, OSError) as exc:
            raise ConnectionLost(
                "Seri bağlantı kesildi veya veri gönderilemiyor. Kablo ve güç bağlantısını kontrol edin. "
                "Kart yeniden açıldığında yarım güncelleme kontrol edilir; geçerli OLD yedeği varsa kurtarılır. "
                f"Teknik ayrıntı: {exc}"
            ) from exc
    return wrapped


class BootloaderTransfer:
    def __init__(self, port: str, baudrate: int = 115200, log_fn=None):
        """
        log_fn: (str) -> None şeklinde bir callback; GUI'ye satır satır durum
        mesajı basmak için kullanılır (örn. self.log_signal.emit).
        """
        self.port = port
        self.baudrate = baudrate
        self.log = log_fn or (lambda msg: None)
        self.ser = None
        self.layout_verified = False
        self.device_version = None

    # ---------------- Bağlantı ----------------
    @serial_errors
    def connect(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=0.05,
            write_timeout=5.0,
        )
        self.log(f"Port açıldı: {self.port} @ {self.baudrate} bps")

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        # PySerial nesnesini de bırak: Windows COM portunu tekrar açabilsin.
        self.ser = None
        self.layout_verified = False

    @serial_errors
    def request_soft_reset(self):
        """STM32'ye fiziksel reset dugmesine denk yazilimsal reset komutu yollar."""
        if self.ser is None or not self.ser.is_open:
            raise TransferError("Reset icin seri port acik degil.")
        self.ser.reset_input_buffer()
        self.ser.write(bytes([proto.CMD_SOFT_RESET]))
        self.ser.flush()
        self.log("Yazilimsal reset komutu gonderildi (0x34).")

    @serial_errors
    def _read_exactly(self, size: int, timeout=None) -> bytes:
        result = bytearray()
        deadline = time.monotonic() + (proto.CHUNK_ACK_TIMEOUT_S if timeout is None else timeout)
        while len(result) < size and time.monotonic() < deadline:
            data = self.ser.read(size - len(result))
            if data:
                result.extend(data)
        if len(result) != size:
            raise TransferError("Karttan yanit beklenirken zaman asimi olustu.")
        return bytes(result)

    # ---------------- Adım 1: Handshake (ICD Bölüm 5.1) ----------------
    @serial_errors
    def handshake(self) -> bool:
        """0x55 gönderir, 10 saniyelik pencere içinde 0x9A bekler.
        Kart her an dinlemede olabileceği için sinyal periyodik tekrar gönderilir."""
        self.log("Handshake gönderiliyor (0x55)...")
        deadline = time.time() + proto.HANDSHAKE_TIMEOUT_S
        self.ser.reset_input_buffer()

        while time.time() < deadline:
            self.ser.write(bytes([proto.SIG_HANDSHAKE_REQ]))
            response = self.ser.read(1)
            if response == bytes([proto.SIG_HANDSHAKE_ACK]):
                self._verify_layout()
                self.log(f"Handshake başarılı: NEW/OLD bootloader v{self.device_version}, 115200 baud.")
                return True
            # kısa bir bekleme sonrası tekrar dene (kart henüz hazır olmayabilir)
            time.sleep(0.3)

        self.log("Handshake zaman aşımına uğradı — kart cevap vermedi.")
        return False

    def _verify_layout(self):
        self.layout_verified = False
        self.ser.write(bytes([proto.CMD_INFO]))
        try:
            info = self._read_exactly(8)
        except ConnectionLost:
            raise
        except TransferError as exc:
            raise TransferError("Bootloader v2 bilgisi alinamadi. Once yeni bootloader'i ST-LINK ile yukleyin.") from exc
        marker, version, capacity, address = struct.unpack('<BBHI', info)
        if (marker, capacity, address) != (proto.CMD_INFO, proto.APP_CAPACITY, proto.APP_ADDRESS) or version not in proto.SUPPORTED_VERSIONS:
            raise TransferError("Kartin protokolu veya Flash duzeni bu arayuzle uyumlu degil.")
        self.device_version = version
        self.layout_verified = True

    @serial_errors
    def select_transfer_speed(self, baud):
        if not self.layout_verified:
            raise TransferError("Önce Bağlan / Handshake yapın.")
        if baud == proto.BASE_BAUD:
            if self.ser.baudrate != proto.BASE_BAUD:
                raise TransferError("Test modu için kartı resetleyip yeniden bağlanın.")
            self.log("Test modu: mevcut 115200 baud, ek paket gecikmesi yok.")
            return
        if baud != proto.FAST_BAUD:
            raise TransferError("Desteklenmeyen aktarım hızı.")
        if self.device_version < 3:
            raise TransferError("Hızlı mod için yeni bootloader'ı ST-LINK ile yükleyin; mevcut kartta Test modu kullanılabilir.")
        self.log("Normal mod: 1.000.000 baud bağlantı doğrulanıyor...")
        self.ser.write(bytes([proto.CMD_FAST_MODE]))
        self.ser.flush()
        self._expect_ack(0xFFFC)
        try:
            # Board waits 50 ms; let both ends finish the old-speed ACK first.
            time.sleep(0.10)
            self.ser.baudrate = proto.FAST_BAUD
            self.ser.reset_input_buffer()
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                self.ser.write(bytes([proto.SIG_HANDSHAKE_REQ]))
                self.ser.flush()
                if self.ser.read(1) == bytes([proto.SIG_HANDSHAKE_ACK]):
                    self.log("Hızlı bağlantı doğrulandı: 1.000.000 baud.")
                    return
                time.sleep(0.05)
            raise TransferError("Hızlı bağlantı doğrulanamadı. Test modunu seçip yeniden bağlanın; gerekirse kartı resetleyin.")
        except Exception:
            # The board returns to base speed after 2 s without confirmation,
            # or 10 s idle if its READY was lost. Never issue START here.
            self.layout_verified = False
            try:
                self.ser.baudrate = proto.BASE_BAUD
            except (serial.SerialException, OSError):
                pass
            raise

    def _expect_ack(self, sequence, timeout=None):
        # One deadline covers both the marker and its variable-length body.
        deadline = time.monotonic() + (proto.CHUNK_ACK_TIMEOUT_S if timeout is None else timeout)
        def read(n):
            return self._read_exactly(n, max(0, deadline - time.monotonic()))
        marker = read(1)[0]
        if marker == proto.CMD_NACK:
            code = read(1)[0]
            raise TransferError(proto.ERROR_MESSAGES.get(code, f"Bilinmeyen NACK: 0x{code:02X}"))
        if marker != proto.CMD_ACK:
            raise TransferError(f"Beklenmeyen cevap: 0x{marker:02X}")
        received = struct.unpack('<H', read(2))[0]
        if received != sequence:
            raise TransferError(f"ACK sirasi {received}, beklenen {sequence}.")

    def send_fw_size(self, fw_size: int) -> bool:
        if not self.layout_verified:
            raise TransferError("Once v2 bootloader ile Baglan / Handshake yapin.")
        if not proto.MIN_IMAGE_SIZE <= fw_size <= proto.APP_CAPACITY:
            raise TransferError("Firmware boyutu gecersiz (en fazla 24 KB).")
        self.log("START: mevcut firmware OLD'a yedekleniyor, NEW hazirlaniyor...")
        self.ser.write(proto.build_fw_size_packet(fw_size))
        self._expect_ack(0xFFFF, proto.START_ACK_TIMEOUT_S)
        self.log("START onaylandi; NEW veri almaya hazir.")
        return True

    # ---------------- Adım 3: Tek bir chunk gönder + retry (ICD Bölüm 5.3, 5.4, 7.1) ----------------
    def send_chunk(self, chunk_index: int, chunk_data: bytes) -> bool:
        packet = proto.build_fw_data_packet(chunk_index, chunk_data)
        for attempt in range(1, proto.MAX_RETRIES + 1):
            self.ser.reset_input_buffer()
            self.ser.write(packet)
            try:
                self._expect_ack(chunk_index)
                return True
            except ConnectionLost:
                raise
            except TransferError as exc:
                self.log(f"Paket {chunk_index}, deneme {attempt}: {exc}")
        return False

    # ---------------- Adım 4: Tüm chunk'ları sırayla gönder ----------------
    def send_all_chunks(self, chunks, progress_fn=None, should_stop=None) -> bool:
        """progress_fn: (index:int, total:int) -> None ; ilerleme çubuğunu güncellemek için.
        should_stop: () -> bool ; her chunk öncesi kontrol edilir, True dönerse aktarım
        kullanıcı isteğiyle (Durdur butonu) güvenle sonlandırılır."""
        total = len(chunks)
        for index, chunk_data in enumerate(chunks):
            if should_stop and should_stop():
                self.log("Aktarım kullanıcı tarafından durduruldu.")
                self.abort()
                return False

            ok = self.send_chunk(index, chunk_data)
            if not ok:
                self.log(f"Chunk {index} 3 denemede de başarısız oldu. Aktarım durduruluyor.")
                self.abort()
                return False
            if progress_fn:
                progress_fn(index + 1, total)
        return True

    # ---------------- Adım 5: Bitiş sinyali + genel CRC32 (ICD Bölüm 5.5) ----------------
    def send_fw_end(self, fw_crc32: int) -> bool:
        self.log("NEW Flash CRC32 ve baslangic vektorleri dogrulaniyor...")
        self.ser.write(proto.build_fw_end_packet(fw_crc32))
        self._expect_ack(0xFFFE)
        self.log("NEW dogrulandi, metadata kaydedildi. Kart yeniden baslayacak.")
        return True

    def abort(self):
        self.ser.reset_input_buffer()
        self.ser.write(bytes([proto.CMD_ABORT]))
        self.log("Iptal: varsa OLD yedegi NEW'e geri yukleniyor...")
        self._expect_ack(0xFFFD, proto.ABORT_ACK_TIMEOUT_S)
        self.log("Iptal/kurtarma onaylandi; kart yeniden basliyor.")

    # ---------------- Tüm akışı tek fonksiyonda birleştir ----------------
    @serial_errors
    def run_full_transfer(self, fw_bytes: bytes, progress_fn=None, should_stop=None,
                           do_handshake: bool = True, data_baud=proto.BASE_BAUD) -> bool:
        """ICD Bölüm 2 ve Bölüm 11'deki uçtan uca akışın tamamı.

        do_handshake=False: handshake bu fonksiyondan ÖNCE, ayrı 'Bağlan' butonuyla
        zaten yapılmışsa (bkz. main.py), burada tekrar handshake denenmez.
        """
        proto.validate_firmware(fw_bytes)
        if should_stop and should_stop():
            raise TransferError("Aktarim baslamadan durduruldu.")
        if do_handshake and not self.handshake():
            raise TransferError("Handshake başarısız — kart bulunamadı veya yanıt vermedi.")

        self.select_transfer_speed(data_baud)
        if should_stop and should_stop():
            raise TransferError("Aktarım başlamadan durduruldu; gerekirse kartı resetleyin.")
        started = time.monotonic()
        if not self.send_fw_size(len(fw_bytes)):
            raise TransferError("Firmware boyutu reddedildi.")

        chunks = proto.split_into_chunks(fw_bytes)
        if not self.send_all_chunks(chunks, progress_fn=progress_fn, should_stop=should_stop):
            raise TransferError("Chunk aktarımı tamamlanamadı (retry limiti aşıldı ya da durduruldu).")

        if should_stop and should_stop():
            self.abort()
            raise TransferError("Aktarim durduruldu.")
        fw_crc32 = proto.crc32_standard(fw_bytes)
        if not self.send_fw_end(fw_crc32):
            raise TransferError("Genel CRC32 doğrulaması başarısız — application geçersiz kaldı.")

        self.log(f"Toplam güncelleme süresi: {time.monotonic() - started:.2f} saniye (yedekleme dahil).")
        return True
