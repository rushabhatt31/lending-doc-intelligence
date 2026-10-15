import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DOCINTEL_DISABLE_LLM"] = "1"  # tests are deterministic and offline

from docintel.cli import CASES  # noqa: E402
from docintel.orchestrator import LendingOrchestrator  # noqa: E402

SAMPLES = ROOT / "samples"


def run_case(orch, folder):
    _, segment, product, applicant = next(c for c in CASES if c[0] == folder)
    app = orch.create_application(segment, applicant, product)
    for f in sorted((SAMPLES / folder).iterdir()):
        orch.add_document_path(app.id, f)
    return orch.process(app.id)


@pytest.fixture
def orch():
    return LendingOrchestrator()


@pytest.fixture
def run(orch):
    return lambda folder: run_case(orch, folder)
