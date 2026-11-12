"""Pure URL syntax helpers; the caller must separately approve the origin.

No configuration, request headers, DNS, network or template integration is used.
"""
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

import idna


def validate_https_origin(origin: str) -> str:
    """Return a canonical HTTPS origin, without asserting deployment trust."""
    if not isinstance(origin, str) or not origin or len(origin) > 2048:
        raise ValueError('Invalid HTTPS origin')
    if any(char.isspace() or unicodedata.category(char).startswith('C') for char in origin):
        raise ValueError('Invalid HTTPS origin')
    # Percent encoding is unnecessary in an origin and can conceal delimiters,
    # credentials, path components or an IPv6 scope identifier.
    if any(char in origin for char in ('%', '\\', '?', '#', '@')):
        raise ValueError('Invalid HTTPS origin')
    try:
        parsed = urlsplit(origin)
    except ValueError:
        raise ValueError('Invalid HTTPS origin') from None
    if parsed.scheme.lower() != 'https' or not parsed.netloc or parsed.path not in ('', '/'):
        raise ValueError('Invalid HTTPS origin')
    authority = parsed.netloc
    if authority.startswith('['):
        match = re.fullmatch(r'\[([^\[\]]+)\](?::([0-9]+))?', authority)
        if not match:
            raise ValueError('Invalid HTTPS origin')
        try:
            host = f'[{ipaddress.IPv6Address(match.group(1)).compressed}]'
        except ValueError:
            raise ValueError('Invalid HTTPS origin') from None
        port = match.group(2)
    else:
        if authority.count(':') > 1:
            raise ValueError('Invalid HTTPS origin')
        hostname, separator, port = authority.partition(':')
        if not hostname or (separator and not port) or (port and not re.fullmatch(r'[0-9]+', port)):
            raise ValueError('Invalid HTTPS origin')
        if re.fullmatch(r'[0-9.]+', hostname):
            try:
                host = str(ipaddress.IPv4Address(hostname))
            except ValueError:
                raise ValueError('Invalid HTTPS origin') from None
        else:
            try:
                host = idna.encode(hostname, uts46=True, std3_rules=True).decode('ascii').lower()
            except (idna.IDNAError, UnicodeError):
                raise ValueError('Invalid HTTPS origin') from None
            # Browsers can interpret numeric/hex host forms as IPv4 addresses.
            if (host.endswith('.') or host.split('.')[-1].isdigit()
                    or re.fullmatch(r'0x[0-9a-f]+', host)):
                raise ValueError('Invalid HTTPS origin')
    if port:
        if len(port) > 5 or (len(port) > 1 and port.startswith('0')) or not 1 <= int(port) <= 65535:
            raise ValueError('Invalid HTTPS origin')
        port_part = '' if port == '443' else f':{port}'
    else:
        port_part = ''
    return f'https://{host}{port_part}'


def build_application_url(origin: str, path: str = '/login') -> str:
    """Build only the fixed application-owned login path; no redirect inputs."""
    if path != '/login':
        raise ValueError('Unsupported application path')
    return validate_https_origin(origin) + '/login'
