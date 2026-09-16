# UART Bootloader — GUI (PC Tarafı)

ICD-BOOT-UART-003 dokümanına göre yazılmış firmware gönderme aracı.

## Kurulum

```bash
pip install pyqt5 pyserial
```

## Çalıştırma

```bash
python main.py
```

## Dosyalar

| Dosya | Görev |
|---|---|
| `protocol.py` | ICD'deki komutlar, paket formatları, CRC16/CRC32 hesaplamaları. ICD değişirse sadece burası güncellenir. |
| `serial_handler.py` | UART üzerinden handshake, boyut bildirimi, chunk gönderme (retry ile), bitiş sinyali — arayüzden bağımsız iş mantığı. |
| `main.py` | PyQt5 penceresi: dosya seçme, port seçme, ilerleme çubuğu, log. Aktarımı arka planda `QThread` ile yürütür ki pencere donmasın. |

## Notlar / Henüz netleşmemiş noktalar (ICD Bölüm 12 — Açık Konular)

- Baud rate şu an 115200 olarak varsayıldı, kesinleşince `protocol.py`'ye taşınıp
  `serial_handler.py` içinde sabit olarak kullanılmalı.
- CRC16/CRC32 polinomları ICD'deki önerilen (tentatif) değerlerle uygulandı;
  kart tarafındaki (embedded C) implementasyonla birebir eşleştiğinden emin olunmalı
  — ikisi farklı polinom kullanırsa CRC kontrolleri hep NACK döner.