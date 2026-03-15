from __future__ import annotations

import functools
import inspect
import logging
import time
from typing import Any, Callable, Mapping

from app.core.config import settings


TRACE_LOGGER_NAME = "medi.trace"


def configure_logging(level: str | int = "INFO") -> None:
    """Ensure a consistent log level/format for app logs in terminal."""
    normalized = _normalize_level(level)
    root = logging.getLogger()

    if not root.handlers:
        logging.basicConfig(
            level=normalized,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        root.setLevel(normalized)

    logging.getLogger("app").setLevel(normalized)
    logging.getLogger("medi").setLevel(normalized)
    logging.getLogger(TRACE_LOGGER_NAME).setLevel(normalized)


def trace_call(func: Callable | None = None, *, event_name: str | None = None):
    """Decorator that logs function enter/exit with elapsed time and exceptions."""
    if func is None:
        return lambda f: trace_call(f, event_name=event_name)

    if getattr(func, "__medi_trace_wrapped__", False):
        return func

    call_name = event_name or f"{func.__module__}.{func.__qualname__}"
    logger = logging.getLogger(TRACE_LOGGER_NAME)
    trace_enabled = bool(getattr(settings, "TRACE_FUNCTIONS", False))
    log_args = bool(getattr(settings, "TRACE_LOG_ARGS", False))

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs):
            if not trace_enabled:
                return await func(*args, **kwargs)

            started = time.perf_counter()
            if log_args:
                logger.info("-> %s %s", call_name, _format_args(args, kwargs))
            else:
                logger.info("-> %s", call_name)
            try:
                result = await func(*args, **kwargs)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                logger.exception("!! %s failed in %.1fms", call_name, elapsed_ms)
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.info("<- %s (%.1fms)", call_name, elapsed_ms)
            return result

        _async_wrapper.__medi_trace_wrapped__ = True
        return _async_wrapper

    @functools.wraps(func)
    def _sync_wrapper(*args, **kwargs):
        if not trace_enabled:
            return func(*args, **kwargs)

        started = time.perf_counter()
        if log_args:
            logger.info("-> %s %s", call_name, _format_args(args, kwargs))
        else:
            logger.info("-> %s", call_name)
        try:
            result = func(*args, **kwargs)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.exception("!! %s failed in %.1fms", call_name, elapsed_ms)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.info("<- %s (%.1fms)", call_name, elapsed_ms)
        return result

    _sync_wrapper.__medi_trace_wrapped__ = True
    return _sync_wrapper


def instrument_module_functions(
    namespace: Mapping[str, Any],
    *,
    include_private: bool = False,
    include_classes: bool = True,
) -> None:
    """
    Wrap functions and methods in a module namespace with trace logging.
    """
    if not bool(getattr(settings, "TRACE_FUNCTIONS", False)):
        return

    module_name = namespace.get("__name__", "")
    mutable_namespace = dict(namespace)

    for name, value in mutable_namespace.items():
        if _should_skip_name(name, include_private):
            continue

        if inspect.isfunction(value) and value.__module__ == module_name:
            namespace[name] = trace_call(value, event_name=f"{module_name}.{name}")  # type: ignore[index]
            continue

        if include_classes and inspect.isclass(value) and value.__module__ == module_name:
            _instrument_class_methods(value, include_private=include_private)


def _instrument_class_methods(cls: type, *, include_private: bool) -> None:
    for name, value in list(vars(cls).items()):
        if _should_skip_name(name, include_private):
            continue
        full_name = f"{cls.__module__}.{cls.__qualname__}.{name}"

        if isinstance(value, staticmethod):
            wrapped = trace_call(value.__func__, event_name=full_name)
            setattr(cls, name, staticmethod(wrapped))
            continue

        if isinstance(value, classmethod):
            wrapped = trace_call(value.__func__, event_name=full_name)
            setattr(cls, name, classmethod(wrapped))
            continue

        if inspect.isfunction(value):
            wrapped = trace_call(value, event_name=full_name)
            setattr(cls, name, wrapped)


def _should_skip_name(name: str, include_private: bool) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return True
    if not include_private and name.startswith("_"):
        return True
    return False


def _normalize_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return int(getattr(logging, str(level).upper(), logging.INFO))


def _format_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    max_items = 4
    parts: list[str] = []

    for idx, value in enumerate(args[:max_items]):
        parts.append(f"arg{idx}={_safe_value(value)}")
    if len(args) > max_items:
        parts.append(f"...+{len(args) - max_items} args")

    shown = 0
    for key, value in kwargs.items():
        if shown >= max_items:
            break
        parts.append(f"{key}={_safe_value(value)}")
        shown += 1
    if len(kwargs) > shown:
        parts.append(f"...+{len(kwargs) - shown} kwargs")

    return " ".join(parts)


def _safe_value(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes len={len(value)}>"
    if isinstance(value, str):
        if len(value) > 120:
            return repr(value[:117] + "...")
        return repr(value)
    if value is None:
        return "None"
    value_type = type(value).__name__
    if value_type in {"Session", "AsyncSession"}:
        return f"<{value_type}>"
    text = repr(value)
    if len(text) > 120:
        text = text[:117] + "..."
    return text

