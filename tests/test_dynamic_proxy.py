from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.dynamic_proxy import DynamicProxyManager
from core.proxy_url import (
    ensure_gateway_scheme,
    explain_proxy_error,
    is_extract_api_url,
    looks_like_socks5,
    redact_proxy_url,
    to_proxy_url,
)
from providers.proxy.api_extract import ApiExtractProvider
from providers.proxy.rotating_gateway import RotatingProxyProvider


class TestToProxyUrl:
    def test_host_port_user_pass(self):
        assert (
            to_proxy_url("gw.example.com:10000:USER123-zone-custom-region-US:secret")
            == "http://USER123-zone-custom-region-US:secret@gw.example.com:10000"
        )

    def test_ipv4_host_port_user_pass(self):
        assert to_proxy_url("1.2.3.4:8080:user:pass") == "http://user:pass@1.2.3.4:8080"

    def test_userinfo_host_port(self):
        assert (
            to_proxy_url("user:pass@gw.example.com:10000")
            == "http://user:pass@gw.example.com:10000"
        )

    def test_existing_http_url(self):
        assert (
            to_proxy_url("http://user:pass@gw.example.com:10000")
            == "http://user:pass@gw.example.com:10000"
        )

    def test_host_port_only(self):
        assert to_proxy_url("1.2.3.4:8080") == "http://1.2.3.4:8080"

    def test_password_may_contain_colon(self):
        assert to_proxy_url("gw.example.com:10000:user:p:ass") == "http://user:p%3Aass@gw.example.com:10000"

    def test_empty(self):
        assert to_proxy_url("") is None
        assert to_proxy_url("   ") is None


class TestExtractApiDetection:
    def test_gen_query_is_extract_api(self):
        assert is_extract_api_url(
            "http://us.rrp.example.com:8089/gen?zone=custom&sessType=rotating"
        )

    def test_gateway_url_is_not_extract_api(self):
        assert not is_extract_api_url("http://user:pass@gw.example.com:10000")
        assert not is_extract_api_url("gw.example.com:10000:user:pass")


class TestRedact:
    def test_hides_password(self):
        assert (
            redact_proxy_url("http://USER:secret@gw.example.com:10000")
            == "http://USER:***@gw.example.com:10000"
        )


class TestSocksDetection:
    def test_looks_like_socks5(self):
        sock = MagicMock()
        sock.recv.return_value = b"\x05\x02"
        with patch("core.proxy_url.socket.create_connection", return_value=sock):
            assert looks_like_socks5("gw.example.com", 10000)
        sock.sendall.assert_called_once()

    def test_upgrades_http_gateway_to_socks5h_only_when_probed(self):
        with patch("core.proxy_url.looks_like_socks5", return_value=True):
            assert (
                ensure_gateway_scheme("http://user:pass@gw.example.com:10000")
                == "http://user:pass@gw.example.com:10000"
            )
            assert (
                ensure_gateway_scheme(
                    "http://user:pass@gw.example.com:10000", probe=True
                )
                == "socks5h://user:pass@gw.example.com:10000"
            )

    def test_leaves_http_when_not_socks(self):
        with patch("core.proxy_url.looks_like_socks5", return_value=False):
            assert (
                ensure_gateway_scheme("http://user:pass@gw.example.com:8080")
                == "http://user:pass@gw.example.com:8080"
            )

    def test_explain_ruleset(self):
        message = explain_proxy_error(
            RuntimeError("cannot complete SOCKS5 connection (0x02) Connection not allowed by ruleset")
        )
        assert "SOCKS" in message


class TestDynamicProxyPrepare:
    def test_prepare_keeps_http_gateway_and_preflights(self):
        manager = DynamicProxyManager("gw.example.com:10000:user:pass")
        with patch("core.dynamic_proxy.preflight_proxy", return_value=(True, "1.2.3.4")):
            ok, detail = manager.prepare()
        assert ok is True
        assert "1.2.3.4" in detail
        assert manager.get_proxy() == "http://user:pass@gw.example.com:10000"

    def test_colon_gateway_does_not_call_extract_api(self):
        manager = DynamicProxyManager(
            "gw.example.com:10000:USER123-zone-custom-region-US:secret"
        )
        assert manager.mode == "gateway"
        with patch("core.dynamic_proxy.requests.get") as fetch:
            proxy = manager.get_proxy()
        fetch.assert_not_called()
        assert proxy == "http://USER123-zone-custom-region-US:secret@gw.example.com:10000"
        assert manager.get_proxy() == proxy

    def test_extract_api_still_fetches(self):
        manager = DynamicProxyManager("http://api.example.com/gen?zone=us")
        assert manager.mode == "extract_api"
        mock_resp = MagicMock()
        mock_resp.text = "10.0.0.1:8080"
        mock_resp.raise_for_status = lambda: None
        with patch("core.dynamic_proxy.requests.get", return_value=mock_resp) as fetch:
            proxy = manager.get_proxy()
        fetch.assert_called_once()
        assert proxy == "http://10.0.0.1:8080"

    def test_extract_api_normalizes_colon_auth_lines(self):
        manager = DynamicProxyManager("http://api.example.com/gen?zone=us")
        mock_resp = MagicMock()
        mock_resp.text = "gw.example.com:10000:user:pass"
        mock_resp.raise_for_status = lambda: None
        with patch("core.dynamic_proxy.requests.get", return_value=mock_resp):
            assert manager.get_proxy() == "http://user:pass@gw.example.com:10000"


class TestProviderNormalization:
    def test_api_extract_normalizes_colon_auth(self):
        provider = ApiExtractProvider(api_url="http://fake")
        mock_resp = MagicMock()
        mock_resp.text = "gw.example.com:10000:user:pass"
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        with patch("core.proxy_providers.requests.get", return_value=mock_resp):
            assert provider.get_proxy() == "http://user:pass@gw.example.com:10000"

    def test_rotating_gateway_accepts_colon_auth(self):
        provider = RotatingProxyProvider(
            gateway_url="gw.example.com:10000:USER123-zone-custom-region-US:secret"
        )
        assert (
            provider.get_proxy()
            == "http://USER123-zone-custom-region-US:secret@gw.example.com:10000"
        )
