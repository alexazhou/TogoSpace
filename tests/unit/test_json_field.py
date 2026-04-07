from model.dbModel.base import JsonField, JsonFieldWithClass
from model.dbModel.historyUsage import HistoryUsage


def test_json_field_python_value_returns_parsed_value_for_valid_json():
    field = JsonField()
    value = field.python_value('{"a": 1, "b": [2, 3]}')
    assert value == {"a": 1, "b": [2, 3]}


def test_json_field_python_value_returns_none_and_warns_for_invalid_json(caplog):
    field = JsonField()
    with caplog.at_level("WARNING"):
        value = field.python_value("{invalid json")

    assert value is None
    assert "JsonField parse failed" in caplog.text


def test_json_field_python_value_keeps_dict_and_list():
    field = JsonField()
    assert field.python_value({"a": 1}) == {"a": 1}
    assert field.python_value([1, 2, 3]) == [1, 2, 3]


def test_json_field_with_class_round_trips_custom_class():
    field = JsonFieldWithClass(HistoryUsage)
    value = HistoryUsage(estimated_prompt_tokens=123, overflow_retry=True)

    dumped = field.db_value(value)
    loaded = field.python_value(dumped)

    assert loaded == value
