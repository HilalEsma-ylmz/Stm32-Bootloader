# Temiz derleme
CubeIDE debug oturumunu durdurun ve devam eden derlemenin bitmesini bekleyin.
Proje kokundeki terminalde: python tools/clean_build.py

Bu komut sadece iki projenin Debug/Release klasorlerini ve kokteki build klasorunu
siler, iki projeyi bastan derler. Kaynaklar ve kart bellegi silinmez.
Basarili sonuc alindiktan sonra GUI'de yeniden secilecek dosya:
build/application_stm32/application_stm32.bin
Bootloader: build/bootloader_stm32/bootloader_stm32.elf
Bu arac -Os kullanir; CubeIDE Debug ayarindan farkli boyut uretebilir.
CubeIDE ile calisiliyorsa Project > Clean, sonra ilgili projede Build Project
kullanin; o durumda GUI'de yeni Debug/application_stm32.bin dosyasini secin.
GUI acikken dosya yeniden derlenirse .bin Sec ile tekrar yukleyin.
Normal app degisikliginde bootloader yeniden yuklenmez.

Eski kurulu uygulamalarda MSP=0x20002000 degeri kurtarma icin kabul edilir.
Yeni yuklemeler RAM sonunda guncelleme istegi icin ayrilan 16 bayti korumali:
MSP <= 0x20001FF0. GUI ve firmware Store_Finish bunu kontrol eder.
Uyumluluk eski uygulamanin RAM kullanimini degistirmez; yeni derleme yuklenene
kadar eski uygulamanin reset istegi alani rezerve edilmis sayilmaz.
