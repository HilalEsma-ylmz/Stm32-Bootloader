/* Fixed-address NEW application + OLD backup. See docs/UPDATE_FLOW.md. */
#include "main.h"
#include "firmware_store.h"
#include <string.h>

extern UART_HandleTypeDef huart1;
#define UPDATE_REQUEST_ADDRESS 0x20001FF0UL
#define UPDATE_REQUEST_MAGIC 0x55504454UL
#define HELLO 0x55U
#define READY 0x9AU
#define START 0x31U
#define DATA 0x32U
#define END 0x33U
#define SOFT_RESET 0x34U
#define INFO 0x35U
#define FAST_MODE 0x36U
#define BASE_BAUD 115200UL
#define FAST_BAUD 1000000UL
#define ABORT 0xFFU
#define ACK 0x21U
#define NACK 0x22U
#define ERR_CRC 1U
#define ERR_FLASH 2U
#define ERR_SEQUENCE 3U
#define CHUNK_SIZE 256U
#define UART_TIMEOUT_MS 1500U
#define UPDATE_WINDOW_MS 7000U

static struct {
    bool active;
    uint32_t image_size, received_size;
    uint16_t expected_seq;
} transfer;

const uint8_t *Store_Read(uint32_t address) { return (const uint8_t *)address; }

static bool writable(uint32_t address, uint32_t size)
{
    if (!size) return false;
    if (address >= APP_NEW_ADDRESS && address < APP_NEW_ADDRESS + APP_CAPACITY)
        return size <= APP_NEW_ADDRESS + APP_CAPACITY - address;
    if (address >= APP_OLD_ADDRESS && address < APP_OLD_ADDRESS + APP_CAPACITY)
        return size <= APP_OLD_ADDRESS + APP_CAPACITY - address;
    if (address >= META_1_ADDRESS && address < META_1_ADDRESS + STORE_PAGE_SIZE)
        return size <= META_1_ADDRESS + STORE_PAGE_SIZE - address;
    if (address >= META_2_ADDRESS && address < META_2_ADDRESS + STORE_PAGE_SIZE)
        return size <= META_2_ADDRESS + STORE_PAGE_SIZE - address;
    return false;
}

bool Store_ErasePage(uint32_t address)
{
    if ((address % STORE_PAGE_SIZE) || !writable(address, STORE_PAGE_SIZE)) return false;
    FLASH_EraseInitTypeDef erase = {0};
    uint32_t page_error = 0U;
    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.PageAddress = address;
    erase.NbPages = 1U;
    if (HAL_FLASH_Unlock() != HAL_OK) return false;
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_WRPERR | FLASH_FLAG_PGERR);
    HAL_StatusTypeDef result = HAL_FLASHEx_Erase(&erase, &page_error);
    HAL_FLASH_Lock();
    if (result != HAL_OK || page_error != 0xFFFFFFFFUL) return false;
    for (uint32_t offset = 0; offset < STORE_PAGE_SIZE; offset += 4U)
        if (*(const volatile uint32_t *)(address + offset) != 0xFFFFFFFFUL) return false;
    return true;
}

bool Store_Program(uint32_t address, const uint8_t *data, uint32_t size)
{
    if (size > APP_CAPACITY || (address & 1U) || !writable(address, (size + 1U) & ~1U))
        return false;
    if (HAL_FLASH_Unlock() != HAL_OK) return false;
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_WRPERR | FLASH_FLAG_PGERR);
    for (uint32_t offset = 0; offset < size; offset += 2U) {
        uint16_t value = data[offset];
        value |= (uint16_t)(offset + 1U < size ? data[offset + 1U] : 0xFFU) << 8;
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, address + offset, value) != HAL_OK ||
            *(const volatile uint16_t *)(address + offset) != value) {
            HAL_FLASH_Lock(); return false;
        }
    }
    HAL_FLASH_Lock();
    return true;
}

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
    uint8_t response[] = {ACK, (uint8_t)seq, (uint8_t)(seq >> 8)};
    write_bytes(response, sizeof(response));
}
static void send_nack(uint8_t code)
{
    uint8_t response[] = {NACK, code};
    write_bytes(response, sizeof(response));
}
static uint16_t crc16(const uint8_t *data, uint32_t size)
{
    uint16_t crc = 0xFFFFU;
    while (size--) {
        crc ^= (uint16_t)*data++ << 8;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc & 0x8000U) ? (uint16_t)((crc << 1) ^ 0x1021U) : (uint16_t)(crc << 1);
    }
    return crc;
}

