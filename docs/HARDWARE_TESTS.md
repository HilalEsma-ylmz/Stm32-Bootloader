# Kart üzerinde doğrulama

Bilgisayar testleri fiziksel güç kesme testinin yerini tutmaz. Aşağıdaki işlemler
kartta ayrıca uygulanmalıdır; bu değişiklik hazırlanırken karta bağlanılmadı.

1. Depodaki yeni bootloader'ı ST-LINK ile yükle, debugger'ı çalışır durumda bırak.
2. Uygulama A'yı UART ile yükle; LED davranışını gözle. Bu ilk kurulumdur.
3. LED davranışı farklı uygulama B üret. Bağlan → Aktarımı Başlat.
4. START yedeklemesi sırasında gücü kes. Güç gelince A açılmalı.
5. DATA aktarımının başında, ortasında ve sonunda ayrı ayrı güç kes.
   Açılışta OLD'tan NEW'e kurtarma bitince A açılmalı.
6. Geri yükleme sırasında tekrar güç kes. Sonraki açılışta A kurtarılmalı.
7. Yüklemeyi tamamla. B açılmalı; sonraki C yüklemesi kesilirse B geri gelmeli.
8. Durdur düğmesi: ABORT onayı ve reset sonrası önceki uygulama açılmalı.
9. Yanlış CRC gönder: END NACK; reset sonrası eski uygulama açılmalı.
10. Yanlış başlangıca linklenmiş, boş veya >24 KB dosya: GUI reddetmeli.
11. INFO desteği olmayan eski bootloader: bağlantı reddedilmeli. V2 bootloader:
    Test modu çalışmalı, Normal mod START göndermeden reddedilmeli.
12. UART DATA ACK'sini kontrollü kaybettir: aynı pakete tekrar ACK gelmeli,
    Flash yeniden programlanmamalı. Reset/ayrışan frame için yeniden handshake yap.
13. Normal modda yüksek hız doğrulanınca aynı firmware'i tamamen yükle. CRC ve
    uygulama açılışı doğru olmalı. Test moduyla logdaki toplam süreyi karşılaştır.
14. FAST_MODE onayı/hız değişimi sırasında bağlantıyı kes: START henüz gitmediği
    için NEW değişmemeli. Reset veya hız geri dönüş süresinden sonra bağlanabilmeli.
15. Normal mod DATA sırasında kesinti: önceki OLD kurtarma davranışı korunmalı.

Programlayıcıyla Flash'ı okurken kontrol noktaları:
- Bootloader 0x08000000..0x080033FF güncelleme boyunca değişmez.
- Eski NEW, START tamamlandığında 0x08009400 OLD alanında bulunur.
- Metadata sayfalarından en az bir tamamlanmış kayıt korunur.
- Sonuçta NEW CRC32'si seçilen dosyanın CRC32'siyle aynıdır.

Yeni firmware CRC'si doğru olup çalışma sırasında kilitlenirse bu sürüm bunu
otomatik tespit etmez. Watchdog + çalışma onayı ayrı bir geliştirmedir.
