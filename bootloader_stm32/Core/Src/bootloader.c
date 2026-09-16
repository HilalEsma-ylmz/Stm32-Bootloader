/* STM32F030R8 UART bootloader - CubeIDE Core/Src/bootloader.c */
#include "main.h"
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

extern UART_HandleTypeDef huart1;

/* 13 KB bootloader, 50 KB application, 1 KB metadata */
#define APP_ADDRESS        0x08003400UL
#define APP_MAX_SIZE       (50UL * 1024UL)
#define META_ADDRESS       0x0800FC00UL
#define APP_FLASH_END      0x0800FBFFUL
#define APP_RAM_START      0x20000100UL
#define RAM_END            0x20002000UL
#define UPDATE_REQUEST_ADDRESS 0x20001FF0UL
#define UPDATE_REQUEST_MAGIC   0x55504454UL

#define HELLO              0x55U
#define READY              0x9AU
#define START              0x31U
#define DATA               0x32U
#define END                0x33U
#define SOFT_RESET         0x34U
#define ACK                0x21U
#define NACK               0x22U
#define ERR_CRC            0x01U
#define ERR_FLASH          0x02U
#define ERR_SEQUENCE       0x03U
#define CHUNK_SIZE         256U
#define UART_TIMEOUT_MS    1500U
#define UPDATE_WINDOW_MS   7000U
#define APP_VECTOR_WORDS   48U
#define META_MAGIC         0xB00710ADUL
#define META_VALID         0xA5A5A5A5UL

typedef struct {
    uint32_t magic;
    uint32_t image_size;
    uint32_t image_crc32;
    uint32_t valid;
} Metadata;

typedef struct {
    bool active;
    uint32_t image_size;
    uint32_t received_size;
    uint16_t expected_seq;
} Transfer;

static Transfer transfer;

static bool read_bytes(uint8_t *data, uint16_t count)
{
    return HAL_UART_Receive(&huart1, data, count, UART_TIMEOUT_MS) == HAL_OK;
}

static void write_bytes(const uint8_t *data, uint16_t count)
{
    (void)HAL_UART_Transmit(&huart1, (uint8_t *)data, count, UART_TIMEOUT_MS);
}

static void send_ack(uint16_t seq)
{
    uint8_t answer[3] = { ACK, (uint8_t)seq, (uint8_t)(seq >> 8) };
    write_bytes(answer, sizeof(answer));
}

static void send_nack(uint8_t error)
{
    uint8_t answer[2] = { NACK, error };
    write_bytes(answer, sizeof(answer));
}

/* CRC-16/CCITT-FALSE; Python GUI ile aynidir. */
static uint16_t crc16(const uint8_t *data, uint32_t length)
{
    uint16_t crc = 0xFFFFU;
    while (length--)
    {
        crc ^= (uint16_t)(*data++) << 8;
        for (uint8_t bit = 0; bit < 8; bit++)
            crc = (crc & 0x8000U) ? (uint16_t)((crc << 1) ^ 0x1021U)
                                  : (uint16_t)(crc << 1);
    }
    return crc;
}

/* IEEE CRC32; Python zlib.crc32 ile aynidir. */
static uint32_t crc32(const uint8_t *data, uint32_t length)
{
    uint32_t crc = 0xFFFFFFFFUL;
    while (length--)
    {
        crc ^= *data++;
        for (uint8_t bit = 0; bit < 8; bit++)
            crc = (crc & 1UL) ? ((crc >> 1) ^ 0xEDB88320UL) : (crc >> 1);
    }
    return ~crc;
}

/* 50 application page + 1 metadata page siler. */
static bool erase_application(void)
{
    FLASH_EraseInitTypeDef erase = {0};
    uint32_t page_error = 0;
    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.PageAddress = APP_ADDRESS;
    erase.NbPages = 51U;

    if (HAL_FLASH_Unlock() != HAL_OK) return false;
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_WRPERR | FLASH_FLAG_PGERR);
    HAL_StatusTypeDef status = HAL_FLASHEx_Erase(&erase, &page_error);
    (void)HAL_FLASH_Lock();
    return (status == HAL_OK) && (page_error == 0xFFFFFFFFUL);
}

