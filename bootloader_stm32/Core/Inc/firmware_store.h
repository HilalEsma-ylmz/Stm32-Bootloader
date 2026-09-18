#ifndef FIRMWARE_STORE_H
#define FIRMWARE_STORE_H
#include <stdbool.h>
#include <stdint.h>

#define APP_NEW_ADDRESS 0x08003400UL
#define APP_OLD_ADDRESS 0x08009400UL
#define APP_CAPACITY (24UL * 1024UL)
#define STORE_PAGE_SIZE 1024UL
#define META_1_ADDRESS 0x0800F800UL
#define META_2_ADDRESS 0x0800FC00UL
#define APP_RAM_START 0x20000100UL
#define APP_RAM_END 0x20001FF0UL
#define APP_VECTOR_BYTES 192UL
#define STORE_MAGIC 0xB00710ADUL
#define STORE_COMMITTED 0xA5A5A5A5UL

/* Port functions: never erase/program outside the two slots/metadata pages. */
const uint8_t *Store_Read(uint32_t address);
bool Store_ErasePage(uint32_t address);
bool Store_Program(uint32_t address, const uint8_t *data, uint32_t size);

uint32_t Store_Crc32(const uint8_t *data, uint32_t size);
bool Store_ImageValid(uint32_t address, uint32_t size, uint32_t crc);
/* Boot/restart recovery, including old-format metadata migration. */
bool Store_Recover(void);
bool Store_NewValid(void);
bool Store_Begin(uint32_t size);
bool Store_Write(uint32_t offset, const uint8_t *data, uint32_t size);
bool Store_Finish(uint32_t size, uint32_t crc);
#endif
