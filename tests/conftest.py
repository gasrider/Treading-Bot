import copy

import pytest
import yaml

with open("config.example.yaml", encoding="utf-8") as f:
    BASE_CFG = yaml.safe_load(f)
BASE_CFG["quote"] = "EUR"
BASE_CFG["secrets"] = {}


@pytest.fixture
def cfg():
    return copy.deepcopy(BASE_CFG)
