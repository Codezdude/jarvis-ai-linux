"""Test helper module: dynamic tools invoked by run_dynamic_tool_isolated in tests."""

import time


def probe_add(a: int, b: int) -> int:
    return a + b


def probe_echo(text: str) -> str:
    return f"echo:{text}"


def probe_sleep(seconds: float = 5.0) -> str:
    time.sleep(seconds)
    return "slept"


def probe_boom():
    raise ValueError("intentional failure")