/* F030 flash word (4 byte) olarak programlanir. */
static bool flash_write(uint32_t address, const uint8_t *data, uint16_t length)
{
    if (HAL_FLASH_Unlock() != HAL_OK) return false;

    for (uint16_t offset = 0; offset < length; offset += 4U)
    {
        uint32_t word = 0xFFFFFFFFUL;
        uint16_t remaining = (uint16_t)(length - offset);
        memcpy(&word, &data[offset], remaining >= 4U ? 4U : remaining);

        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, address + offset, word) != HAL_OK ||
            *(const uint32_t *)(address + offset) != word)
        {
            (void)HAL_FLASH_Lock();
            return false;
        }
    }
    (void)HAL_FLASH_Lock();
    return true;
}

/* START: image size al, uygulama/metadata sayfalarini sil. */
static void handle_start(void)
{
    uint8_t raw[2];
    if (!read_bytes(raw, sizeof(raw))) return;
    uint32_t size = (uint32_t)raw[0] | ((uint32_t)raw[1] << 8);

    if (size == 0U || size > APP_MAX_SIZE) { send_nack(ERR_SEQUENCE); return; }
    if (!erase_application()) { send_nack(ERR_FLASH); return; }

    transfer.active = true;
    transfer.image_size = size;
    transfer.received_size = 0U;
    transfer.expected_seq = 0U;
    send_ack(0xFFFFU);
}

/* DATA: seq:u16 + len:u16 + payload[256] + crc16:u16 */
static void handle_data(void)
{
    uint8_t header[4], payload[CHUNK_SIZE], raw_crc[2];
    uint8_t crc_input[1U + sizeof(header) + CHUNK_SIZE];

    if (!read_bytes(header, sizeof(header)) || !read_bytes(payload, sizeof(payload)) ||
        !read_bytes(raw_crc, sizeof(raw_crc))) return;

    uint16_t seq = (uint16_t)header[0] | ((uint16_t)header[1] << 8);
    uint16_t length = (uint16_t)header[2] | ((uint16_t)header[3] << 8);
    uint16_t received_crc = (uint16_t)raw_crc[0] | ((uint16_t)raw_crc[1] << 8);

    if (!transfer.active || seq != transfer.expected_seq || length == 0U ||
        length > CHUNK_SIZE || transfer.received_size + length > transfer.image_size)
    {
        send_nack(ERR_SEQUENCE);
        return;
    }

    crc_input[0] = DATA;
    memcpy(&crc_input[1], header, sizeof(header));
    memcpy(&crc_input[1 + sizeof(header)], payload, length);
    if (crc16(crc_input, 1U + sizeof(header) + length) != received_crc)
    {
        send_nack(ERR_CRC);
        return;
    }

    uint16_t write_length = (uint16_t)((length + 3U) & ~3U);
    if (!flash_write(APP_ADDRESS + transfer.received_size, payload, write_length))
    {
        send_nack(ERR_FLASH);
        return;
    }
    transfer.received_size += length;
    transfer.expected_seq++;
    send_ack(seq);
}

/* END: tum image CRC32 kontrolu ardindan valid metadata yazimi. */
static void handle_end(void)
{
    uint8_t raw[4];
    Metadata metadata;
    if (!read_bytes(raw, sizeof(raw))) return;

    uint32_t expected_crc = (uint32_t)raw[0] | ((uint32_t)raw[1] << 8) |
                            ((uint32_t)raw[2] << 16) | ((uint32_t)raw[3] << 24);
    if (!transfer.active || transfer.received_size != transfer.image_size ||
        crc32((const uint8_t *)APP_ADDRESS, transfer.image_size) != expected_crc)
    {
        transfer.active = false;
        send_nack(ERR_CRC);
        return;
    }

    metadata.magic = META_MAGIC;
    metadata.image_size = transfer.image_size;
    metadata.image_crc32 = expected_crc;
    metadata.valid = META_VALID;
    if (!flash_write(META_ADDRESS, (const uint8_t *)&metadata, sizeof(metadata)))
    {
        transfer.active = false;
        send_nack(ERR_FLASH);
        return;
    }
    transfer.active = false;
    send_ack(0xFFFEU);
    HAL_Delay(20U);
    NVIC_SystemReset();
}

