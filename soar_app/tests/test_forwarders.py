import pytest
from soar_sdk.exceptions import ActionFailure

from src.forwarders import Resolved, resolve_forwarders, resolve_with_client

OSL = "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"
BER = "B2000000-0000-0000-0000-000000000002"
ROWS = [
    {"guid": OSL, "hostname": "hf-osl-0001", "label": "Oslo 1"},
    {"guid": BER, "hostname": "hf-ber-0001", "label": "hf-ber-0001"},
    {"guid": "C3", "hostname": "dup", "label": "x"},
    {"guid": "D4", "hostname": "dup", "label": "y"},
]


def test_guid_match_case_insensitive():
    guids, res = resolve_forwarders(OSL.lower(), ROWS)
    assert guids == [OSL]
    assert res == [Resolved(OSL.lower(), OSL, "hf-osl-0001")]


def test_hostname_match_case_insensitive():
    assert resolve_forwarders("HF-OSL-0001", ROWS)[0] == [OSL]


def test_label_match():
    assert resolve_forwarders("oslo 1", ROWS)[0] == [OSL]


@pytest.mark.parametrize(
    "spec",
    ["hf-osl-0001, hf-ber-0001", "hf-osl-0001 hf-ber-0001", "hf-osl-0001,hf-ber-0001"],
)
def test_separators(spec):
    assert resolve_forwarders(spec, ROWS)[0] == [OSL, BER]


def test_star():
    assert resolve_forwarders(" * ", ROWS) == (["*"], [])


def test_star_mixed_rejected():
    with pytest.raises(ActionFailure, match=r"\*"):
        resolve_forwarders("*, hf-osl-0001", ROWS)


def test_unknown_named():
    with pytest.raises(ActionFailure, match="hf-nope") as e:
        resolve_forwarders("hf-osl-0001, hf-nope, hf-gone", ROWS)
    assert "hf-gone" in e.value.message


def test_ambiguous_named():
    with pytest.raises(ActionFailure, match="dup") as e:
        resolve_forwarders("dup", ROWS)
    assert "C3" in e.value.message
    assert "D4" in e.value.message


@pytest.mark.parametrize("spec", ["", " , ", None])
def test_empty_rejected(spec):
    with pytest.raises(ActionFailure, match=r"required.*\*"):
        resolve_forwarders(spec, ROWS)


def test_duplicates_collapsed():
    guids, res = resolve_forwarders(f"hf-osl-0001, {OSL}", ROWS)
    assert guids == [OSL]
    assert len(res) == 2


def test_fqdn_not_matched():
    with pytest.raises(ActionFailure, match=r"hf-osl-0001\.example\.com"):
        resolve_forwarders("hf-osl-0001.example.com", ROWS)


def test_resolve_with_client_reads_all_forwarders(api):
    client, rec = api({("GET", "forwarders"): (200, {"items": ROWS})})
    assert resolve_with_client(client, "hf-ber-0001")[0] == [BER]
    assert rec.requests[0].url.params.get("sourcetype") is None


def test_resolve_with_client_star_skips_lookup(api):
    client, rec = api({})
    assert resolve_with_client(client, "*") == (["*"], [])
    assert rec.requests == []
