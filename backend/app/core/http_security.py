from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send


ASGIApp = Callable[
    [Scope, Receive, Send],
    Awaitable[None],
]


BASE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": (
        "strict-origin-when-cross-origin"
    ),
    "X-Frame-Options": "DENY",
    "Permissions-Policy": (
        "camera=(), geolocation=(), "
        "microphone=(), payment=(), usb=()"
    ),
}

HSTS_VALUE = (
    "max-age=31536000; includeSubDomains"
)


class SecurityHeadersMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        production: bool = False,
    ) -> None:
        self.app = app
        self.production = production

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(
                scope,
                receive,
                send,
            )
            return

        async def send_with_security_headers(
            message: Message,
        ) -> None:
            if (
                message["type"]
                == "http.response.start"
            ):
                headers = MutableHeaders(
                    scope=message
                )

                for (
                    header_name,
                    header_value,
                ) in BASE_SECURITY_HEADERS.items():
                    headers[header_name] = (
                        header_value
                    )

                if self.production:
                    headers[
                        "Strict-Transport-Security"
                    ] = HSTS_VALUE

            await send(message)

        await self.app(
            scope,
            receive,
            send_with_security_headers,
        )
