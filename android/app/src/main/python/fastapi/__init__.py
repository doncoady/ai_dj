import functools
import inspect
from typing import Any, Callable, get_type_hints

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect


class _HeaderSpec:
    def __init__(self, default: Any = None, alias: str | None = None):
        self.default = default
        self.alias = alias


def Header(default: Any = None, *, alias: str | None = None) -> Any:
    return _HeaderSpec(default=default, alias=alias)


def _coerce(value: str, ann: Any) -> Any:
    if ann is inspect.Parameter.empty or ann is str:
        return value
    origin = getattr(ann, "__origin__", None)
    if ann is int:
        return int(value)
    if ann is float:
        return float(value)
    if ann is bool:
        return value.lower() in ("1", "true", "yes", "on")
    if origin is not None:
        args = [a for a in getattr(ann, "__args__", ()) if a is not type(None)]
        if args:
            return _coerce(value, args[0])
    return value


def _wrap_endpoint(fn: Callable) -> Callable:
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    @functools.wraps(fn)
    async def endpoint(request: Request):
        kwargs: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            ann = hints.get(name, param.annotation)
            if ann is Request or name == "request":
                kwargs[name] = request
            elif isinstance(param.default, _HeaderSpec):
                hdr_name = param.default.alias or name.replace("_", "-")
                raw = request.headers.get(hdr_name)
                kwargs[name] = _coerce(raw, ann) if raw is not None else param.default.default
            elif name in request.path_params:
                kwargs[name] = _coerce(str(request.path_params[name]), ann)
            elif name in request.query_params:
                kwargs[name] = _coerce(request.query_params[name], ann)
            elif param.default is not inspect.Parameter.empty:
                kwargs[name] = param.default
            else:
                kwargs[name] = None
        return await fn(**kwargs)

    return endpoint


class FastAPI(Starlette):
    def __init__(self, title: str = "", lifespan=None, **kwargs):
        super().__init__(lifespan=lifespan, **kwargs)
        self.title = title

    def _add_http_route(self, path: str, methods: list[str]):
        def decorator(fn: Callable):
            self.router.routes.append(Route(path, _wrap_endpoint(fn), methods=methods))
            return fn

        return decorator

    def get(self, path: str, **kwargs):
        return self._add_http_route(path, ["GET"])

    def post(self, path: str, **kwargs):
        return self._add_http_route(path, ["POST"])

    def put(self, path: str, **kwargs):
        return self._add_http_route(path, ["PUT"])

    def delete(self, path: str, **kwargs):
        return self._add_http_route(path, ["DELETE"])

    def websocket(self, path: str, **kwargs):
        def decorator(fn: Callable):
            self.router.routes.append(WebSocketRoute(path, fn))
            return fn

        return decorator

    def middleware(self, middleware_type: str):
        def decorator(fn: Callable):
            if middleware_type == "http":
                from starlette.middleware.base import BaseHTTPMiddleware

                self.add_middleware(BaseHTTPMiddleware, dispatch=fn)
            return fn

        return decorator


__all__ = [
    "FastAPI",
    "Header",
    "Request",
    "Response",
    "WebSocket",
    "WebSocketDisconnect",
]
