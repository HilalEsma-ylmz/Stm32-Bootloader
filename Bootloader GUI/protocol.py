"""UART v3: sabit NEW, OLD yedegi ve hiz secimi. Ayrintilar: docs/UPDATE_FLOW.md."""

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
CMD_INFO            = 0x35
PROTOCOL_VERSION    = 3
SUPPORTED_VERSIONS  = (2, 3)
CMD_FAST_MODE       = 0x36
BASE_BAUD           = 115200
FAST_BAUD           = 1000000
APP_ADDRESS         = 0x08003400
MIN_IMAGE_SIZE      = 192
START_ACK_TIMEOUT_S = 30.0  # Backup + metadata + erase
ABORT_ACK_TIMEOUT_S = 30.0  # May restore OLD into NEW

# ---------- Hata kodları (ICD Bölüm 7.2, sadece NACK'te) ----------
ERR_CRC_MISMATCH    = 0x01
ERR_FLASH_WRITE_FAIL = 0x02
ERR_SEQUENCE        = 0x03

ERROR_MESSAGES = {
    ERR_CRC_MISMATCH: "NACK 0x01 - CRC / uygulama dogrulama hatasi: paket, image veya vektorler gecersiz.",
    ERR_FLASH_WRITE_FAIL: "NACK 0x02 - Flash / kurtarma hatasi: silme, yazma, yedekleme veya metadata islemi basarisiz.",
    ERR_SEQUENCE: "NACK 0x03 - Sira/boyut hatasi: paket sirasi veya image boyutu gecersiz.",
}

# ---------- Flash / Chunk parametreleri (ICD Bölüm 5.3, 8) ----------
CHUNK_SIZE = 256                     # byte
APP_CAPACITY = 24 * 1024             # NEW and OLD are each 24 KB
MAX_CHUNKS = APP_CAPACITY // CHUNK_SIZE   # 96

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
    """ICD Bölüm 5.2: [CMD=0x31][FW_SIZE 2 byte little-endian]"""
    return bytes([CMD_FW_SIZE]) + struct.pack('<H', fw_size)


def build_fw_data_packet(chunk_index: int, chunk_data: bytes) -> bytes:
    """0x32 + index:u16 + length:u16 + payload[256] + CRC16; padding excluded from CRC."""
    if not 0 < len(chunk_data) <= CHUNK_SIZE:
        raise ValueError(f"chunk_data {CHUNK_SIZE} byte olmalı, {len(chunk_data)} verildi")
    header = bytes([CMD_FW_DATA]) + struct.pack('<HH', chunk_index, len(chunk_data))
    payload = chunk_data.ljust(CHUNK_SIZE, b'\xFF')
    crc = crc16_ccitt(header + chunk_data)
    return header + payload + struct.pack('<H', crc)


def build_fw_end_packet(fw_crc32: int) -> bytes:
    """ICD Bölüm 5.5: [CMD=0x33][FW_CRC32 4 byte little-endian]"""
    return bytes([CMD_FW_END]) + struct.pack('<I', fw_crc32)


# ================= Yardımcı: dosyayı chunk'lara bölme =================

def split_into_chunks(fw_bytes: bytes):
    """Return raw chunks; packet builder adds final 0xFF padding."""
    chunks = []
    for i in range(0, len(fw_bytes), CHUNK_SIZE):
        piece = fw_bytes[i:i + CHUNK_SIZE]
        chunks.append(piece)
    return chunks


def validate_firmware(data: bytes):
    """Catch an empty/oversize or wrong-link-address binary before erasing Flash."""
    if not MIN_IMAGE_SIZE <= len(data) <= APP_CAPACITY:
        raise ValueError(f"Firmware boyutu {MIN_IMAGE_SIZE}..{APP_CAPACITY} bayt olmali.")
    msp, reset = struct.unpack_from('<II', data)
    if not (0x20000100 < msp <= 0x20001FF0) or msp % 8:
        raise ValueError("Baslangic stack adresi gecersiz; application linker ayarini kontrol edin.")
    if not reset & 1 or not APP_ADDRESS + MIN_IMAGE_SIZE <= (reset & ~1) < APP_ADDRESS + len(data):
        raise ValueError("Reset adresi gecersiz; .bin 0x08003400 adresi icin derlenmeli.")
