"""
Antigravity Proxy - A versatile proxy for Google Antigravity / Gemini Code Assist.
"""

__version__ = "1.4.0"

try:
    from agy_proxy.dns_fallback import install_dns_fallback
    install_dns_fallback()
except Exception:
    pass
