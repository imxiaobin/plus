from __future__ import annotations


def test_import_host_port_user_pass_and_skip_duplicates(client):
    body = {
        "text": (
            "us.rrp.example.com:10000:USER042836-zone-custom-region-US:secret\n"
            "# comment\n"
            "us.rrp.example.com:10000:USER042836-zone-custom-region-US:secret\n"
            "not-a-proxy\n"
            "http://alice:pw@gw.example.com:8080\n"
        )
    }
    imported = client.post("/api/http-proxies/import", json=body)
    assert imported.status_code == 200
    data = imported.json()
    assert data["imported"] == 2
    assert data["skipped"] == 1
    assert data["invalid"] == ["not-a-proxy"]

    listed = client.get("/api/http-proxies")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["active"] == 2
    urls = [item["url"] for item in payload["items"]]
    assert "http://USER042836-zone-custom-region-US:***@us.rrp.example.com:10000" in urls
    assert "http://alice:***@gw.example.com:8080" in urls
    assert all("secret" not in item["url"] and "pw" not in item["url"] for item in payload["items"])


def test_http_proxy_pool_toggle_and_delete(client):
    client.post(
        "/api/http-proxies/import",
        json={"text": "gw.example.com:10000:user:pass"},
    )
    items = client.get("/api/http-proxies").json()["items"]
    proxy_id = items[0]["id"]

    updated = client.put(f"/api/http-proxies/{proxy_id}", json={"is_active": False})
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False
    assert client.get("/api/http-proxies").json()["active"] == 0

    deleted = client.delete(f"/api/http-proxies/{proxy_id}")
    assert deleted.status_code == 200
    assert client.get("/api/http-proxies").json()["total"] == 0


def test_http_proxy_pool_delete_all(client):
    client.post(
        "/api/http-proxies/import",
        json={"text": "a.example.com:10000:user:pass\nb.example.com:10000:user:pass"},
    )
    assert client.get("/api/http-proxies").json()["total"] == 2

    cleared = client.delete("/api/http-proxies")
    assert cleared.status_code == 200
    assert cleared.json()["ok"] is True
    assert cleared.json()["deleted"] == 2
    listed = client.get("/api/http-proxies").json()
    assert listed["total"] == 0
    assert listed["active"] == 0


def test_register_api_accepts_http_proxy_pool(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "api.task_commands.command_service.create_register_task",
        lambda payload: captured.update(payload) or {"task_id": "http-pool-task"},
    )
    client.post(
        "/api/http-proxies/import",
        json={"text": "gw.example.com:10000:user:pass"},
    )

    response = client.post(
        "/api/tasks/register",
        json={
            "count": 1,
            "http_proxy_pool": True,
            "extra": {"mail_provider": "domain_inbucket"},
        },
    )

    assert response.status_code == 200
    assert captured["http_proxy_pool"] is True
    assert captured["proxy_pool"] is False
    assert captured["pulse"] is False


def test_register_api_rejects_empty_http_proxy_pool(client):
    response = client.post(
        "/api/tasks/register",
        json={
            "count": 1,
            "http_proxy_pool": True,
            "extra": {"mail_provider": "domain_inbucket"},
        },
    )
    assert response.status_code == 400
    assert "HTTP 代理池没有可用代理" in response.json()["detail"]


def test_401_check_api_accepts_http_proxy_pool(client, monkeypatch):
    captured = {}
    client.post(
        "/api/http-proxies/import",
        json={"text": "gw.example.com:10000:user:pass"},
    )
    monkeypatch.setattr(
        "api.account_checks.service.check_refresh_tokens_async",
        lambda platform, concurrency, **kwargs: captured.update(
            {
                "platform": platform,
                "concurrency": concurrency,
                **kwargs,
            }
        )
        or {"task_id": "task_http_pool_401"},
    )

    response = client.post(
        "/api/accounts/check-refresh-tokens",
        json={
            "platform": "chatgpt",
            "concurrency": 4,
            "http_proxy_pool": True,
            "browser": False,
        },
    )

    assert response.status_code == 200
    assert captured["http_proxy_pool"] is True
    assert captured["proxy_node"] is None
    assert captured["browser"] is False


