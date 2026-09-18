# UART Firmware Sender — Normal / Test

24 KB NEW / 24 KB OLD duzenindeki bootloader ile kullanilir.

Kurulum: pip install -r requirements.txt

Calistirma: python main.py

Baglan -> .bin Sec -> Aktarimi Baslat. START sirasinda yedekleme yapilir.
Normal mod: 1.000.000 baud (bootloader v3 gerekir).
Test modu: 115200 baud, onceki hiz, ek gecikme yok (v2/v3).
Handshake daima 115200 ile baslar; hiz degisimi START'tan once dogrulanir.
Durdur, karttan OLD kurtarma onayi bekler. Reset komutunun ayri onayi yoktur.

INFO destegi olmayan eski bootloader reddedilir; v2 sadece Test modunu destekler.
Bos, yanlis adrese derlenmis veya 24 KB uzeri firmware reddedilir.

[Bellek duzeni ve kurulum](../README.md)

[Protokol ve kurtarma akisi](../docs/UPDATE_FLOW.md)