static void handle_start(void)
{
    uint8_t raw[2];
    if (!read_bytes(raw, sizeof(raw))) { send_nack(ERR_SEQUENCE); return; }
    uint32_t size = raw[0] | ((uint32_t)raw[1] << 8);
    if (size < APP_VECTOR_BYTES || size > APP_CAPACITY) { send_nack(ERR_SEQUENCE); return; }
    /* A START retransmission before any DATA must not back up an erased NEW. */
    if (transfer.active) {
        if (size == transfer.image_size && transfer.received_size == 0U) send_ack(0xFFFFU);
        else send_nack(ERR_SEQUENCE);
        return;
    }
    if (!Store_Begin(size)) { send_nack(ERR_FLASH); return; }
    transfer.active = true;
    transfer.image_size = size;
    transfer.received_size = 0U;
    transfer.expected_seq = 0U;
    send_ack(0xFFFFU);
}

static void handle_data(void)
{
    uint8_t frame[5U + CHUNK_SIZE], raw_crc[2];
    frame[0] = DATA;
    if (!read_bytes(frame + 1, sizeof(frame) - 1U) || !read_bytes(raw_crc, 2U)) {
        transfer.active = false; send_nack(ERR_SEQUENCE); return;
    }
    uint16_t seq = frame[1] | ((uint16_t)frame[2] << 8);
    uint16_t size = frame[3] | ((uint16_t)frame[4] << 8);
    uint16_t checksum = raw_crc[0] | ((uint16_t)raw_crc[1] << 8);
    uint32_t offset = (uint32_t)seq * CHUNK_SIZE;
    if (!transfer.active || !size || size > CHUNK_SIZE || offset >= transfer.image_size ||
        size != (transfer.image_size - offset > CHUNK_SIZE ? CHUNK_SIZE : transfer.image_size - offset)) {
        send_nack(ERR_SEQUENCE); return;
    }
    if (crc16(frame, 5U + size) != checksum) { send_nack(ERR_CRC); return; }
    /* Lost ACK: recognize identical previously committed DATA, do not reprogram. */
    if (seq < transfer.expected_seq) {
        if (memcmp(Store_Read(APP_NEW_ADDRESS + offset), frame + 5, size) == 0) send_ack(seq);
        else send_nack(ERR_SEQUENCE);
        return;
    }
    if (seq != transfer.expected_seq || offset != transfer.received_size) {
        send_nack(ERR_SEQUENCE); return;
    }
    if (!Store_Write(offset, frame + 5, size)) {
        transfer.active = false; send_nack(ERR_FLASH); return;
    }
    transfer.received_size += size;
    transfer.expected_seq++;
    send_ack(seq);
}

static void handle_end(void)
{
    uint8_t raw[4];
    if (!read_bytes(raw, sizeof(raw))) { send_nack(ERR_SEQUENCE); return; }
    uint32_t crc = raw[0] | ((uint32_t)raw[1] << 8) | ((uint32_t)raw[2] << 16) | ((uint32_t)raw[3] << 24);
    if (!transfer.active || transfer.received_size != transfer.image_size) {
        send_nack(ERR_SEQUENCE); return;
    }
    if (!Store_ImageValid(APP_NEW_ADDRESS, transfer.image_size, crc)) {
        transfer.active = false; send_nack(ERR_CRC); return;
    }
    if (!Store_Finish(transfer.image_size, crc)) {
        transfer.active = false; send_nack(ERR_FLASH); return;
    }
    transfer.active = false;
    send_ack(0xFFFEU);
    HAL_Delay(20U);
    NVIC_SystemReset();
}

/* No C stack access is allowed after replacing MSP. r0=stack, r1=entry. */
__attribute__((naked, noreturn)) static void branch_to_app(
    uint32_t stack __attribute__((unused)), uint32_t entry __attribute__((unused)))
{
    __asm volatile("movs r2, #0\n msr control, r2\n msr msp, r0\n isb\n cpsie i\n bx r1\n");
}

