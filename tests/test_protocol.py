import socket
import unittest

from protocol import ACK, RECORD_ID, recv_exact, recv_header, send_header


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_header(self):
        a, b = socket.socketpair()
        try:
            send_header(a, {'type': 'hello', 'mode': 'individual'})
            self.assertEqual(recv_header(b)['type'], 'hello')
        finally:
            a.close()
            b.close()

    def test_eof(self):
        a, b = socket.socketpair()
        try:
            a.sendall(b'abc')
            a.shutdown(socket.SHUT_WR)
            with self.assertRaises(EOFError):
                recv_exact(b, 5)
        finally:
            a.close()
            b.close()

    def test_sequence_packet(self):
        packed = RECORD_ID.pack(123)
        self.assertEqual(ACK.unpack(packed)[0], 123)
