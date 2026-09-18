# STM32 UART Bootloader — NEW / OLD

STM32F030R8 (64 KB Flash / 8 KB SRAM) için UART güncelleme sistemi.
Tek application projesi vardır. Uygulama daima NEW adresinde çalışır;
OLD, kurtarma için saklanan birebir kopyadır ve doğrudan çalıştırılmaz.

## Bellek düzeni

| Bölge | Başlangıç | Son adres (dahil) | Boyut |
|---|---|---|---|
| Bootloader | 0x08000000 | 0x080033FF | 13 KB |
| NEW | 0x08003400 | 0x080093FF | 24 KB |
| OLD | 0x08009400 | 0x0800F3FF | 24 KB |
| Kullanılmayan | 0x0800F400 | 0x0800F7FF | 1 KB |
| Metadata 1 | 0x0800F800 | 0x0800FBFF | 1 KB |
| Metadata 2 | 0x0800FC00 | 0x0800FFFF | 1 KB |

İki projenin linker dosyası da SRAM'in ilk 256 baytını vektör tablosuna,
son 16 baytını güncelleme isteğine ayırır.

## Kurulum ve kullanım

1. CubeIDE'de bu depodaki `bootloader_stm32` ve `application_stm32` projelerini
   import et. Properties → Resource → Location ile eski masaüstü kopyasını
   değil bu depoyu derlediğini kontrol et.
2. Refresh ve Clean/Build yap. Yeni `firmware_store.c` dosyasının derlendiğini
   kontrol et. Bootloader Debug optimizasyonu boyut için `-Os` yapıldı.
3. Yeni bootloader'ı ST-LINK ile 0x08000000 başlangıcına yükle. Bu adım UART
   bootloader'ı kendisini güncelleyemediği için gereklidir. Debugger işlemciyi
   durdurursa Resume yap veya debug oturumundan çıkıp kartı resetle.
4. Application'ı yeniden derle: başlangıç 0x08003400, en fazla 24 KB.
   Application Debug yapılandırması `.bin` üretir. OLD için ayrı derleme yoktur.
5. PC'de `pip install -r "Bootloader GUI/requirements.txt"` ve
   `python "Bootloader GUI/main.py"` çalıştır.
6. USB-TTL TX → PA10, RX ← PA9, ortak GND, uygun 3.3 V sinyal seviyesi.
   Kart USB'den besleniyorsa dönüştürücünün VCC bağlantısı gerekmez.
7. 115200 baud ile Bağlan → application `.bin` seç → aktarım modunu seç → Aktarımı Başlat.
   START sırasında yedekleme/silme yapılır; PC en fazla 30 saniye bekler.
8. END onayından sonra kart resetlenir. Geçerli NEW varsa 7 saniyelik
   güncelleme penceresinin ardından uygulama başlar.

### Normal ve Test modu

- **Normal (varsayılan): 1.000.000 baud.** Bağlantı 115200 ile kurulur; START
  öncesinde v3 bootloader ile hız değişimi doğrulanır. Application'ın UART hızı
  115200 olarak kalır. Bootloader'ı tekrar derleyip ST-LINK ile yüklemek gerekir.
- **Test: 115200 baud.** Önceki aktarım hızı aynen korunur; yapay gecikme eklenmez.
  V2 bootloader ile de kullanılabilir. Normal mod v2 kartta silmeye başlamadan reddedilir.
- Hızlı bağlantı doğrulanmazsa START gönderilmez. Tekrar bağlanmadan önce gerekirse
  resetle; otomatik olarak daha düşük hızda sessizce yükleme yapılmaz.
- Bootloader hız değişimini 2 saniye içinde doğrulayamazsa 115200'e döner.
  Doğrulanmış hızlı bağlantıda 10 saniye komut gelmezse de 115200'e döner ve
  aktarımı pasif yapar. Yarım NEW bu nedenle geçerli sayılmaz; yeniden START veya
  reset sonrası kurtarma gerekir.
- Kablo kopması/yazma timeout'u bağlantı hatası olarak açıklanır. GUI kaydında
  yedekleme dahil toplam güncelleme süresi gösterilir.

Waveshare USB TO TTL (B), SKU 21550, CH343G tabanlıdır; üretici 6 Mbps'ye kadar
destek listeler: https://www.waveshare.com/product/iot-communication/wired-comm-converter/usb-to-ttl-b.htm
Burada 1 Mbps seçilmiştir; gerçek hat kararlılığı kartta test edilmelidir.
Baud oranı yaklaşık 8,68 kat artsa da Flash işlemleri ve ACK gecikmeleri nedeniyle
toplam güncellemenin aynı oranda hızlanacağı garanti edilmez.

İlk kurulumda henüz eski firmware yoktur: yarım kalırsa bootloader yükleme
bekler. Bir başarılı kurulumdan sonraki güncellemelerde yedekleme devrededir.
ST-LINK ile yalnızca application yüklemek metadata oluşturmaz; ilk uygulamayı
UART üzerinden yükle. ST-LINK mass erase işlemi OLD ve metadata dahil her şeyi
siler; normal güncellemelerde ST-LINK kullanma.

## Eski 50 KB düzenden geçiş

Eski metadata doğruysa ve uygulama yeni sınırlara (24 KB, stack/vectors) uyuyorsa
ilk açılışta otomatik v2 kaydı oluşturulur. Kaynak legacy metadata, yeni kayıt
tamamlanana kadar korunur. 24 KB'den büyük veya yeni vektör kontrolüne uymayan
legacy imaj otomatik silinmez; START reddedilir. Böyle bir kartta firmware'i
yeniden derleyip planlı temiz kurulum gerekir; temiz kurulum eski yedeği korumaz.
Eski bootloader v2 INFO cevabını vermez; yeni GUI START göndermeden bağlantıyı
reddeder. Önce bootloader'ı güncelle.

## Dosyalar

- `bootloader_stm32/Core/Src/firmware_store.c`: yedekleme, metadata, kurtarma.
- `bootloader_stm32/Core/Inc/firmware_store.h`: Flash yerleşimi ve API.
- `bootloader_stm32/Core/Src/bootloader.c`: UART, Flash HAL sürücüsü, uygulamaya geçiş.
- `application_stm32/Core/Src/main.c`: LED uygulaması ve HELLO/reset dinleyicisi.
- `Bootloader GUI/`: PC arayüzü, paketler ve seri bağlantı.
- [Güncelleme akışı ve metadata](docs/UPDATE_FLOW.md).
- [Kart üzerinde test adımları](docs/HARDWARE_TESTS.md).

## Doğrulama

`python tools/build_firmware.py` CubeIDE ARM GCC ile iki projeyi `build/` altında
derler. Gerekirse `--tool-bin PATH` ile compiler dizini verilir. Bu işlem karta
yazmaz. `python -m unittest discover -s tests -p "test_*.py"` PC protokol testlerini
çalıştırır. `tests/run_store_tests.cmd` Visual Studio C derleyicisiyle gerçek
`firmware_store.c` kodunu Flash simülasyonunda çalıştırır.

Kesinti testleri, hedef sayfanın yarım silinmesini ve 16 bit yazmanın yarım
kalmasını taklit eder. Fiziksel kartın brownout/Flash elektriksel davranışı ve
UART zamanlaması ayrıca test edilmelidir. CRC bütünlük kontrolüdür, imza değildir.
Bu sürüm yeni firmware'in çalışma sağlığını onaylatmaz; CRC'si doğru fakat
çalışırken kilitlenen bir firmware için otomatik watchdog rollback yoktur.