static void jump_to_application(void)
{
    const uint32_t *vectors = (const uint32_t *)APP_NEW_ADDRESS;
    uint32_t stack = vectors[0], entry = vectors[1];
    (void)HAL_UART_DeInit(&huart1);
    __disable_irq();
    SysTick->CTRL = 0U; SysTick->LOAD = 0U; SysTick->VAL = 0U;
    NVIC->ICER[0] = 0xFFFFFFFFUL;
    NVIC->ICPR[0] = 0xFFFFFFFFUL;
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;
    /* Bootloader linker reserves this RAM too, so its own globals are safe. */
    memcpy((void *)0x20000000UL, vectors, APP_VECTOR_BYTES);
    __HAL_RCC_SYSCFG_CLK_ENABLE();
    __HAL_SYSCFG_REMAPMEMORY_SRAM();
    __DSB(); __ISB();
    branch_to_app(stack, entry);
}

static bool set_baud(uint32_t baud)
{
    huart1.Init.BaudRate = baud;
    if (HAL_UART_Init(&huart1) != HAL_OK) return false;
    __HAL_UART_CLEAR_OREFLAG(&huart1);
    __HAL_UART_CLEAR_FEFLAG(&huart1);
    __HAL_UART_CLEAR_NEFLAG(&huart1);
    __HAL_UART_SEND_REQ(&huart1, UART_RXDATA_FLUSH_REQUEST);
    return true;
}

/* This negotiation finishes BEFORE START: no Flash is changed on failure.
 * ACK is sent at the old baud, then HELLO/READY confirms the new baud.
 * Without confirmation return to 115200 so a cable failure cannot strand us.
 */
static void handle_fast_mode(void)
{
    if (transfer.active || huart1.Init.BaudRate != BASE_BAUD) {
        send_nack(ERR_SEQUENCE); return;
    }
    send_ack(0xFFFCU);
    HAL_Delay(50U);
    if (set_baud(FAST_BAUD)) {
        uint32_t started = HAL_GetTick();
        while (HAL_GetTick() - started < 2000U) {
            uint8_t command;
            if (HAL_UART_Receive(&huart1, &command, 1U, 10U) == HAL_OK && command == HELLO) {
                const uint8_t ready = READY;
                write_bytes(&ready, 1U);
                return;
            }
        }
    }
    (void)set_baud(BASE_BAUD);
}

static void process_update_command(uint8_t command)
{
    switch (command) {
    case HELLO: { const uint8_t ready = READY; write_bytes(&ready, 1U); break; }
    case INFO: {
        /* Protocol version and layout guard: never update an old 50 KB loader. */
        const uint8_t info[] = {INFO, 3U, 0U, 0x60U, 0U, 0x34U, 0U, 8U};
        write_bytes(info, sizeof(info)); break;
    }
    case START: handle_start(); break;
    case FAST_MODE: handle_fast_mode(); break;
    case DATA: handle_data(); break;
    case END: handle_end(); break;
    case ABORT:
        transfer.active = false;
        if (!Store_Recover()) { send_nack(ERR_FLASH); break; }
        send_ack(0xFFFDU);
        HAL_Delay(20U);
        NVIC_SystemReset(); break;
    case SOFT_RESET: NVIC_SystemReset(); break;
    default: break;
    }
}

void Bootloader_Run(void)
{
    volatile uint32_t *request = (volatile uint32_t *)UPDATE_REQUEST_ADDRESS;
    bool requested = *request == UPDATE_REQUEST_MAGIC;
    *request = 0U;
    memset(&transfer, 0, sizeof(transfer));
    bool recovered = Store_Recover(); /* Recovery precedes even an update request. */
    if (recovered && Store_NewValid() && !requested) {
        uint32_t started = HAL_GetTick();
        while (HAL_GetTick() - started < UPDATE_WINDOW_MS) {
            uint8_t command;
            if (HAL_UART_Receive(&huart1, &command, 1U, 10U) == HAL_OK) {
                if (command == HELLO) { process_update_command(command); requested = true; break; }
                if (command == SOFT_RESET) NVIC_SystemReset();
            }
        }
        if (!requested) jump_to_application();
    }
    uint32_t last_command = HAL_GetTick();
    while (1) {
        uint8_t command;
        if (read_bytes(&command, 1U)) {
            process_update_command(command);
            last_command = HAL_GetTick();
        }
        /* Lost PC/cable: restore the command channel without committing NEW.
         * A subsequent START can recover the interrupted transfer from OLD. */
        if (huart1.Init.BaudRate != BASE_BAUD && HAL_GetTick() - last_command > 10000U) {
            transfer.active = false;
            (void)set_baud(BASE_BAUD);
        }
    }
}
