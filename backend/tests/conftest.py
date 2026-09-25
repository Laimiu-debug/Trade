from __future__ import annotations

import asyncio
import inspect
from typing import Any

import httpx


class CompatClient:
    """Minimal sync client for pytest when starlette TestClient is incompatible with httpx."""
    __test__ = False

    def __init__(
        self,
        app: Any,
        *,
        base_url: str = "http://testserver",
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | httpx.Cookies | None = None,
        follow_redirects: bool = True,
        raise_server_exceptions: bool = True,
        **_: Any,
    ) -> None:
        self.app = app
        self.base_url = base_url
        self.headers = dict(headers or {})
        self.cookies = httpx.Cookies(cookies)
        self.follow_redirects = bool(follow_redirects)
        self.raise_server_exceptions = bool(raise_server_exceptions)

    def _run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | httpx.Cookies | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)

        merged_cookies = httpx.Cookies(self.cookies)
        if cookies:
            merged_cookies.update(cookies)

        async def _send() -> httpx.Response:
            transport = httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=self.raise_server_exceptions,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url=self.base_url,
                headers=merged_headers,
                cookies=merged_cookies,
                follow_redirects=self.follow_redirects,
            ) as client:
                return await client.request(method, url, **kwargs)

        response = self._run(_send())
        self.cookies.update(response.cookies)
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def close(self) -> None:
        return None

    def __enter__(self) -> "CompatClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def pytest_configure() -> None:
    if "app" in inspect.signature(httpx.Client.__init__).parameters:
        return
    import fastapi.testclient as fastapi_testclient
    import starlette.testclient as starlette_testclient

    fastapi_testclient.TestClient = CompatClient
    starlette_testclient.TestClient = CompatClient
