import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Bootloader GUI'))
import protocol as p
from serial_handler import BootloaderTransfer, TransferError, ConnectionLost
import serial


def firmware(size=513):
    return struct.pack('<II', 0x20001FF0, p.APP_ADDRESS + 193) + bytes((i % 251 for i in range(size - 8)))


class Serial:
    def __init__(self, response=b'', handler=None):
        self.response = bytearray(response)
        self.handler = handler
        self.sent = []
        self.baudrate = p.BASE_BAUD

    def flush(self):
        pass

    def read(self, n):
        # Deliberately fragment every response into one-byte reads.
        result = bytes(self.response[:min(n, 1)])
        del self.response[:len(result)]
        return result

    def write(self, data):
        self.sent.append(data)
        if self.handler:
            self.response.extend(self.handler(data))
        return len(data)

    def reset_input_buffer(self):
        self.response.clear()


class ProtocolTests(unittest.TestCase):
    def transfer(self, response=b'', handler=None):
        transfer = BootloaderTransfer('fake')
        transfer.ser = Serial(response, handler)
        transfer.layout_verified = True
        return transfer

    def test_crc_vectors(self):
        self.assertEqual(p.crc16_ccitt(b'123456789'), 0x29B1)
        self.assertEqual(p.crc32_standard(b'123456789'), 0xCBF43926)

    def test_start_nack_has_only_two_bytes(self):
        t = self.transfer(b'\x22\x02')
        with self.assertRaisesRegex(TransferError, 'Flash'):
            t.send_fw_size(513)

    def test_end_nack_has_only_two_bytes(self):
        with self.assertRaisesRegex(TransferError, 'CRC'):
            self.transfer(b'\x22\x01').send_fw_end(0)

    def test_ack_sequence_checked(self):
        with self.assertRaisesRegex(TransferError, 'sirasi'):
            self.transfer(b'\x21\x00\x00')._expect_ack(2)

    def test_partial_packet(self):
        frame = p.build_fw_data_packet(2, b'X')
        self.assertEqual(len(frame), 263)
        self.assertEqual(frame[:5], b'\x32\x02\x00\x01\x00')
        self.assertEqual(frame[6:-2], b'\xff' * 255)
        self.assertEqual(struct.unpack('<H', frame[-2:])[0], p.crc16_ccitt(frame[:6]))

    def test_image_validation(self):
        p.validate_firmware(firmware())
        for data in (b'', firmware(p.APP_CAPACITY + 1), struct.pack('<II', 0x20002000, 0x08000101) + bytes(505)):
            with self.assertRaises(ValueError):
                p.validate_firmware(data)

    def test_layout_gate(self):
        for version, capacity in ((1, p.APP_CAPACITY), (2, 51200)):
            t = self.transfer(struct.pack('<BBHI', p.CMD_INFO, version, capacity, p.APP_ADDRESS))
            with self.assertRaises(TransferError):
                t._verify_layout()
            self.assertFalse(t.layout_verified)
        t = self.transfer()
        t.layout_verified = False
        with self.assertRaises(TransferError):
            t.send_fw_size(513)
        self.assertEqual(t.ser.sent, [])

    def test_transfer_wire_format(self):
        received = bytearray()
        def board(data):
            if data[0] == p.SIG_HANDSHAKE_REQ:
                return bytes([p.SIG_HANDSHAKE_ACK])
            if data[0] == p.CMD_INFO:
                return struct.pack('<BBHI', p.CMD_INFO, 2, p.APP_CAPACITY, p.APP_ADDRESS)
            if data[0] == p.CMD_FW_SIZE:
                self.assertEqual(struct.unpack('<H', data[1:])[0], 513)
                return b'\x21\xff\xff'
            if data[0] == p.CMD_FW_DATA:
                seq, n = struct.unpack('<HH', data[1:5])
                self.assertEqual(struct.unpack('<H', data[-2:])[0], p.crc16_ccitt(data[:5+n]))
                received.extend(data[5:5+n])
                return struct.pack('<BH', p.CMD_ACK, seq)
            self.assertEqual(data[0], p.CMD_FW_END)
            self.assertEqual(struct.unpack('<I', data[1:])[0], p.crc32_standard(received))
            return b'\x21\xfe\xff'
        t = self.transfer(handler=board)
        progress = []
        self.assertTrue(t.run_full_transfer(firmware(), lambda a, b: progress.append((a,b))))
        self.assertEqual(received, firmware())
        self.assertEqual(progress, [(1,3), (2,3), (3,3)])

    def test_data_retry_and_abort(self):
        attempts = []
        def board(data):
            attempts.append(data)
            return b'\x22\x01' if len(attempts) == 1 else b'\x21\x00\x00'
        t = self.transfer(handler=board)
        self.assertTrue(t.send_chunk(0, bytes(256)))
        self.assertEqual(attempts[0], attempts[1])
        t = self.transfer(handler=lambda data: b'\x21\xfd\xff')
        self.assertFalse(t.send_all_chunks([bytes(256)], should_stop=lambda: True))
        self.assertEqual(t.ser.sent, [b'\xff'])

    def test_compiled_application_binary(self):
        image = ROOT / 'build/application_stm32/application_stm32.bin'
        if not image.exists():
            self.skipTest('Build firmware first')
        p.validate_firmware(image.read_bytes())

    def test_fast_negotiation_before_start(self):
        def board(data):
            if data == bytes([p.CMD_FAST_MODE]):
                self.assertEqual(t.ser.baudrate, p.BASE_BAUD)
                return b'\x21\xfc\xff'
            self.assertEqual(data, b'\x55')
            self.assertEqual(t.ser.baudrate, p.FAST_BAUD)
            return b'\x9a'
        t = self.transfer(handler=board)
        t.device_version = 3
        with patch('serial_handler.time.sleep'):
            t.select_transfer_speed(p.FAST_BAUD)
        self.assertEqual(t.ser.sent, [b'\x36', b'\x55'])

    def test_v2_fast_rejected_without_flash_commands(self):
        t = self.transfer()
        t.device_version = 2
        with self.assertRaisesRegex(TransferError, 'ST-LINK'):
            t.run_full_transfer(firmware(), do_handshake=False, data_baud=p.FAST_BAUD)
        self.assertEqual(t.ser.sent, [])

    def test_test_mode_no_delay_no_switch(self):
        t = self.transfer()
        t.device_version = 2
        with patch('serial_handler.time.sleep') as sleep:
            t.select_transfer_speed(p.BASE_BAUD)
        sleep.assert_not_called()
        self.assertEqual(t.ser.sent, [])
        self.assertEqual(t.ser.baudrate, p.BASE_BAUD)

    def test_fast_failure_does_not_send_start(self):
        t = self.transfer(handler=lambda data: b'\x21\xfc\xff' if data == b'\x36' else b'')
        t.device_version = 3
        with patch.object(t, '_expect_ack'), patch('serial_handler.time.sleep'), patch('serial_handler.time.monotonic', side_effect=[0,10]):
            with self.assertRaisesRegex(TransferError, 'doğrulanamadı'):
                t.run_full_transfer(firmware(), do_handshake=False, data_baud=p.FAST_BAUD)
        self.assertNotIn(p.build_fw_size_packet(len(firmware())), t.ser.sent)
        self.assertFalse(t.layout_verified)
        self.assertEqual(t.ser.baudrate, p.BASE_BAUD)

    def test_write_timeout_is_explained(self):
        t = self.transfer()
        t.ser.write = Mock(side_effect=serial.SerialTimeoutException('Write timeout'))
        with self.assertRaisesRegex(ConnectionLost, 'Seri bağlantı'):
            t.run_full_transfer(firmware(), do_handshake=False)

    def test_unplug_during_read_not_retried_as_nack(self):
        t = self.transfer()
        t.ser.read = Mock(side_effect=serial.SerialException('device removed'))
        with self.assertRaises(ConnectionLost):
            t.send_chunk(0, bytes(256))
        self.assertEqual(len(t.ser.sent), 1)


if __name__ == '__main__':
    unittest.main()
