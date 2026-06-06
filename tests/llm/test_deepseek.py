from strix.config.config import QWEN_API_BASE, QWEN_DEFAULT_MODEL, resolve_llm_config
from strix.llm.config import LLMConfig
from strix.llm.llm import LLM
from strix.llm.utils import deepseek_completion_kwargs, qwen_completion_kwargs, resolve_strix_model


def test_qwen_is_default_model(monkeypatch) -> None:
    monkeypatch.delenv("STRIX_LLM", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-test")

    model, api_key, api_base = resolve_llm_config()

    assert model == QWEN_DEFAULT_MODEL
    assert api_key == "qwen-test"
    assert api_base == QWEN_API_BASE


def test_qwen_env_overrides_base(monkeypatch) -> None:
    monkeypatch.setenv("STRIX_LLM", "qwen3.7-max")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-test")
    monkeypatch.setenv("DASHSCOPE_API_BASE", "https://example.test/v1")

    model, api_key, api_base = resolve_llm_config()

    assert model == "qwen3.7-max"
    assert api_key == "qwen-test"
    assert api_base == "https://example.test/v1"


def test_qwen_model_resolves_to_openai_compatible_litellm_model() -> None:
    api_model, canonical = resolve_strix_model("qwen3.7-max")

    assert api_model == "openai/qwen3.7-max"
    assert canonical == "qwen3.7-max"


def test_qwen_request_args_enable_thinking(monkeypatch) -> None:
    monkeypatch.setenv("STRIX_LLM", "qwen3.7-max")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-test")

    llm = LLM(LLMConfig(), agent_name=None)
    args = llm._build_completion_args([{"role": "user", "content": "hello"}])

    assert args["model"] == "openai/qwen3.7-max"
    assert args["api_key"] == "qwen-test"
    assert args["api_base"] == QWEN_API_BASE
    assert "reasoning_effort" not in args
    assert args["extra_body"] == {"enable_thinking": True}


def test_qwen_completion_kwargs_enables_thinking() -> None:
    assert qwen_completion_kwargs() == {
        "extra_body": {"enable_thinking": True},
    }


def test_deepseek_model_still_resolves_to_openai_compatible_litellm_model() -> None:
    api_model, canonical = resolve_strix_model("deepseek-v4-pro")

    assert api_model == "openai/deepseek-v4-pro"
    assert canonical == "deepseek-v4-pro"


def test_deepseek_completion_kwargs_uses_explicit_reasoning_effort() -> None:
    assert deepseek_completion_kwargs("low") == {
        "reasoning_effort": "low",
        "extra_body": {"thinking": {"type": "enabled"}},
    }