def test_401_check_api_rejects_empty_http_proxy_pool(client):
    response = client.post(
        "/api/accounts/check-refresh-tokens",
        json={"http_proxy_pool": True, "concurrency": 1},
    )
    assert response.status_code == 400
    assert "HTTP 代理池没有可用代理" in response.json()["detail"]


def test_401_check_api_rejects_http_proxy_pool_with_mihomo_node(client):
    response = client.post(
        "/api/accounts/check-refresh-tokens",
        json={
            "http_proxy_pool": True,
            "proxy_node": "US Fast",
            "concurrency": 1,
        },
    )
    assert response.status_code == 400
    assert "不能与 Mihomo 节点同时使用" in response.json()["detail"]


def test_refresh_login_uses_http_pool_when_mihomo_listener_is_down(monkeypatch):
    import application.tasks as tasks_module

    logs = []
    monkeypatch.setenv("MIHOMO_PROXY_URL", "http://127.0.0.1:7890")
    monkeypatch.setattr(
        tasks_module,
        "_probe_chatgpt_login_route",
        lambda proxy: (
            (True, "HTTP 200")
            if proxy and "gw.example.com" in str(proxy)
            else (
                (
                    False,
                    "ConnectError: Failed to connect to 127.0.0.1 port 7890 after 0 ms: Connection refused",
                )
                if proxy
                else (True, "HTTP 403")
            )
        ),
    )
    monkeypatch.setattr(
        "core.proxy_pool.proxy_pool.get_next_static",
        lambda region="": "http://user:pass@gw.example.com:10000",
    )
    logger = type(
        "LoggerStub",
        (),
        {"log": lambda _self, message, **_kwargs: logs.append(message)},
    )()

    proxy = tasks_module._resolve_refresh_login_proxy("", logger=logger)

    assert proxy == "http://user:pass@gw.example.com:10000"
    assert any("HTTP 代理池" in message for message in logs)


def test_401_recovery_skips_mihomo_slots_for_http_pool_proxy(monkeypatch):
    import application.tasks as tasks_module

    logs = []
    check_kwargs = []

    class LoggerStub:
        def log(self, message, **_kwargs):
            logs.append(message)

        def set_progress(self, *_args, **_kwargs):
            pass

        def set_counts(self, **_kwargs):
            pass

        def set_result_data(self, *_args, **_kwargs):
            pass

        def is_cancel_requested(self):
            return False

        def finish(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(
        tasks_module, "_account_ids_for_platform", lambda *_args, **_kwargs: [1]
    )
    monkeypatch.setattr(
        tasks_module,
        "_resolve_refresh_login_proxy",
        lambda _node, *, logger, **_kwargs: "http://user:pass@gw.example.com:10000",
    )
    monkeypatch.setattr(
        "core.mihomo_client.mihomo_client.create_registration_allocator",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not create Mihomo allocator")
        ),
    )
    monkeypatch.setattr("core.proxy_pool.proxy_pool.active_count", lambda: 2)
    monkeypatch.setattr(
        "core.proxy_pool.proxy_pool.get_next_static",
        lambda region="": "http://user:pass@gw.example.com:10001",
    )

    def fake_check(account_id, **kwargs):
        check_kwargs.append(kwargs)
        return {"account_id": account_id, "state": "valid", "message": "ok"}

    monkeypatch.setattr(tasks_module, "_run_single_refresh_token_check", fake_check)

    tasks_module._execute_refresh_token_check_task(
        {
            "platform": "chatgpt",
            "concurrency": 1,
            "browser": False,
            "http_proxy_pool": True,
        },
        LoggerStub(),
    )

    assert any("HTTP 代理池" in message for message in logs)
    assert not any("Mihomo slot" in message for message in logs)
    assert check_kwargs[0]["login_proxy"] == "http://user:pass@gw.example.com:10001"
    assert check_kwargs[0]["login_proxy_rotate_callback"] is not None
