"""Pure, offline capacity checks for text-only AI requests.

DeepSeek's official model/pricing documentation, checked 2026-10-09, lists
Flash and V4 Pro with a 1M context and 384K maximum output:
https://api-docs.deepseek.com/quick_start/pricing/
https://api-docs.deepseek.com/api/list-models/
The numerical model metadata uses 1,048,576 and 393,216 tokens respectively.
Legacy Flash aliases explicitly listed on that page share Flash's limits.

This is not a tokenizer or a billed-token estimate. For an ordinary byte-based
text tokenizer, each token consumes at least one UTF-8 byte, so text bytes give
a conservative upper bound. Protocol framing is separately reserved; the
reserve is an estimate rather than a provider-certified framing count. Other
tokenizers/providers need their own metadata/tokenizer before treating this
method as a strict bound. No network discovery, secret reads or import-time I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


DEEPSEEK_CONTEXT_WINDOW = 1048576
DEEPSEEK_MAX_OUTPUT = 393216
UNKNOWN_CONTEXT_WINDOW = 131072
UNKNOWN_MAX_OUTPUT = 32768
PROTOCOL_RESERVE_TOKENS = 256
PER_MESSAGE_RESERVE_TOKENS = 8
ESTIMATION_METHOD = "utf8_bytes_upper_bound_with_protocol_reserve"
OFFICIAL_CAPACITY_SOURCE = "https://api-docs.deepseek.com/api/list-models/"
_OFFICIAL_MODELS = frozenset({
    "deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
    "deepseek-v4-pro",
})


@dataclass(frozen=True)
class ModelCapacity:
    model: str
    context_window: int
    max_output_tokens: int
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "context_window": self.context_window,
                "max_output_tokens": self.max_output_tokens, "capacity_source": self.source}


@dataclass(frozen=True)
class InputAssessment:
    input_tokens_upper_bound: int
    input_capacity_tokens: int
    output_reserved_tokens: int
    message_characters: int
    utf8_bytes: int
    overhead_tokens: int
    fits: bool
    capacity: ModelCapacity

    def as_dict(self) -> dict[str, Any]:
        return {**self.capacity.as_dict(),
                "input_tokens_upper_bound": self.input_tokens_upper_bound,
                "input_capacity_tokens": self.input_capacity_tokens,
                "output_reserved_tokens": self.output_reserved_tokens,
                "message_characters": self.message_characters, "utf8_bytes": self.utf8_bytes,
                "overhead_tokens": self.overhead_tokens, "fits": self.fits,
                "estimation_method": ESTIMATION_METHOD, "exact_token_count": False,
                "estimator_assumption": "ordinary_byte_based_text_tokenizer"}


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"AI {label} 必须是正整数。")
    if isinstance(value, str) and (not value.strip() or not value.strip().isascii()
                                   or not value.strip().isdigit()):
        raise ValueError(f"AI {label} 必须是正整数。")
    number = int(value)
    if number < 1:
        raise ValueError(f"AI {label} 必须是正整数。")
    return number


def configured_model_capacity(*, model: str | None = None, base_url: str | None = None,
                              metadata: Mapping[str, Any] | None = None,
                              environ: Mapping[str, str] | None = None) -> ModelCapacity:
    """Resolve explicit metadata/env first, then official known models.

Metadata accepts the public /models row's ``context_window`` and
``max_output_tokens`` fields. An optional ``id`` must match the selected model.
Environment overrides are DEEPSEEK_CONTEXT_WINDOW_TOKENS and
DEEPSEEK_MAX_OUTPUT_TOKENS. Unknown models/hosts get an explicitly unverified
128K fallback, never DeepSeek's 1M just because their model name says Flash.
Only the named non-secret configuration fields are consulted.
"""
    env = os.environ if environ is None else environ
    selected = str(model if model is not None else env.get("DEEPSEEK_MODEL", "deepseek-flash")).strip()
    base = str(base_url if base_url is not None else env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parsed = urlsplit(base)
    known = (parsed.scheme.lower() == "https" and parsed.hostname == "api.deepseek.com"
             and parsed.port in {None, 443} and selected.lower() in _OFFICIAL_MODELS)
    context = DEEPSEEK_CONTEXT_WINDOW if known else UNKNOWN_CONTEXT_WINDOW
    output = DEEPSEEK_MAX_OUTPUT if known else UNKNOWN_MAX_OUTPUT
    source = OFFICIAL_CAPACITY_SOURCE if known else "unverified_provider_fallback"
    details = {} if metadata is None else dict(metadata)
    if details.get("id") is not None and details["id"] != selected:
        raise ValueError("AI 容量元数据与所选模型不一致。")
    explicit_context = details.get("context_window", env.get("DEEPSEEK_CONTEXT_WINDOW_TOKENS"))
    explicit_output = details.get("max_output_tokens", env.get("DEEPSEEK_MAX_OUTPUT_TOKENS"))
    if explicit_context is not None:
        context = _positive_int(explicit_context, "上下文容量")
    if explicit_output is not None:
        output = _positive_int(explicit_output, "最大输出容量")
    elif explicit_context is not None:
        output = min(output, context)
    if explicit_context is not None or explicit_output is not None:
        from_metadata = any(details.get(key) is not None for key in ("context_window", "max_output_tokens"))
        source = "explicit_model_metadata" if from_metadata else "explicit_environment_configuration"
    if output > context:
        raise ValueError("AI 最大输出容量不能超过上下文容量。")
    return ModelCapacity(selected, context, output, source)


def compact_json(value: Any) -> str:
    """Remove JSON formatting whitespace without truncating or changing data."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def assess_messages(messages: Sequence[Mapping[str, Any]], *, max_output_tokens: int,
                    capacity: ModelCapacity | None = None) -> InputAssessment:
    """Count actual message strings, not an escaped HTTP JSON envelope.

The requested maximum output (including the provider's thinking allowance)
is reserved in the shared context. Non-text content is rejected so images or
tools cannot silently bypass this text-only estimator.
    """
    limits = capacity if capacity is not None else configured_model_capacity()
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise ValueError("AI 本次输出预算必须是正整数。")
    output = _positive_int(max_output_tokens, "本次输出预算")
    if output > limits.max_output_tokens:
        raise ValueError("AI 本次输出预算超过所选模型最大输出容量。")
    characters = byte_count = 0
    for message in messages:
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise ValueError("AI 容量估算仅支持文本消息。")
        if any(key not in {"role", "content", "name"} for key in message):
            raise ValueError("AI 文本容量估算不支持工具或其他消息字段。")
        for key in ("role", "content", "name"):
            if key not in message:
                continue
            value = message[key]
            if not isinstance(value, str):
                raise ValueError("AI 容量估算仅支持文本消息字段。")
            characters += len(value)
            byte_count += len(value.encode("utf-8"))
    overhead = PROTOCOL_RESERVE_TOKENS + PER_MESSAGE_RESERVE_TOKENS * len(messages)
    upper_bound = byte_count + overhead
    input_capacity = max(0, limits.context_window - output)
    return InputAssessment(upper_bound, input_capacity, output, characters,
                           byte_count, overhead, upper_bound <= input_capacity, limits)
