"""
protocol.py
-----------
ICD-BOOT-UART-003'te tanımlanan protokolün Python tarafındaki karşılığı.
Bu dosyadaki her sabit ve fonksiyon, ICD dokümanındaki ilgili bölüme birebir karşılık gelir.
ICD'de bir değer değişirse SADECE bu dosya güncellenir, GUI koduna dokunulmaz.
"""

import struct

# ---------- Sinyaller / Komutlar (ICD Bölüm 4) ----------
SIG_HANDSHAKE_REQ   = 0x55
SIG_HANDSHAKE_ACK   = 0x9A

CMD_FW_SIZE         = 0x31
CMD_FW_SIZE_ACK     = 0x21
CMD_FW_SIZE_NACK    = 0x22

CMD_FW_DATA         = 0x32
CMD_ACK             = 0x21
CMD_NACK            = 0x22

CMD_FW_END          = 0x33
CMD_FW_END_ACK      = 0x21
CMD_FW_END_NACK     = 0x22

# Arayuzdeki reset dugmesi: STM32 NVIC_SystemReset() cagirir.
CMD_SOFT_RESET      = 0x34

CMD_ABORT           = 0xFF

# ---------- Hata kodları (ICD Bölüm 7.2, sadece NACK'te) ----------
ERR_CRC_MISMATCH    = 0x01
ERR_FLASH_WRITE_FAIL = 0x02
ERR_SEQUENCE        = 0x03

ERROR_MESSAGES = {
    ERR_CRC_MISMATCH: "NACK 0x01 - CRC16 hatasi: paket bozuk veya eksik alindi.",
    ERR_FLASH_WRITE_FAIL: "NACK 0x02 - Flash yazma hatasi: kart flash'a yazamadi.",
    ERR_SEQUENCE: "NACK 0x03 - Sira/boyut hatasi: paket sirasi veya image boyutu gecersiz.",
}

# ---------- Flash / Chunk parametreleri (ICD Bölüm 5.3, 8) ----------
CHUNK_SIZE = 256                     # byte
APP_CAPACITY = 50 * 1024             # 51.200 byte (50 KB)
MAX_CHUNKS = APP_CAPACITY // CHUNK_SIZE   # 200

# ---------- Zamanlama parametreleri (ICD Bölüm 7.1) ----------
HANDSHAKE_TIMEOUT_S = 10.0
CHUNK_ACK_TIMEOUT_S = 5.0
MAX_RETRIES = 3


# ================= CRC Hesaplamaları =================

def crc16_ccitt(data: bytes) -> int:
    """ICD Bölüm 5.3 — CRC16-CCITT, poly 0x1021, init 0xFFFF (chunk seviyesi kontrol)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def crc32_standard(data: bytes) -> int:
    """ICD Bölüm 5.5 — genel/son doğrulama CRC32 (standart, zlib/Ethernet varyantı)."""
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


# ================= Paket Oluşturucular (Host -> Target) =================

def build_fw_size_packet(fw_size: int) -> bytes:
    """ICD Bölüm 5.2: [CMD=0x10][FW_SIZE 2 byte little-endian]"""
    return bytes([CMD_FW_SIZE]) + struct.pack('<H', fw_size)


def build_fw_data_packet(chunk_index: int, chunk_data: bytes) -> bytes:
    """ICD Bölüm 5.3: [CMD=0x20][CHUNK_INDEX 1 byte][CHUNK_DATA 256 byte][CRC16 2 byte]

    chunk_data tam 256 byte olmalı; son chunk gerekiyorsa 0xFF ile pad edilmelidir
    (bu projede 55 KB tam bölündüğü için normalde padding gerekmez).
    """
    if not 0 < len(chunk_data) <= CHUNK_SIZE:
        raise ValueError(f"chunk_data {CHUNK_SIZE} byte olmalı, {len(chunk_data)} verildi")
    header = bytes([CMD_FW_DATA]) + struct.pack('<HH', chunk_index, len(chunk_data))
    payload = chunk_data.ljust(CHUNK_SIZE, b'\xFF')
    crc = crc16_ccitt(header + chunk_data)
    return header + payload + struct.pack('<H', crc)


def build_fw_end_packet(fw_crc32: int) -> bytes:
    """ICD Bölüm 5.5: [CMD=0x30][FW_CRC32 4 byte little-endian]"""
    return bytes([CMD_FW_END]) + struct.pack('<I', fw_crc32)


# ================= Yardımcı: dosyayı chunk'lara bölme =================

def split_into_chunks(fw_bytes: bytes):
    """Firmware byte dizisini ICD'ye uygun 256 byte'lık chunk'lara böler.
    Son chunk eksikse 0xFF ile tamamlar (genel bir güvenlik önlemi olarak; bu boyutlarda
    genelde gerekmez ama farklı boyutlu .bin dosyalarında devreye girer)."""
    chunks = []
    for i in range(0, len(fw_bytes), CHUNK_SIZE):
        piece = fw_bytes[i:i + CHUNK_SIZE]
        chunks.append(piece)
    return chunks
