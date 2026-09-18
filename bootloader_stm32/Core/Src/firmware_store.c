/* Power-cut safe fixed-address firmware store.
 * READY: NEW is executable; OLD may hold the previous version.
 * BACKUP: NEW is authoritative, OLD must not be used.
 * LOADING: OLD is authoritative, NEW must not be executed.
 * Every state transition commits to the other metadata page before any
 * destructive operation. OLD is never executed; recovery copies it to NEW.
 */
#include "firmware_store.h"
#include <stddef.h>
#include <string.h>

#define FORMAT 2UL
#define READY 1UL
#define BACKUP 2UL
#define LOADING 3UL
typedef struct {
    uint32_t magic, format, generation, state;
    uint32_t new_size, new_crc, old_size, old_crc;
    uint32_t record_crc, committed;
} Record;
_Static_assert(sizeof(Record) == 40, "Metadata layout changed");

uint32_t Store_Crc32(const uint8_t *data, uint32_t size)
{
    uint32_t crc = 0xFFFFFFFFUL;
    while (size--) {
        crc ^= *data++;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1U) ? 0xEDB88320UL : 0U);
    }
    return ~crc;
}

static bool size_valid(uint32_t size)
{
    return size >= APP_VECTOR_BYTES && size <= APP_CAPACITY;
}

bool Store_ImageValid(uint32_t address, uint32_t size, uint32_t crc)
{
    uint32_t vectors[2];
    if ((address != APP_NEW_ADDRESS && address != APP_OLD_ADDRESS) || !size_valid(size))
        return false;
    memcpy(vectors, Store_Read(address), sizeof(vectors));
    /* OLD is linked to NEW. Installed legacy images may use the physical
     * SRAM top as MSP. Fresh uploads must reserve the reset mailbox. */
    return vectors[0] > APP_RAM_START && (vectors[0] <= APP_RAM_END || vectors[0] == 0x20002000UL) &&
        (vectors[0] & 7U) == 0U && (vectors[1] & 1U) != 0U &&
        (vectors[1] & ~1UL) >= APP_NEW_ADDRESS + APP_VECTOR_BYTES &&
        (vectors[1] & ~1UL) < APP_NEW_ADDRESS + size &&
        Store_Crc32(Store_Read(address), size) == crc;
}

static bool record_valid(const Record *r)
{
    return r->magic == STORE_MAGIC && r->format == FORMAT &&
        r->committed == STORE_COMMITTED &&
        r->state >= READY && r->state <= LOADING &&
        (r->new_size == 0U || size_valid(r->new_size)) &&
        (r->old_size == 0U || size_valid(r->old_size)) &&
        (r->state == LOADING || size_valid(r->new_size)) &&
        (r->state != BACKUP || r->old_size == 0U) &&
        r->record_crc == Store_Crc32((const uint8_t *)r, offsetof(Record, record_crc));
}

static uint32_t load(Record *r)
{
    Record a, b;
    memcpy(&a, Store_Read(META_1_ADDRESS), sizeof(a));
    memcpy(&b, Store_Read(META_2_ADDRESS), sizeof(b));
    bool av = record_valid(&a), bv = record_valid(&b);
    if (!av && !bv) { memset(r, 0, sizeof(*r)); return 0U; }
    if (bv && (!av || (int32_t)(b.generation - a.generation) > 0)) {
        *r = b; return META_2_ADDRESS;
    }
    *r = a; return META_1_ADDRESS;
}

static bool commit(Record r)
{
    Record previous;
    uint32_t current = load(&previous);
    uint32_t target = current == META_1_ADDRESS ? META_2_ADDRESS : META_1_ADDRESS;
    r.magic = STORE_MAGIC;
    r.format = FORMAT;
    r.generation = current ? previous.generation + 1U : 1U;
    r.record_crc = Store_Crc32((const uint8_t *)&r, offsetof(Record, record_crc));
    r.committed = STORE_COMMITTED;
    /* Never erase the current record. Commit marker is a separate last write. */
    if (!Store_ErasePage(target) ||
        !Store_Program(target, (const uint8_t *)&r, offsetof(Record, committed)) ||
        !Store_Program(target + offsetof(Record, committed),
                       (const uint8_t *)&r.committed, sizeof(r.committed))) return false;
    Record check;
    memcpy(&check, Store_Read(target), sizeof(check));
    return record_valid(&check) && memcmp(&r, &check, sizeof(r)) == 0;
}

static bool erase_slot(uint32_t address)
{
    for (uint32_t offset = 0; offset < APP_CAPACITY; offset += STORE_PAGE_SIZE)
        if (!Store_ErasePage(address + offset)) return false;
    return true;
}

