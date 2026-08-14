"""Minimal async-test runner for environments installing only core pytest.

The normal development extra provides pytest-asyncio.  This hook keeps the
suite executable in constrained/offline build images without changing test
semantics and yields to pytest-asyncio when that plugin is installed.
"""

import asyncio
import inspect


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run an async test")


def pytest_pyfunc_call(pyfuncitem):
    if pyfuncitem.config.pluginmanager.hasplugin("asyncio"):
        return None
    if "asyncio" not in pyfuncitem.keywords or not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    arguments = {
        name: pyfuncitem.funcargs[name]
        for name in inspect.signature(pyfuncitem.obj).parameters
    }
    asyncio.run(pyfuncitem.obj(**arguments))
    return True
