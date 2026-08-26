"""模型调用接口

- `OpenAIModelClient`：真实模型客户端（OpenAI 风格 chat/completions，用 requests 直连，
  配置来自 config.py / job_gpt55.yaml）；
- `PlaceholderModelClient` / `MockErrorModelClient`：占位与测试用客户端。
"""

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """一次工具调用请求。"""
    name: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None


@dataclass
class ChatResponse:
    """模型返回的结构化响应。"""
    content: Optional[str] = None          # 模型自然语言回复
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    latency_seconds: Optional[float] = None
    response_id: Optional[str] = None


def _normalized_usage(raw_usage: Any) -> Dict[str, Any]:
    """Normalize Responses and Chat Completions token accounting."""
    raw = raw_usage if isinstance(raw_usage, dict) else {}
    input_details = raw.get("input_tokens_details") or raw.get("prompt_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or raw.get("completion_tokens_details") or {}
    if not isinstance(input_details, dict):
        input_details = {}
    if not isinstance(output_details, dict):
        output_details = {}
    input_tokens = raw.get("input_tokens", raw.get("prompt_tokens"))
    output_tokens = raw.get("output_tokens", raw.get("completion_tokens"))
    total_tokens = raw.get("total_tokens")
    if total_tokens is None and isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": input_details.get(
            "cached_tokens", raw.get("cache_read_input_tokens")
        ),
        "output_tokens": output_tokens,
        "reasoning_tokens": output_details.get("reasoning_tokens"),
        "total_tokens": total_tokens,
        "provider_raw": raw,
    }


class ModelClient(ABC):
    """模型客户端抽象基类。"""

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        """向模型发送对话请求，返回模型响应。"""
        raise NotImplementedError


def _wait_for_request_interval(client: Any) -> None:
    """Honor provider-specific minimum request intervals without affecting others."""
    interval = max(0.0, float(getattr(client.config, "model_request_interval", 0.0)))
    previous = getattr(client, "_last_request_started_at", None)
    if interval and previous is not None:
        remaining = interval - (time.monotonic() - previous)
        if remaining > 0:
            time.sleep(remaining)
    client._last_request_started_at = time.monotonic()


def _merge_chat_provider_options(payload: Dict[str, Any], config: Any) -> None:
    """Translate Harbor-style kwargs into an OpenAI-compatible JSON body."""
    options = dict(getattr(config, "model_provider_options", {}) or {})
    extra_body = options.pop("extra_body", None)
    for key in ("thinking", "output_config", "reasoning_effort"):
        if key in options:
            payload[key] = options[key]
    if isinstance(extra_body, dict):
        payload.update(extra_body)

