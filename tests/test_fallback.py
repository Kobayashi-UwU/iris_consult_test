import pytest

from ai import client as cl
from core.schemas import EmailDraft


class FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


@pytest.fixture(autouse=True)
def clear_cooldowns():
    cl._COOLDOWN.clear()
    yield
    cl._COOLDOWN.clear()


def make_client(behaviour):
    c = cl.LLMClient("live")
    c.mode, c.models = "live", ["best", "second", "gemma-last"]
    calls = []

    def fake_call(model, system, user, schema):
        calls.append(model)
        b = behaviour.get(model)
        if b:
            raise b
        return {"subject": "s", "body": f"from {model}"}
    c._call = fake_call
    return c, calls


def test_daily_quota_moves_down_the_chain_and_remembers():
    c, calls = make_client({"best": FakeAPIError(429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier retryDelay': '70855s'")})
    out, used = c._generate("sys", "user", EmailDraft)
    assert used == "second" and out["body"] == "from second"
    c._generate("sys", "user", EmailDraft)
    assert calls == ["best", "second", "second"]          # exhausted model is skipped next time
    assert cl._cooling("best") > 60_000


def test_overload_and_bad_json_fall_through_to_gemma():
    c, calls = make_client({"best": FakeAPIError(503, "UNAVAILABLE"), "second": ValueError("bad json")})
    _, used = c._generate("sys", "user", EmailDraft)
    assert used == "gemma-last"
    assert cl._cooling("second") == 0                       # a parse error doesn't bench the model


def test_all_exhausted_raises():
    err = FakeAPIError(429, "PerDay retryDelay': '9999s'")
    c, _ = make_client({"best": err, "second": err, "gemma-last": err})
    with pytest.raises(cl.LLMUnavailable):
        c._generate("sys", "user", EmailDraft)


def test_extract_json_strips_fences():
    assert cl._extract_json(' {\n "a": 1\n}\n```') == '{\n "a": 1\n}'
