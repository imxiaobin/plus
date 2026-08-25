from pathlib import Path


ACCOUNTS_TSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "Accounts.tsx"


def test_accounts_page_filters_all_accounts_with_refresh_tokens_on_the_server():
    source = ACCOUNTS_TSX.read_text(encoding="utf-8")

    assert "hasRefreshTokenOnly" in source
    assert "params.set('has_refresh_token', 'true')" in source
    assert "仅看有 RT" in source
    assert "account.has_refresh_token" in source


def test_registration_dialog_combines_proxy_pool_dynamic_ip_and_direct_modes():
    source = ACCOUNTS_TSX.read_text(encoding="utf-8")

    assert "'/proxy-nodes" in source or "`/proxy-nodes" in source
    assert "proxy_pool" in source and "proxyMode === 'pool'" in source
    assert "proxy_node: null" in source
    assert "proxy_api_url" in source and "useDynamic ? proxyApiUrl.trim() : null" in source
    assert "无（本机直连）" in source
    assert "Mihomo 代理池（自动选择节点）" in source
    assert "HTTP 代理池（host:port:user:pass）" in source
    assert "http_proxy_pool" in source
    assert "proxyMode === 'http_pool'" in source
    assert "Mihomo 代理池状态" in source
    assert "动态 IP（轮换住宅代理）" in source
    assert "代理网关或提取 API" in source
    assert "host:port:user:pass" in source
    assert "HTTP 代理池（host:port:user:pass）" in source
    assert "http_proxy_pool" in source
    assert "proxyMode === 'http_pool'" in source


def test_registration_dialog_supports_microsoft_pool_and_server_har_capability():
    source = ACCOUNTS_TSX.read_text(encoding="utf-8")

    assert "微软邮箱池（每个邮箱注册 6 次）" in source
    assert "mail_provider: mailProvider" in source
    assert "identity_provider: identityMode" in source
    assert "手机号协议注册（HeroSMS）" in source
    assert "identityMode === 'mailbox'" in source
    assert "sms_provider: 'herosms'" in source
    assert "const defaultProvider" not in source
    assert '<option value="" disabled>请选择邮箱服务</option>' in source
    assert "请选择注册邮箱服务" in source
    assert "har_capture_available" in source
    assert "harAvailable &&" in source


def test_microsoft_mailbox_settings_support_multiple_txt_files():
    source = (
        ACCOUNTS_TSX.parents[1]
        / "components"
        / "settings"
        / "ProviderCards.tsx"
    ).read_text(encoding="utf-8")

    assert "multiple" in source
    assert 'accept=".txt,text/plain"' in source
    assert "new FormData()" in source
    assert "apiForm('/microsoft-mailboxes/import'" in source
    assert "'/microsoft-mailboxes/stats'" in source


def test_mailbox_settings_do_not_offer_a_default_provider_action():
    source = (
        ACCOUNTS_TSX.parents[1]
        / "components"
        / "settings"
        / "ProviderCards.tsx"
    ).read_text(encoding="utf-8")

    assert "handleSetDefault" not in source
    assert "providers.setDefault" not in source


def test_401_check_passes_an_explicit_login_proxy_node():
    source = ACCOUNTS_TSX.read_text(encoding="utf-8")

    assert "maintenanceProxyNode" in source
    assert "http_proxy_pool: useHttpPool" in source
    assert "proxy_node: useHttpPool ? null : maintenanceProxyNode || null" in source
    assert "登录代理：自动" in source
    assert "登录代理：HTTP 代理池" in source
    assert "__http_pool__" in source
    assert "注册或登录代理节点" in source


def test_accounts_page_displays_survival_statistics():
    source = ACCOUNTS_TSX.read_text(encoding="utf-8")

    assert "/accounts/survival-stats?platform=chatgpt" in source
    assert "alive_accounts" in source
    assert "historical_registered_emails" in source
    assert "survival_rate" in source
    assert "存活账号" in source
    assert "历史注册成功邮箱" in source
    assert "存活率" in source


def test_accounts_page_has_sub2api_authorize_button_and_status():
    source = ACCOUNTS_TSX.read_text(encoding="utf-8")

    assert ">授权<" in source or "授权" in source
    assert "未授权" in source
    assert "已授权" in source
    assert "授权中" in source
    assert "authorize/sub2api" in source
    assert 'colSpan={7}' in source
    assert ">Sub2<" in source or "Sub2" in source
    assert "settings?tab=sub2api" in source


def test_accounts_page_has_delete_button():
    source = ACCOUNTS_TSX.read_text(encoding="utf-8")

    assert "method: 'DELETE'" in source
    assert "`/accounts/${account.id}`" in source
    assert "确定删除账号" in source
    assert ">删除<" in source or "删除" in source
    assert "Trash2" in source
