from __future__ import annotations

from application.tasks import create_refresh_token_check_task
from services.task_runtime import task_runtime


class AccountChecksService:
    def check_refresh_tokens_async(
        self,
        platform: str = "chatgpt",
        concurrency: int | None = None,
        proxy_node: str | None = None,
        http_proxy_pool: bool = False,
        browser: bool = True,
        account_ids: list[int] | None = None,
    ) -> dict:
        task = create_refresh_token_check_task(
            platform or "chatgpt",
            concurrency,
            proxy_node=proxy_node,
            http_proxy_pool=bool(http_proxy_pool),
            browser=bool(browser),
            account_ids=account_ids,
        )
        task_runtime.wake_up()
        return task
