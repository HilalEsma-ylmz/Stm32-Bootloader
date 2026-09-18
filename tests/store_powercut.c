/* Runs the production firmware_store.c with simulated Flash and power cuts. */
#include "firmware_store.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>

#define BASE 0x08000000UL
static uint8_t flash[65536], saved[65536], recovery_saved[65536];
static uint8_t a[769], b[1281], c[513];
static unsigned operations, cut_at, cut_mode;
static jmp_buf power_off;
static unsigned cases;

const uint8_t *Store_Read(uint32_t address)
{
    assert(address >= BASE && address < BASE + sizeof(flash));
    return flash + address - BASE;
}
static void before(void)
{
    ++operations;
    if (operations == cut_at && cut_mode == 0) longjmp(power_off, 1);
}
static void after(void)
{
    if (operations == cut_at) longjmp(power_off, 1);
}
bool Store_ErasePage(uint32_t address)
{
    assert(address % STORE_PAGE_SIZE == 0);
    assert((address >= APP_NEW_ADDRESS && address < APP_OLD_ADDRESS + APP_CAPACITY) ||
           address == META_1_ADDRESS || address == META_2_ADDRESS);
    before();
    uint8_t *p = flash + address - BASE;
    memset(p, 0xFF, operations == cut_at && cut_mode == 1 ? STORE_PAGE_SIZE / 2 : STORE_PAGE_SIZE);
    after();
    return true;
}
bool Store_Program(uint32_t address, const uint8_t *data, uint32_t size)
{
    assert(address % 2 == 0 && address >= APP_NEW_ADDRESS && address + size <= BASE + sizeof(flash));
    for (uint32_t i = 0; i < size; i += 2) {
        before();
        uint8_t *p = flash + address - BASE + i;
        uint8_t lo = data[i], hi = i + 1 < size ? data[i + 1] : 0xFF;
        assert((p[0] & lo) == lo && (p[1] & hi) == hi);
        p[0] &= lo;
        if (!(operations == cut_at && cut_mode == 1)) p[1] &= hi;
        after();
    }
    return true;
}
static void image(uint8_t *data, unsigned size, uint8_t seed)
{
    for (unsigned i = 0; i < size; ++i) data[i] = (uint8_t)(seed + i * 17U);
    uint32_t vectors[2] = { APP_RAM_END, APP_NEW_ADDRESS + APP_VECTOR_BYTES + 1U };
    memcpy(data, vectors, sizeof(vectors));
}
static void download(const uint8_t *data, unsigned size)
{
    assert(Store_Begin(size));
    for (unsigned offset = 0; offset < size; offset += 256) {
        unsigned n = size - offset > 256 ? 256 : size - offset;
        assert(Store_Write(offset, data + offset, n));
    }
    assert(Store_Finish(size, Store_Crc32(data, size)));
}
static int matches(const uint8_t *data, unsigned size)
{
    return memcmp(Store_Read(APP_NEW_ADDRESS), data, size) == 0;
}
static void guards(void)
{
    for (unsigned i = 0; i < APP_NEW_ADDRESS - BASE; ++i) assert(flash[i] == 0xFF);
    for (unsigned i = 0xF400; i < 0xF800; ++i) assert(flash[i] == 0xFF);
}
static void cuts_update(const uint8_t *old, unsigned old_size, const uint8_t *next, unsigned next_size)
{
    memcpy(saved, flash, sizeof(flash));
    operations = 0; cut_at = 0;
    download(next, next_size);
    unsigned count = operations;
    for (cut_mode = 0; cut_mode < 3; ++cut_mode) {
        for (cut_at = 1; cut_at <= count; ++cut_at) {
            unsigned target = cut_at;
            memcpy(flash, saved, sizeof(flash)); operations = 0;
            if (setjmp(power_off) == 0) download(next, next_size);
            cut_at = 0;
            assert(Store_Recover());
            assert(Store_NewValid());
            assert(matches(old, old_size) || matches(next, next_size));
            guards(); ++cases;
            cut_at = target;
        }
    }
    cut_at = 0;
    memcpy(flash, saved, sizeof(flash));
    download(next, next_size);
}
int main(int argc, char **argv)
{
    memset(flash, 0xFF, sizeof(flash));
    image(a, sizeof(a), 11); image(b, sizeof(b), 73); image(c, sizeof(c), 141);
    assert(Store_Recover() && !Store_NewValid());
    assert(!Store_Begin(0) && !Store_Begin(APP_CAPACITY + 1));
    download(a, sizeof(a));
    cuts_update(a, sizeof(a), b, sizeof(b));
    cuts_update(b, sizeof(b), c, sizeof(c)); /* Existing OLD + alternating records. */

    /* Power fails again while restoring after an interrupted update. */
    assert(Store_Begin(sizeof(b)));
    assert(Store_Write(0, b, 256));
    memcpy(recovery_saved, flash, sizeof(flash));
    operations = 0;
    assert(Store_Recover());
    unsigned recovery_ops = operations;
    for (cut_mode = 0; cut_mode < 3; ++cut_mode) {
        for (cut_at = 1; cut_at <= recovery_ops; ++cut_at) {
            unsigned target = cut_at;
            memcpy(flash, recovery_saved, sizeof(flash)); operations = 0;
            if (setjmp(power_off) == 0) assert(Store_Recover());
            cut_at = 0;
            assert(Store_Recover() && Store_NewValid() && matches(c, sizeof(c)));
            guards(); ++cases; cut_at = target;
        }
    }
    cut_at = 0;
    assert(Store_Begin(sizeof(a)));
    assert(Store_Write(0, a, sizeof(a)));
    assert(!Store_Finish(sizeof(a), Store_Crc32(a, sizeof(a)) ^ 1U));
    assert(Store_Recover() && matches(c, sizeof(c)));

    /* Legacy metadata import, including a cut at each import operation. */
    memset(flash, 0xFF, sizeof(flash));
    memcpy(flash + APP_NEW_ADDRESS - BASE, a, sizeof(a));
    uint32_t legacy[4] = {STORE_MAGIC, sizeof(a), Store_Crc32(a, sizeof(a)), STORE_COMMITTED};
    memcpy(flash + META_2_ADDRESS - BASE, legacy, sizeof(legacy));
    memcpy(saved, flash, sizeof(flash));
    operations = 0; assert(Store_Recover()); unsigned migration_ops = operations;
    for (cut_mode = 0; cut_mode < 3; ++cut_mode) {
        for (cut_at = 1; cut_at <= migration_ops; ++cut_at) {
            unsigned target = cut_at;
            memcpy(flash, saved, sizeof(flash)); operations = 0;
            if (setjmp(power_off) == 0) assert(Store_Recover());
            cut_at = 0;
            assert(Store_Recover() && Store_NewValid() && matches(a, sizeof(a)));
            ++cases; cut_at = target;
        }
    }
    cut_at = 0;
    /* Legacy >24 KB is not silently truncated/erased. */
    memcpy(flash, saved, sizeof(flash));
    legacy[1] = APP_CAPACITY + 1;
    memcpy(flash + META_2_ADDRESS - BASE, legacy, sizeof(legacy));
    operations = 0; assert(!Store_Begin(sizeof(b))); assert(operations == 0);


    /* Installed legacy MSP remains recoverable, but not accepted in new uploads. */
    memset(flash, 0xFF, sizeof(flash));
    uint32_t legacy_msp = 0x20002000UL;
    memcpy(a, &legacy_msp, sizeof(legacy_msp));
    memcpy(flash + APP_NEW_ADDRESS - BASE, a, sizeof(a));
    legacy[1] = sizeof(a); legacy[2] = Store_Crc32(a, sizeof(a));
    memcpy(flash + META_2_ADDRESS - BASE, legacy, sizeof(legacy));
    assert(Store_Recover() && Store_NewValid());
    cuts_update(a, sizeof(a), b, sizeof(b));
    assert(Store_Begin(sizeof(a)) && Store_Write(0, a, sizeof(a)));
    assert(!Store_Finish(sizeof(a), Store_Crc32(a, sizeof(a))));
    assert(Store_Recover() && matches(b, sizeof(b)));
    legacy_msp = 0x20002008UL;
    memcpy(a, &legacy_msp, sizeof(legacy_msp));
    memcpy(flash + APP_NEW_ADDRESS - BASE, a, sizeof(a));
    assert(!Store_ImageValid(APP_NEW_ADDRESS, sizeof(a), Store_Crc32(a, sizeof(a))));
    image(a, sizeof(a), 11);

    /* First install interruption: no fallback exists; never boot a partial NEW. */
    memset(flash, 0xFF, sizeof(flash));
    assert(Store_Begin(sizeof(a)) && Store_Write(0, a, 256));
    assert(Store_Recover() && !Store_NewValid());
    download(a, sizeof(a)); assert(Store_NewValid());
    if (argc == 2) {
        FILE *input = fopen(argv[1], "rb");
        assert(input && fread(flash, 1, sizeof(flash), input) == sizeof(flash));
        fclose(input);
        operations = 0;
        assert(Store_Recover() && Store_NewValid() && operations == 0);
        memcpy(recovery_saved, flash, sizeof(flash));
        assert(Store_Begin(sizeof(b)) && Store_Write(0, b, 256));
        assert(Store_Recover() && Store_NewValid());
        assert(memcmp(flash + APP_NEW_ADDRESS - BASE,
                      recovery_saved + APP_NEW_ADDRESS - BASE, 7772) == 0);
        puts("PASS: actual board snapshot boots and survives interrupted replacement");
    }
    printf("PASS: %u power-cut cases on production firmware_store.c; recovery, migration, CRC, bounds\n", cases);
    return 0;
}
