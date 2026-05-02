from util.llmApiUtil import OpenAIMessage, OpenAIRequest, OpenAIUsage, OpenaiApiRole
from util.llmApiUtil import client as llm_client


def test_cache_injection_points_cover_system_and_last_message():
    assert llm_client._CACHE_INJECTION_POINTS == [
        {"location": "message", "role": "system"},
        {"location": "message", "index": -1},
    ]



def test_openai_usage_normalizes_legacy_cache_fields_into_prompt_cache_usage():
    usage = OpenAIUsage.model_validate({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {
            "cached_tokens": 75,
            "cache_creation_tokens": 30,
        },
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 75,
    })

    assert usage.prompt_cache_usage is not None
    assert usage.prompt_cache_usage.cached_tokens == 75
    assert usage.prompt_cache_usage.cache_write_tokens == 30


def test_openai_usage_keeps_none_distinct_from_zero_for_cached_tokens():
    usage = OpenAIUsage.model_validate({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 0,
    })

    assert usage.prompt_cache_usage is not None
    assert usage.prompt_cache_usage.cached_tokens == 0
    assert usage.prompt_cache_usage.cache_write_tokens == 30


def test_openai_usage_normalizes_anthropic_cache_read_tokens_into_cached_tokens():
    usage = OpenAIUsage.model_validate({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 55,
    })

    assert usage.prompt_cache_usage is not None
    assert usage.prompt_cache_usage.cached_tokens == 55
    assert usage.prompt_cache_usage.cache_write_tokens == 30
