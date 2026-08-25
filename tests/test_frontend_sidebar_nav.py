from pathlib import Path


APP_TSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.tsx"
SETTINGS_PAGE_TSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "SettingsPage.tsx"
SETTINGS_TSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "Settings.tsx"
WELCOME_DIALOG_TSX = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "components"
    / "WelcomeDialog.tsx"
)


def _nav_items_block() -> str:
    source = APP_TSX.read_text(encoding="utf-8")
    start = source.index("const NAV_ITEMS: NavItem[] = [")
    end = source.index("];", start)
    return source[start:end]


def test_sidebar_top_level_nav_includes_registration_tasks():
    block = _nav_items_block()

    assert block.count("path:") == 5
    assert 'path: "/"' not in block
    assert 'path: "/accounts/chatgpt"' in block
    assert 'label: "chatgpt free"' in block
    assert 'path: "/sub2api"' in block
    assert 'labelKey: "nav.sub2Monitor"' in block
    assert 'path: "/tasks"' in block
    assert 'path: "/microsoft-mailboxes"' in block
    assert 'path: "/settings"' in block
    assert 'labelKey: "nav.settings"' in block


def test_sidebar_hides_accounts_menu_and_other_business_links():
    source = APP_TSX.read_text(encoding="utf-8")

    assert "setAccountsOpen" not in source
    assert "getPlatforms" not in source
    assert "nav.accounts" not in source
    assert "nav.ctfGptPlus" not in source
    assert "nav.gopayGptPlus" not in source
    assert "nav.plusManager" not in source
    assert "nav.tasks" not in source


def test_sidebar_includes_general_mailbox_and_proxy_pool_settings_submenu_items():
    source = APP_TSX.read_text(encoding="utf-8")

    start = source.index("const SETTINGS_NAV_ITEMS:")
    end = source.index("];", start)
    block = source[start:end]

    assert block.count('hash: "') == 6
    assert 'labelKey: "nav.settings.general", hash: "general"' in block
    assert 'labelKey: "nav.settings.mailbox", hash: "mailbox"' in block
    assert 'labelKey: "nav.settings.proxyPool", hash: "proxy-pool"' in block
    assert 'labelKey: "nav.settings.httpProxyPool", hash: "http-proxy-pool"' in block
    assert 'labelKey: "nav.settings.smsPlatform", hash: "sms-platform"' in block
    assert 'labelKey: "nav.settings.sub2api", hash: "sub2api"' in block

    assert "currentTab" in source
    assert "/settings?tab=${item.hash}" in source


def test_settings_page_includes_sms_platform_tab():
    source = SETTINGS_PAGE_TSX.read_text(encoding="utf-8")
    settings = SETTINGS_TSX.read_text(encoding="utf-8")

    assert '"sms-platform"' in source
    assert 't("settings.title.smsPlatform")' in source
    assert 't("nav.settings.smsPlatform")' in source
    assert '<Settings kind="sms" />' in source
    assert "kind?: ProviderKind" in settings
    assert "sms_providers" in settings
    assert "settings.provider.smsUsage" in settings


def test_settings_page_includes_sub2api_tab():
    source = SETTINGS_PAGE_TSX.read_text(encoding="utf-8")

    assert '"sub2api"' in source
    assert 't("settings.title.sub2api")' in source
    assert 't("nav.settings.sub2api")' in source
    assert "<Sub2ApiSettings" in source


def test_sub2api_settings_loads_groups_for_selection():
    source = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "pages"
        / "Sub2ApiSettings.tsx"
    ).read_text(encoding="utf-8")
    assert "/config/sub2api/groups" in source
    assert "settings.sub2api.loadGroups" in source
    assert "toggleValue" in source
    assert "/config/sub2api/models" in source
    assert "settings.sub2api.models" in source
    assert "settings.sub2api.modelMapping" in source
    assert "sub2api_model_mapping" in source


def test_sub2api_monitor_page_loads_local_authorized_accounts():
    source = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "pages"
        / "Sub2ApiMonitor.tsx"
    ).read_text(encoding="utf-8")
    app = APP_TSX.read_text(encoding="utf-8")
    assert "/config/sub2api/monitor" in source
    assert "sub2.monitor.title" in source
    assert "authorize/sub2api" in source
    assert "sub2.monitor.reauthorize" in source
    assert "/config/sub2api/sol-terra-mapping" in source
    assert "sub2.monitor.solTerra.add" in source
    assert "sub2.monitor.solTerra.remove" in source
    assert 'path: "/sub2api"' in app
    assert "<Sub2ApiMonitor" in app


def test_app_does_not_mount_the_welcome_dialog():
    source = APP_TSX.read_text(encoding="utf-8")

    assert "WelcomeDialog" not in source
    assert not WELCOME_DIALOG_TSX.exists()
