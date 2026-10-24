import copy

import pytest
import yaml

with open("config.yaml", encoding="utf-8") as f:
    BASE_CFG = yaml.safe_load(f)
BASE_CFG["quote"] = "EUR"


@pytest.fixture
def cfg():
    return copy.deepcopy(BASE_CFG)
