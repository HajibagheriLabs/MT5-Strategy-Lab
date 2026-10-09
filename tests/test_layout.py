import importlib

import pytest

MODULES = [
    "strategylab.api",
    "strategylab.backtest",
    "strategylab.compiler",
    "strategylab.config",
    "strategylab.jobs",
    "strategylab.metrics",
    "strategylab.mql5_constants",
    "strategylab.mt5_data",
    "strategylab.params",
    "strategylab.parity",
    "strategylab.report",
    "strategylab.result",
    "strategylab.server_clock",
    "strategylab.sessions",
    "strategylab.store",
    "strategylab.tester",
    "strategylab.winproc",
    "strategylab.sim.broker",
    "strategylab.sim.clock",
    "strategylab.sim.constants",
    "strategylab.sim.market",
    "strategylab.sim.runner",
    "strategylab.sim.shim",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
