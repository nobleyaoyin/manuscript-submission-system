"""Pure URL tests using synthetic origins only; no network or database."""
import unittest
from unittest.mock import patch

from app.email.trusted_origin import build_application_url, validate_https_origin


class TrustedOriginTest(unittest.TestCase):
    def test_canonical_dns_and_ports(self):
        cases = {
            'HTTPS://Example.COM/': 'https://example.com',
            'https://example.com:443': 'https://example.com',
            'https://example.com:8443/': 'https://example.com:8443',
            'https://LOCALHOST': 'https://localhost',
            'https://example.com:65535': 'https://example.com:65535',
        }
        for value, expected in cases.items():
            with self.subTest(value=value): self.assertEqual(validate_https_origin(value), expected)

    def test_ipv4_and_ipv6_canonicalization(self):
        cases = {
            'https://192.0.2.1:443': 'https://192.0.2.1',
            'https://[2001:0DB8:0:0::1]:8443/': 'https://[2001:db8::1]:8443',
            'https://[::1]': 'https://[::1]',
            'https://[::ffff:192.0.2.1]': 'https://[::ffff:192.0.2.1]',
            'https://[::ffff:c000:201]': 'https://[::ffff:192.0.2.1]',
        }
        for value, expected in cases.items():
            with self.subTest(value=value): self.assertEqual(validate_https_origin(value), expected)

    def test_unicode_and_idna(self):
        self.assertEqual(validate_https_origin('https://BÜCHER.example'), 'https://xn--bcher-kva.example')
        self.assertEqual(validate_https_origin('https://xn--bcher-kva.example'), 'https://xn--bcher-kva.example')
        for value in ('https://-bad.example', 'https://bad_.example', 'https://xn--.example', 'https://a..example'):
            with self.subTest(value=value), self.assertRaises(ValueError): validate_https_origin(value)

    def test_invalid_schemes_userinfo_and_components(self):
        invalid = (None, '', '//example.com', 'example.com', 'http://example.com', 'javascript:alert(1)',
                   'https:///example.com', 'https://user@example.com', 'https://user:pass@example.com',
                   'https://example.com/path', 'https://example.com//', 'https://example.com?',
                   'https://example.com#', 'https://example.com?next=/login', 'https://example.com#fragment',
                   'https://example.com\\@evil.example', 'https://example.com\\evil')
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError): validate_https_origin(value)

    def test_ports_and_ambiguous_ip_formats(self):
        invalid = ('https://example.com:', 'https://example.com:0', 'https://example.com:65536',
                   'https://example.com:-1', 'https://example.com:+443', 'https://example.com:0443',
                   'https://example.com:abc', 'https://example.com:999999999',
                   'https://127.1', 'https://127.0.0.01', 'https://2130706433',
                   'https://0x7f000001', 'https://0x7f.1', 'https://example.123',
                   'https://[::1]evil', 'https://[::1]:', 'https://[::1]:-1',
                   'https://[v1.example]', 'https://[fe80::1%25en0]', 'https://2001:db8::1')
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError): validate_https_origin(value)

    def test_encoded_delimiters_whitespace_and_controls(self):
        invalid = ('https://example.com%2f@evil.example', 'https://example.com%2fpath',
                   'https://example.com%3fnext', 'https://%65xample.com', 'https://example.com%252f',
                   ' https://example.com', 'https://example.com ', 'https://exa mple.com',
                   'https://example.com\n', 'https://exa\tmple.com', 'https://example.com\x00',
                   'https://example.com\u200b', 'https://example.com\u2028')
        for value in invalid:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError): validate_https_origin(value)

    def test_fixed_path_only_and_no_tokens(self):
        self.assertEqual(build_application_url('HTTPS://EXAMPLE.COM:443/'), 'https://example.com/login')
        self.assertEqual(build_application_url('https://[2001:db8::1]:8443', '/login'), 'https://[2001:db8::1]:8443/login')
        for path in ('', '/', '/other', '//evil.example', '/login?next=https://evil.example', '/login#token',
                     '/login%3Ftoken=x', '/login/../other', '/login\\evil', 'https://evil.example', None):
            with self.subTest(path=path), self.assertRaises(ValueError): build_application_url('https://example.com', path)

    def test_validation_makes_no_network_calls(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            self.assertEqual(build_application_url('https://bücher.example'), 'https://xn--bcher-kva.example/login')


if __name__ == '__main__':
    unittest.main()
