import importlib.util
import sys
from pathlib import Path

import pytest

from strategylab.report import parse_report

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPORTS = sorted((Path(__file__).parent / "fixtures" / "reports").glob("*.htm"))


@pytest.fixture(scope="module")
def crosscheck():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "metric_crosscheck", SCRIPTS / "metric_crosscheck.py"
        )
        module = importlib.util.module_from_spec(spec)
        # Its dataclasses look their module up by name while being created.
        sys.modules["metric_crosscheck"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.mark.parametrize("path", REPORTS, ids=[p.stem for p in REPORTS])
def test_every_difference_from_metatrader_is_explained(crosscheck, path):
    rows = crosscheck.compare(parse_report(path))
    unexplained = [(row.metric, row.printed, row.ours) for row in rows if row.status == "DIFFERS"]
    assert unexplained == []


def test_the_committed_page_is_current(crosscheck):
    results = [
        (path, report, crosscheck.compare(report))
        for path in REPORTS
        for report in [parse_report(path)]
    ]
    assert crosscheck.OUTPUT.read_text(encoding="utf-8") == crosscheck.render(results)
