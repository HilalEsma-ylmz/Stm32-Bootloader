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

import protocol as proto


class TransferError(Exception):
    """Aktarım sırasında kurtarılamayan bir hata oluştuğunda fırlatılır."""
    pass


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

    # ---------------- Bağlantı ----------------
    def connect(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=0.05,
        )
        self.log(f"Port açıldı: {self.port} @ {self.baudrate} bps")

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        # PySerial nesnesini de bırak: Windows COM portunu tekrar açabilsin.
        self.ser = None

    def request_soft_reset(self):
        """STM32'ye fiziksel reset dugmesine denk yazilimsal reset komutu yollar."""
        if self.ser is None or not self.ser.is_open:
            raise TransferError("Reset icin seri port acik degil.")
        self.ser.reset_input_buffer()
        self.ser.write(bytes([proto.CMD_SOFT_RESET]))
        self.ser.flush()
        self.log("Yazilimsal reset komutu gonderildi (0x34).")

    def _read_exactly(self, size: int) -> bytes:
        result = bytearray()
        deadline = time.monotonic() + proto.CHUNK_ACK_TIMEOUT_S
        while len(result) < size and time.monotonic() < deadline:
            data = self.ser.read(size - len(result))
            if data:
                result.extend(data)
        if len(result) != size:
            raise TransferError("Karttan yanit beklenirken zaman asimi olustu.")
        return bytes(result)

    # ---------------- Adım 1: Handshake (ICD Bölüm 5.1) ----------------
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
                self.log("Handshake başarılı, kart güncelleme modunda.")
                return True
            # kısa bir bekleme sonrası tekrar dene (kart henüz hazır olmayabilir)
            time.sleep(0.3)

        self.log("Handshake zaman aşımına uğradı — kart cevap vermedi.")
        return False

    # ---------------- Adım 2: Firmware boyutu bildirimi (ICD Bölüm 5.2) ----------------
    def send_fw_size(self, fw_size: int) -> bool:
        if fw_size > proto.APP_CAPACITY:
            self.log(f"Dosya çok büyük: {fw_size} byte > kapasite {proto.APP_CAPACITY} byte")
            return False

        self.log(f"Firmware boyutu bildiriliyor: {fw_size} byte")
        packet = proto.build_fw_size_packet(fw_size)
        self.ser.write(packet)

        try:
            response = self._read_exactly(3)
        except TransferError as exc:
            raise TransferError("START onayi alinmadi: karttan yanit gelmedi.") from exc
        if response == bytes([proto.CMD_ACK, 0xFF, 0xFF]):
            self.log("Kart boyutu kabul etti.")
            return True
        elif response[0] == proto.CMD_NACK:
            message = proto.ERROR_MESSAGES.get(response[1], f"NACK 0x{response[1]:02X} - Bilinmeyen kart hata kodu.")
            self.log(message)
            raise TransferError(message)
        else:
            raise TransferError(f"START icin beklenmeyen yanit: {response.hex(' ')}")

    # ---------------- Adım 3: Tek bir chunk gönder + retry (ICD Bölüm 5.3, 5.4, 7.1) ----------------
    def send_chunk(self, chunk_index: int, chunk_data: bytes) -> bool:
        packet = proto.build_fw_data_packet(chunk_index, chunk_data)

        for attempt in range(1, proto.MAX_RETRIES + 1):
            self.ser.write(packet)
            try:
                marker = self._read_exactly(1)[0]
                if marker == proto.CMD_ACK:
                    sequence = struct.unpack('<H', self._read_exactly(2))[0]
                    if sequence == chunk_index:
                        return True
                    self.log(f"Chunk {chunk_index}: ACK sira numarasi {sequence} geldi, beklenen farkli.")
                    continue

                if marker == proto.CMD_NACK:
                    err = self._read_exactly(1)[0]
                    self.log(f"Chunk {chunk_index}: {proto.ERROR_MESSAGES.get(err, f'NACK 0x{err:02X} - bilinmeyen hata')}, "
                             f"deneme {attempt}/{proto.MAX_RETRIES}")
                    continue

                self.log(f"Chunk {chunk_index}: beklenmeyen yanit 0x{marker:02X}.")
                continue
            except TransferError:
                pass

            self.log(f"Chunk {chunk_index}: cevap alınamadı (timeout), deneme {attempt}/{proto.MAX_RETRIES}")
            # timeout da retry sebebi

        return False  # 3 denemede de başarısız

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
        self.log("Aktarım tamamlandı, genel CRC32 gönderiliyor...")
        packet = proto.build_fw_end_packet(fw_crc32)
        self.ser.write(packet)

        try:
            response = self._read_exactly(3)
        except TransferError as exc:
            raise TransferError("END onayi alinmadi: kart resetlenmis veya yanit gelmedi.") from exc
        if response == bytes([proto.CMD_ACK, 0xFE, 0xFF]):
            self.log("Genel CRC32 doğrulandı. Application geçerli olarak işaretlendi.")
            return True
        elif response[0] == proto.CMD_NACK:
            message = proto.ERROR_MESSAGES.get(response[1], f"NACK 0x{response[1]:02X} - Bilinmeyen kart hata kodu.")
            self.log(message)
            raise TransferError(message)
        else:
            raise TransferError(f"END icin beklenmeyen yanit: {response.hex(' ')}")

    def abort(self):
        try:
            self.ser.write(bytes([proto.CMD_ABORT]))
            self.log("CMD_ABORT gönderildi, aktarım güvenli şekilde sonlandırıldı.")
        except Exception:
            pass

    # ---------------- Tüm akışı tek fonksiyonda birleştir ----------------
    def run_full_transfer(self, fw_bytes: bytes, progress_fn=None, should_stop=None,
                           do_handshake: bool = True) -> bool:
        """ICD Bölüm 2 ve Bölüm 11'deki uçtan uca akışın tamamı.

        do_handshake=False: handshake bu fonksiyondan ÖNCE, ayrı 'Bağlan' butonuyla
        zaten yapılmışsa (bkz. main.py), burada tekrar handshake denenmez.
        """
        if do_handshake and not self.handshake():
            raise TransferError("Handshake başarısız — kart bulunamadı veya yanıt vermedi.")

        if not self.send_fw_size(len(fw_bytes)):
            raise TransferError("Firmware boyutu reddedildi.")

        chunks = proto.split_into_chunks(fw_bytes)
        if not self.send_all_chunks(chunks, progress_fn=progress_fn, should_stop=should_stop):
            raise TransferError("Chunk aktarımı tamamlanamadı (retry limiti aşıldı ya da durduruldu).")

        fw_crc32 = proto.crc32_standard(fw_bytes)
        if not self.send_fw_end(fw_crc32):
            raise TransferError("Genel CRC32 doğrulaması başarısız — application geçersiz kaldı.")

        return True
