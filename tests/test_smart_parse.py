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
