import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "test_cases.json")

with open(DATA_PATH, encoding="utf-8") as f:
    _RAW = json.load(f)

TEST_CASES = {tc["id"]: tc for tc in _RAW}


def all_ids_in_order():
    return [tc["id"] for tc in _RAW]


def by_automation(tier):
    return [tc["id"] for tc in _RAW if tc["automation"] == tier]


AUTOMATION_COUNTS = {
    "automated": len(by_automation("automated")),
    "partial": len(by_automation("partial")),
    "manual": len(by_automation("manual")),
    "total": len(_RAW),
}