class ResponsesModelClient(ModelClient):
    """OpenAI Responses API（/responses）客户端，适配 gpt-5.5 @ api.apevon.ai。

    与 OpenAIModelClient 接口完全一致，design_loop 无需改动。
    """

    def __init__(self, config=None):
        from config import cfg as default_cfg

        self.config = config or default_cfg
        self._last_request_started_at = None
        if not self.config.model_api_key:
            raise ValueError("MODEL_API_KEY 未配置")

    @staticmethod
    def _convert_messages(messages):
        """chat/completions 风格消息 -> Responses input items。"""
        instructions = None
        items = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                instructions = (instructions or "") + (m.get("content") or "")
            elif role in ("user", "assistant"):
                for tc in m.get("tool_calls") or []:
                    items.append({
                        "type": "function_call",
                        "call_id": tc["id"],
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    })
                if m.get("content"):
                    items.append({"role": role, "content": m["content"]})
            elif role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": m["tool_call_id"],
                    "output": m["content"],
                })
        return instructions, items

    @staticmethod
    def _convert_tools(tools):
        """chat 格式 {"type":"function","function":{...}} -> Responses 扁平格式。"""
        out = []
        for t in tools or []:
            fn = t.get("function", t)
            out.append({
                "type": "function",
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                "strict": bool(fn.get("strict", False)),
            })
        return out

    def chat(self, messages, tools=None):
        import requests

        _wait_for_request_interval(self)
        url = f"{self.config.model_base_url.rstrip('/')}/responses"
        instructions, items = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "model": self.config.model_name,
            "input": items,
            # 推理模型的思考也消耗输出额度，16k 容易截断，建议 32000 起
            "max_output_tokens": max(self.config.model_max_completion_tokens, 32000),
            "reasoning": {"effort": self.config.model_reasoning_effort},
            "parallel_tool_calls": False,
        }
        if instructions:
            payload["instructions"] = instructions
        if tools:
            payload["tools"] = self._convert_tools(tools)
            payload["tool_choice"] = "required"

        print(f"[MODEL] >>> 请求 {url} | model={self.config.model_name} | "
              f"items={len(items)} | tools={len(tools or [])}", flush=True)
        t0 = time.time()
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.config.model_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.model_timeout_seconds,
        )
        elapsed = time.time() - t0
        print(f"[MODEL] <<< HTTP {resp.status_code} | 耗时 {elapsed:.1f}s", flush=True)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise requests.HTTPError(f"{e} | 响应内容: {resp.text[:500]}", response=resp) from e

        body = resp.json()
        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for item in body.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        content_parts.append(c.get("text", ""))
            elif item.get("type") == "function_call":
                raw = item.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw)
                except json.JSONDecodeError as e:
                    raise ValueError(f"工具 {item.get('name')} 参数 JSON 解析失败: {e}") from e
                if not isinstance(arguments, dict):
                    raise ValueError(f"工具 {item.get('name')} 参数必须是 JSON object")
                tool_calls.append(
                    ToolCall(
                        name=item["name"],
                        arguments=arguments,
                        call_id=item.get("call_id") or item.get("id"),
                    )
                )

        print(f"[MODEL] <<< 回复: content={sum(len(p) for p in content_parts)} 字符 | "
              f"tool_calls={[tc.name for tc in tool_calls] or '无'}", flush=True)
        return ChatResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            usage=_normalized_usage(body.get("usage")),
            latency_seconds=elapsed,
            response_id=body.get("id"),
        )

class OpenAIModelClient(ModelClient):
    """OpenAI 风格（/chat/completions）真实模型客户端，用 requests 直连。

    配置来源：config.cfg（环境变量 / .env / job_gpt55.yaml）。
    适用于 Moonshot/Kimi、OpenAI、vLLM 等兼容该接口的服务。
    """

    def __init__(self, config=None):
        from config import cfg as default_cfg

        self.config = config or default_cfg
        self._last_request_started_at = None
        if not self.config.model_api_key:
            raise ValueError(
                "模型 API key 未配置：请通过 MODEL_API_KEY、.env 或 job_gpt55.yaml 提供"
            )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        import requests

        _wait_for_request_interval(self)
        url = f"{self.config.model_base_url.rstrip('/')}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": self.config.model_temperature,
        }
        token_parameter = getattr(
            self.config, "model_token_parameter", "max_completion_tokens"
        )
        payload[token_parameter] = self.config.model_max_completion_tokens
        _merge_chat_provider_options(payload, self.config)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "required"
            payload["parallel_tool_calls"] = False

        print(f"[MODEL] >>> 请求 {url} | model={self.config.model_name} | "
              f"messages={len(messages)} | tools={len(tools or [])}", flush=True)
        t0 = time.time()
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.config.model_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.model_timeout_seconds,
        )
        elapsed = time.time() - t0
        print(f"[MODEL] <<< HTTP {resp.status_code} | 耗时 {elapsed:.1f}s", flush=True)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            # 带上服务端返回体，便于诊断（如 401 key 无效、429 限流）
            raise requests.HTTPError(f"{e} | 响应内容: {resp.text[:500]}", response=resp) from e
        body = resp.json()
        msg = body["choices"][0]["message"]

        tool_calls: List[ToolCall] = []
        for tc in (msg.get("tool_calls") or []):
            raw_args = tc["function"].get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"工具 {tc['function']['name']} 的参数 JSON 解析失败: {e}；"
                    f"可能是 max_completion_tokens 截断所致。原始内容: {raw_args[:300]}"
                ) from e
            if not isinstance(arguments, dict):
                raise ValueError(f"工具 {tc['function']['name']} 的参数必须是 JSON object")
            tool_calls.append(
                ToolCall(
                    name=tc["function"]["name"],
                    arguments=arguments,
                    call_id=tc.get("id"),
                )
            )
        print(f"[MODEL] <<< 回复: content={len(msg.get('content') or '')} 字符 | "
              f"tool_calls={[tc.name for tc in tool_calls] or '无'}", flush=True)
        return ChatResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            usage=_normalized_usage(body.get("usage")),
            latency_seconds=elapsed,
            response_id=body.get("id"),
        )


