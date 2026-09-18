# Sabit çalışma adresinde NEW / OLD güncellemesi

NEW daima 0x08003400 adresinden çalışır. OLD aynı imajın kopyasıdır; içindeki
reset vektörü de NEW adresini gösterir. Kurtarma, OLD'u NEW'e geri kopyalar.
UART güncellemesi sırasında uygulama çalışmaz, yalnızca bootloader çalışır.

## Kayıt durumları

1. READY: NEW geçerli; OLD önceki sürümü içerebilir.
2. BACKUP: NEW sağlam kaynak; OLD henüz geçerli sayılmaz.
3. LOADING: NEW çalıştırılamaz; OLD varsa kurtarma kaynağıdır.

`Store_Begin()` önce `Store_Recover()` çağırır. READY NEW varsa BACKUP kaydını
tamamlar. Ardından NEW'i OLD'a sayfa sayfa kopyalar (1 KB silme, 256 bayt RAM
tamponu, 16 bit Flash programlama). OLD CRC ve vektör kontrolünü geçince
LOADING kaydını tamamlar. Yalnızca bundan sonra NEW silinir.

`Store_Write()` NEW sınırlarına yazar. UART katmanı tam 256 baytlık paketler
(yalnızca son paket kısa), sıra ve CRC16 zorunluluğunu uygular. Önceden kabul
edilmiş aynı paket tekrar gelirse Flash içeriği karşılaştırılır ve yeniden
programlanmadan ACK verilir. Kısmi Flash hatasında transfer iptal durumuna
geçer; yeni START ile kurtarma/yedekleme işlemi baştan yapılır.

`Store_Finish()` NEW Flash CRC32'sini ve vektörlerini doğrular; READY kaydını
tamamlar. Sonra END ACK gönderilir ve reset atılır. CRC yalnızca gerçek imaj
boyutunda hesaplanır; UART/son halfword dolgusu dahil değildir.

## Açılışta kurtarma

`Bootloader_Run()` uygulamadan HELLO isteği olsa bile önce `Store_Recover()`
çalıştırır. BACKUP yarım kaldıysa NEW korunmuştur; OLD geçersiz sayılarak READY
kaydı oluşturulur. LOADING varsa ve OLD sağlamsa OLD tekrar NEW'e kopyalanır,
doğrulanır ve READY kaydı yazılır. Geri yükleme kesilirse OLD'a dokunulmadığından
sonraki açılışta geri yükleme baştan tekrar edilir. İlk kurulumda OLD yoksa
yarım NEW çalıştırılmaz; UART yüklemesi beklenir.

READY NEW doğrulamayı geçemezse kayıtlı sağlam OLD üzerinden kurtarma denenir.
Hiçbir güvenilir kayıt/imaj yoksa rastgele Flash verisi uygulama sayılmaz.
İşlem sırasında kayıt yazma veya kurtarma başarısızsa otomatik uygulama atlaması
yapılmaz; güncelleme modunda kalınır.

## Magic number tam olarak nerede?

Her metadata sayfasının başında bir 40 baytlık kayıt vardır:

| Ofset | Değer |
|---|---|
| +0 | magic = 0xB00710AD |
| +4 | format = 2 |
| +8 | artan kayıt nesli |
| +12 | READY=1, BACKUP=2, LOADING=3 |
| +16 / +20 | NEW boyutu / CRC32 |
| +24 / +28 | OLD boyutu / CRC32 |
| +32 | İlk 32 baytın CRC32'si |
| +36 | committed = 0xA5A5A5A5; EN SON yazılır |

Dolayısıyla magic adresleri 0x0800F800 ve 0x0800FC00; tamamlanma işaretleri
0x0800F824 ve 0x0800FC24'tür. Tek başına magic veya committed uygulamanın
geçerli olduğu anlamına gelmez; kayıt CRC'si, durum ve imaj doğrulaması gerekir.

`commit()` en son kaydın bulunduğu sayfayı korur, diğer sayfayı siler. Gövdeyi
yazdıktan sonra committed değerini ayrı ve son işlem olarak yazar. `load()` iki
kaydı doğrular ve en yeni geçerli nesli seçer. Her kayıt değişiminde sayfalar
yer değiştirir; önceki kayıt yeni kayıt tamamlanmadan silinmez. Bu sayede kayıt
silme, yarım gövde ve yarım committed yazımı durumlarında eski karar korunur.
Flash ömrü sınırlıdır; bu kayıtlar yalnızca güncelleme/kurtarma geçişlerinde yazılır.

## UART v3 (metadata formatı hâlâ 2)

Tüm çok baytlı sayılar little-endian; başlangıç 115200 8N1.
Test modu 115200'de kalır; Normal mod START öncesinde 1.000.000 baud'a geçer.

| İşlem | İstek | Başarı cevabı |
|---|---|---|
| HELLO | 55 | 9A |
| INFO | 35 | 35, version:u8=3, capacity:u16=24576, NEW:u32=0x08003400 |
| FAST_MODE | 36 (aktarım başlamadan) | 21 FC FF eski hızda; 50 ms sonra 1 Mbps; HELLO/READY ile doğrulama |
| START | 31, size:u16 | 21 FF FF (yedekleme/silme bitti) |
| DATA | 32, seq:u16, len:u16, payload[256], crc16:u16 | 21, seq:u16 |
| END | 33, image_crc32:u32 | 21 FE FF (geçerlilik kaydı tamamlandı) |
| ABORT | FF | 21 FD FF (varsa yedek geri yüklendi), sonra reset |
| RESET | 34 | Cevap yok, yazılım reseti |

NACK: 22 + tek hata baytı (1 CRC/imaj, 2 Flash/kurtarma, 3 sıra/boyut/durum).
FAST_MODE Flash'a dokunmaz. PC ACK ardından 100 ms bekler, seri port hızını
değiştirir, 1,5 saniye içinde HELLO/READY doğrulaması arar. Başarısızsa START
göndermez. Bootloader 2 saniye doğrulama gelmezse 115200'e döner. Doğrulanmış
hızlı bağlantı 10 saniye komutsuz kalırsa aktarımı pasif yapıp 115200'e döner.
GUI v2 INFO cevabını da kabul eder ancak yalnızca Test modu kullanılabilir.
START/ABORT için 30 saniye, diğer ACK okumalarında 5 saniye beklenir. GUI yalnızca
DATA'yı en fazla üç kez tekrar yollar. END ACK kaybolursa GUI başarı iddiasında
bulunmaz; reset sonrası bootloader kalıcı kayda göre karar verir.

Protokol bir senkronizasyon çerçevesi içermez; kesilen/eksik UART frame'inden
sonra gerekiyorsa reset ve yeni handshake ile baştan başlanır. Reset sonrası
aktarım otomatik devam etmez. Donanım kesintisi koruması metadata/kopya
mekanizmasıyla sağlanır; aktarımın kesintisiz sürmesi vaat edilmez.