static bool copy_image(uint32_t source, uint32_t destination, uint32_t size)
{
    uint8_t chunk[256];
    for (uint32_t page = 0; page < size; page += STORE_PAGE_SIZE) {
        if (!Store_ErasePage(destination + page)) return false;
        for (uint32_t offset = page; offset < page + STORE_PAGE_SIZE && offset < size;
             offset += sizeof(chunk)) {
            uint32_t n = size - offset;
            if (n > sizeof(chunk)) n = sizeof(chunk);
            memcpy(chunk, Store_Read(source + offset), n);
            if (!Store_Program(destination + offset, chunk, n)) return false;
        }
    }
    return true;
}

/* Original layout: magic, image_size, image_crc32, valid at META_2.
 * A legacy image >24 KB overlaps the new backup layout: fail closed, no erase.
 */
static bool migrate(void)
{
    Record r;
    if (load(&r)) return true;
    uint32_t legacy[4];
    memcpy(legacy, Store_Read(META_2_ADDRESS), sizeof(legacy));
    if (legacy[0] != STORE_MAGIC || legacy[3] != STORE_COMMITTED) return true;
    if (!Store_ImageValid(APP_NEW_ADDRESS, legacy[1], legacy[2])) return false;
    memset(&r, 0, sizeof(r));
    r.state = READY; r.new_size = legacy[1]; r.new_crc = legacy[2];
    return commit(r); /* First commit is META_1; legacy META_2 remains intact. */
}

bool Store_Recover(void)
{
    Record r;
    if (!migrate()) return false;
    if (!load(&r)) return true; /* Blank device: stay in updater until first install. */
    if (r.state != LOADING && Store_ImageValid(APP_NEW_ADDRESS, r.new_size, r.new_crc)) {
        if (r.state == READY) return true;
        /* Interrupted backup: OLD is incomplete, NEW was never touched. */
        r.state = READY; r.old_size = 0U; r.old_crc = 0U;
        return commit(r);
    }
    if (Store_ImageValid(APP_OLD_ADDRESS, r.old_size, r.old_crc)) {
        if (!copy_image(APP_OLD_ADDRESS, APP_NEW_ADDRESS, r.old_size) ||
            !Store_ImageValid(APP_NEW_ADDRESS, r.old_size, r.old_crc)) return false;
        r.new_size = r.old_size; r.new_crc = r.old_crc; r.state = READY;
        return commit(r);
    }
    /* Interrupted first install has no previous firmware. */
    return r.state == LOADING && r.old_size == 0U;
}

bool Store_NewValid(void)
{
    Record r;
    return load(&r) && r.state == READY &&
        Store_ImageValid(APP_NEW_ADDRESS, r.new_size, r.new_crc);
}

bool Store_Begin(uint32_t size)
{
    Record r;
    if (!size_valid(size) || !Store_Recover()) return false;
    load(&r);
    if (Store_NewValid()) {
        r.state = BACKUP; r.old_size = 0U; r.old_crc = 0U;
        if (!commit(r)) return false; /* Invalidate OLD before touching it. */
        if (!copy_image(APP_NEW_ADDRESS, APP_OLD_ADDRESS, r.new_size) ||
            !Store_ImageValid(APP_OLD_ADDRESS, r.new_size, r.new_crc)) return false;
        r.old_size = r.new_size; r.old_crc = r.new_crc;
    }
    r.state = LOADING; r.new_size = 0U; r.new_crc = 0U;
    /* A reset from this point must restore OLD, even if NEW still looks valid. */
    return commit(r) && erase_slot(APP_NEW_ADDRESS);
}

bool Store_Write(uint32_t offset, const uint8_t *data, uint32_t size)
{
    Record r;
    return load(&r) && r.state == LOADING && (offset & 1U) == 0U &&
        offset < APP_CAPACITY && size > 0U && size <= APP_CAPACITY - offset &&
        Store_Program(APP_NEW_ADDRESS + offset, data, size);
}

bool Store_Finish(uint32_t size, uint32_t crc)
{
    Record r;
    if (!load(&r) || r.state != LOADING || !Store_ImageValid(APP_NEW_ADDRESS, size, crc))
        return false;
    uint32_t msp;
    memcpy(&msp, Store_Read(APP_NEW_ADDRESS), sizeof(msp));
    if (msp > APP_RAM_END) return false; /* Fresh uploads reserve the mailbox. */
    r.new_size = size; r.new_crc = crc; r.state = READY;
    return commit(r);
}
