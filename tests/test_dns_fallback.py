import asyncio
import socket
import unittest

from agy_proxy.dns_fallback import (
    _is_ip_or_local,
    install_dns_fallback,
    patched_getaddrinfo,
)


class TestDnsFallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_dns_fallback()

    def test_is_ip_or_local(self):
        self.assertTrue(_is_ip_or_local("127.0.0.1"))
        self.assertTrue(_is_ip_or_local(b"127.0.0.1"))
        self.assertTrue(_is_ip_or_local("localhost"))
        self.assertTrue(_is_ip_or_local(b"localhost"))
        self.assertTrue(_is_ip_or_local("::1"))
        self.assertTrue(_is_ip_or_local("192.168.1.1"))
        self.assertTrue(_is_ip_or_local(b"192.168.1.1"))
        self.assertFalse(_is_ip_or_local("oauth2.googleapis.com"))
        self.assertFalse(_is_ip_or_local(b"oauth2.googleapis.com"))

    def test_patched_getaddrinfo_localhost(self):
        res = patched_getaddrinfo("127.0.0.1", 8000, family=socket.AF_INET)
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0][4][0], "127.0.0.1")

        # Test with bytes host
        res_bytes = patched_getaddrinfo(b"127.0.0.1", 8000, family=socket.AF_INET)
        self.assertTrue(len(res_bytes) > 0)
        self.assertEqual(res_bytes[0][4][0], "127.0.0.1")

    async def test_asyncio_loop_getaddrinfo_with_family_keyword_arg(self):
        """
        Verifies that BaseEventLoop.getaddrinfo does not raise:
        TypeError: BaseEventLoop.run_in_executor() got an unexpected keyword argument 'family'
        when called with keyword arguments like family=socket.AF_INET, and supports bytes host.
        """
        loop = asyncio.get_running_loop()
        # Should not raise TypeError: run_in_executor unexpected keyword argument 'family'
        res = await loop.getaddrinfo(
            "127.0.0.1",
            8000,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0][4][0], "127.0.0.1")

        # Support bytes host from anyio/httpcore
        res_b = await loop.getaddrinfo(
            b"127.0.0.1",
            8000,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
        self.assertTrue(len(res_b) > 0)
        self.assertEqual(res_b[0][4][0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
