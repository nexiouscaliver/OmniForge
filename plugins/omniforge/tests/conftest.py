"""Shared pytest fixtures for the OmniForge test suite.

Some test modules run coroutines with `asyncio.run()` (the modern API, which
clears the current event loop when it finishes) while others use the legacy
`asyncio.get_event_loop().run_until_complete()` pattern, which raises on
Python 3.13 once no current loop exists. This autouse fixture gives every test
a fresh, owned event loop and closes it afterward, so the suite is
order-independent without leaking loops (no `ResourceWarning: unclosed event
loop`).
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _isolate_event_loop():
    """Install a fresh event loop for the test and close it when done."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        # Tests that use asyncio.run() swap in (and close) their own loop and
        # leave the current loop cleared; either way, close the one we own and
        # drop the current reference so nothing reuses a closed loop.
        asyncio.set_event_loop(None)
        if not loop.is_closed():
            loop.close()