class AnthropicModelClient(ModelClient):
    """Direct Anthropic Messages API adapter with strict tool-use conversion."""

    def __init__(self, config=None):
        from config import cfg as default_cfg

        self.config = config or default_cfg
        self._last_request_started_at = None
        self._assistant_blocks_by_tool_id: Dict[str, List[Dict[str, Any]]] = {}
        if not self.config.model_api_key:
            raise ValueError("模型 API key 未配置")

    @staticmethod
    def _convert_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        converted = []
        for tool in tools or []:
            function = tool.get("function", tool)
            converted.append(
                {
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "input_schema": function.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
        return converted

    @staticmethod
    def _convert_messages(
        messages: List[Dict[str, Any]],
        assistant_blocks_by_tool_id: Optional[
            Dict[str, List[Dict[str, Any]]]
        ] = None,
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        system_parts: List[str] = []
        converted: List[Dict[str, Any]] = []

        def append_blocks(role: str, blocks: List[Dict[str, Any]]) -> None:
            if not blocks:
                return
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"].extend(blocks)
            else:
                converted.append({"role": role, "content": blocks})

        for message in messages:
            role = message.get("role")
            if role == "system":
                if message.get("content"):
                    system_parts.append(str(message["content"]))
                continue
            if role == "tool":
                append_blocks(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": message["tool_call_id"],
                            "content": str(message.get("content") or ""),
                        }
                    ],
                )
                continue
            if role not in {"user", "assistant"}:
                continue
            blocks: List[Dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            if role == "assistant":
                tool_calls = message.get("tool_calls") or []
                preserved = None
                if assistant_blocks_by_tool_id and tool_calls:
                    preserved = assistant_blocks_by_tool_id.get(tool_calls[0].get("id"))
                if preserved is not None:
                    append_blocks(role, preserved)
                    continue
                for tool_call in tool_calls:
                    raw_arguments = tool_call["function"].get("arguments") or "{}"
                    try:
                        arguments = json.loads(raw_arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "input": arguments,
                        }
                    )
            append_blocks(role, blocks)
        return "\n".join(system_parts) or None, converted

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        import requests

        _wait_for_request_interval(self)
        url = f"{self.config.model_base_url.rstrip('/')}/messages"
        system, converted_messages = self._convert_messages(
            messages, self._assistant_blocks_by_tool_id
        )
        payload: Dict[str, Any] = {
            "model": self.config.model_name,
            "messages": converted_messages,
            "max_tokens": self.config.model_max_completion_tokens,
        }
        if system:
            payload["system"] = system
        options = dict(getattr(self.config, "model_provider_options", {}) or {})
        extra_body = options.pop("extra_body", None)
        for key in ("thinking", "output_config"):
            if key in options:
                payload[key] = options[key]
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        if "thinking" not in payload:
            payload["temperature"] = self.config.model_temperature
        if tools:
            payload["tools"] = self._convert_tools(tools)
            payload["tool_choice"] = {
                "type": "auto" if "thinking" in payload else "any"
            }

        print(
            f"[MODEL] >>> 请求 {url} | model={self.config.model_name} | "
            f"messages={len(converted_messages)} | tools={len(tools or [])}",
            flush=True,
        )
        started = time.perf_counter()
        response = requests.post(
            url,
            headers={
                "x-api-key": self.config.model_api_key,
                "Authorization": f"Bearer {self.config.model_api_key}",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.model_timeout_seconds,
        )
        elapsed = time.perf_counter() - started
        print(f"[MODEL] <<< HTTP {response.status_code} | 耗时 {elapsed:.1f}s", flush=True)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise requests.HTTPError(
                f"{exc} | 响应内容: {response.text[:500]}", response=response
            ) from exc

        body = response.json()
        raw_content = body.get("content") or []
        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for block in raw_content:
            if block.get("type") == "text":
                content_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                arguments = block.get("input") or {}
                if not isinstance(arguments, dict):
                    raise ValueError(
                        f"工具 {block.get('name')} 参数必须是 JSON object"
                    )
                tool_calls.append(
                    ToolCall(
                        name=block["name"],
                        arguments=arguments,
                        call_id=str(block["id"]),
                    )
                )
                self._assistant_blocks_by_tool_id[str(block["id"])] = raw_content
        print(
            f"[MODEL] <<< 回复: content={sum(len(part) for part in content_parts)} 字符 | "
            f"tool_calls={[call.name for call in tool_calls] or '无'}",
            flush=True,
        )
        return ChatResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            usage=_normalized_usage(body.get("usage")),
            latency_seconds=elapsed,
            response_id=body.get("id"),
        )


def create_model_client(config=None) -> ModelClient:
    """Create the protocol adapter selected by a validated model profile."""
    from config import cfg as default_cfg

    selected = config or default_cfg
    style = getattr(selected, "model_api_style", "responses")
    if style == "responses":
        return ResponsesModelClient(selected)
    if style == "chat_completions":
        return OpenAIModelClient(selected)
    if style == "anthropic_messages":
        return AnthropicModelClient(selected)
    raise ValueError(f"不支持的模型 API 类型: {style}")


class PlaceholderModelClient(ModelClient):
    """占位模型客户端：返回预先设定好的工具调用序列，仅用于演示主循环。

    真实模型请使用上方的 OpenAIModelClient。
    """

    def __init__(self, demo_sequence: Optional[List[Dict[str, Any]]] = None):
        # 演示用的默认“设计”序列
        if demo_sequence is None:
            demo_sequence = [
                {
                    "name": "create_patch_antenna",
                    "arguments": {
                        "substrate_width_mm": 100.0,
                        "substrate_length_mm": 85.0,
                        "substrate_height_mm": 5.0,
                        "patch_width_mm": 58.0,
                        "patch_length_mm": 38.4,
                        "feed_width_mm": 12.0,
                        "inset_depth_mm": 10.5,
                        "inset_gap_mm": 1.4,
                    },
                },
                {
                    "name": "finalize_design",
                    "arguments": {
                        "summary": "Validated 2.45 GHz inset-fed patch; measured S11 -21 dB and 120 MHz bandwidth."
                    },
                },
            ]
        self._demo_sequence = demo_sequence
        self._step = 0

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        """按顺序返回预设工具调用，循环结束后返回自然语言总结。"""
        if self._step < len(self._demo_sequence):
            item = self._demo_sequence[self._step]
            self._step += 1
            return ChatResponse(
                content=None,
                tool_calls=[ToolCall(name=item["name"], arguments=item["arguments"])],
                usage={
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                    "provider_raw": {},
                },
                latency_seconds=0.0,
            )

        # 序列结束后，返回终止消息（正常不应进入这里，因为 finalize_design 已结束）
        return ChatResponse(
            content="设计序列已结束。",
            tool_calls=[],
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            latency_seconds=0.0,
        )


class MockErrorModelClient(ModelClient):
    """Test client that emits one invalid spec before the valid demo workflow."""

    def __init__(self):
        self._step = 0
        self._fallback = PlaceholderModelClient()

    def chat(self, messages, tools=None):
        self._step += 1
        if self._step == 1:
            return ChatResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        name="create_patch_antenna",
                        arguments={"patch_length_mm": 38.4},
                    )
                ],
            )
        return self._fallback.chat(messages, tools)
