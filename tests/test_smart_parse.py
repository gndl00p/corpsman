# tests/test_smart_parse.py
import os
from corpsman.smart.parse import parse_smartctl_json

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "smartctl")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def test_healthy_disk_parses():
    s = parse_smartctl_json(load("sata_healthy.json"))
    assert s.available is True
    assert s.overall_passed is True
    assert s.attrs[5] == 0
    assert s.attrs[197] == 0
    assert s.power_on_hours == 8112


def test_failing_disk_attributes_parse():
    s = parse_smartctl_json(load("sata_pending_sectors.json"))
    assert s.attrs[5] == 128
    assert s.attrs[197] == 41
    assert s.attrs[198] == 8
    assert s.attrs[187] == 12


def test_crc_attribute_is_parsed_but_kept_separate():
    s = parse_smartctl_json(load("sata_crc_only.json"))
    assert s.attrs[199] == 4831


def test_garbage_input_is_unavailable_not_an_exception():
    s = parse_smartctl_json("this is not json")
    assert s.available is False
    assert s.unreadable_reason is not None


def test_empty_input_is_unavailable():
    s = parse_smartctl_json("")
    assert s.available is False


def test_json_without_attribute_table_is_unavailable():
    s = parse_smartctl_json('{"smartctl": {"version": [7, 4]}}')
    assert s.available is False


def test_table_present_but_all_rows_unparseable_is_unavailable():
    # The forbidden state: available=True with an empty attrs dict reads
    # downstream as "no bad attributes found" for a drive whose SMART
    # table was garbage.
    doc = ('{"ata_smart_attributes": {"table": ['
           '{"id": 5, "raw": {"value": "not-an-int"}},'
           '{"id": 197, "raw": {"value": "also-not"}}]}}')
    s = parse_smartctl_json(doc)
    assert s.available is False
    assert s.attrs == {}
    assert s.unreadable_reason is not None


def test_non_dict_rows_are_unavailable_not_empty_success():
    doc = '{"ata_smart_attributes": {"table": ["nonsense", 42]}}'
    s = parse_smartctl_json(doc)
    assert s.available is False


def test_rows_missing_raw_are_unavailable():
    doc = '{"ata_smart_attributes": {"table": [{"id": 5}, {"id": 197}]}}'
    s = parse_smartctl_json(doc)
    assert s.available is False


def test_partial_row_failure_still_parses_the_good_rows():
    # One bad row must NOT discard the whole table -- only a table where
    # nothing survived is a failure.
    doc = ('{"ata_smart_attributes": {"table": ['
           '{"id": 5, "raw": {"value": 3}},'
           '{"id": 197, "raw": {"value": "bad"}}]}}')
    s = parse_smartctl_json(doc)
    assert s.available is True
    assert s.attrs == {5: 3}


def test_reason_kind_distinguishes_failure_modes():
    from corpsman.smart.parse import (
        REASON_NO_OUTPUT, REASON_PARSE_ERROR, REASON_NO_ATA_TABLE,
    )
    assert parse_smartctl_json("").reason_kind == REASON_NO_OUTPUT
    assert parse_smartctl_json("not json").reason_kind == REASON_PARSE_ERROR
    assert parse_smartctl_json('{"smartctl": {}}').reason_kind == REASON_NO_ATA_TABLE


def test_boolean_id_is_not_treated_as_attribute_one():
    doc = '{"ata_smart_attributes": {"table": [{"id": true, "raw": {"value": 9}}]}}'
    s = parse_smartctl_json(doc)
    assert 1 not in s.attrs