static bool application_is_valid(void)
{
    const Metadata *metadata = (const Metadata *)META_ADDRESS;
    uint32_t app_msp = *(const uint32_t *)APP_ADDRESS;
    uint32_t app_reset = *(const uint32_t *)(APP_ADDRESS + 4U);

    if (metadata->magic != META_MAGIC || metadata->valid != META_VALID ||
        metadata->image_size == 0U || metadata->image_size > APP_MAX_SIZE)
        return false;

    if (crc32((const uint8_t *)APP_ADDRESS, metadata->image_size) != metadata->image_crc32)
        return false;

    if (app_msp < APP_RAM_START || app_msp > RAM_END ||
        (app_reset & 1U) == 0U || (app_reset & ~1UL) < APP_ADDRESS ||
        (app_reset & ~1UL) > APP_FLASH_END)
        return false;

    return true;
}

static void jump_to_application(void)
{
    const uint32_t *app_vectors = (const uint32_t *)APP_ADDRESS;
    uint32_t *ram_vectors = (uint32_t *)0x20000000UL;
    uint32_t app_msp = app_vectors[0];
    uint32_t app_reset = app_vectors[1];

    __disable_irq();
    SysTick->CTRL = 0U;
    (void)HAL_UART_DeInit(&huart1);

    for (uint32_t index = 0U; index < APP_VECTOR_WORDS; index++)
        ram_vectors[index] = app_vectors[index];

    __HAL_RCC_SYSCFG_CLK_ENABLE();
    __HAL_SYSCFG_REMAPMEMORY_SRAM();
    __DSB();
    __ISB();
    __set_MSP(app_msp);
    __enable_irq();
    ((void (*)(void))app_reset)();

    while (1) { }
}

static void process_update_command(uint8_t command)
{
    switch (command)
    {
        case HELLO: { const uint8_t ready = READY; write_bytes(&ready, 1U); break; }
        case START: handle_start(); break;
        case DATA:  handle_data();  break;
        case END:   handle_end();   break;
        case SOFT_RESET: NVIC_SystemReset(); break;
        default: break;
    }
}

static void update_mode_forever(void)
{
    uint8_t command;

    while (1)
    {
        if (!read_bytes(&command, 1U)) continue;
        process_update_command(command);
    }
}

static bool update_requested_within_window(void)
{
    const uint32_t start_tick = HAL_GetTick();
    uint8_t command;

    while ((HAL_GetTick() - start_tick) < UPDATE_WINDOW_MS)
    {
        if (HAL_UART_Receive(&huart1, &command, 1U, 10U) == HAL_OK && command == HELLO)
        {
            const uint8_t ready = READY;
            write_bytes(&ready, 1U);
            return true;
        }
    }
    return false;
}

static bool update_requested_by_application(void)
{
    volatile uint32_t *request = (volatile uint32_t *)UPDATE_REQUEST_ADDRESS;
    bool requested = (*request == UPDATE_REQUEST_MAGIC);

    *request = 0U;
    return requested;
}

/* Valid app varsa 7 saniye update istegi bekler, yoksa app'e gecer. */
void Bootloader_Run(void)
{
    memset(&transfer, 0, sizeof(transfer));

    if (update_requested_by_application())
        update_mode_forever();

    if (!application_is_valid())
        update_mode_forever();

    if (update_requested_within_window())
        update_mode_forever();

    jump_to_application();
}
