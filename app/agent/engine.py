"""Agent 引擎：function calling 对话循环。

生成器事件（路由层负责转 SSE）：
- {"type": "delta", "content": str}           正文增量
- {"type": "tool_call", "tool", "args"}       模型发起的工具调用
- {"type": "confirm", "request_id", "tool", "summary"}  写操作等待用户确认
- {"type": "confirm_denied", "tool"}          用户拒绝
- {"type": "tool_result", "tool", "status", "result"}   工具执行结果
- {"type": "error", "error": str}             模型调用失败
"""
from __future__ import annotations

import json
import uuid

from . import tools as tool_mod
from .registry import PendingConfirm


def stream_agent(ctx, settings, messages: list[dict], max_iterations: int = 8,
                 temperature: float = 0.3, max_tokens: int = 1024):
    from ..rag.provider import ProviderError, stream_chat_events

    cfg = settings.agent_config()
    if cfg is None:
        yield {"type": "error", "error": "未配置 Agent 模型"}
        return

    schemas = tool_mod.enabled_schemas(ctx)
    history = [dict(m) for m in messages]  # system + 历史 + 当前用户问题

    for _ in range(max_iterations):
        try:
            assistant_content: list[str] = []
            calls = {}
            for kind, value in stream_chat_events(
                cfg, history, tools=schemas, temperature=temperature, max_tokens=max_tokens
            ):
                if kind == "content":
                    assistant_content.append(value)
                    yield {"type": "delta", "content": value}
                elif kind == "tool_call":
                    calls[value.index] = value
        except ProviderError as exc:
            yield {"type": "error", "error": str(exc)}
            return

        if not calls:
            return  # 正常结束，最终正文已流式输出

        # 组装 assistant 消息（含 tool_calls），供下一轮上下文
        assistant_msg = {"role": "assistant", "content": "".join(assistant_content)}
        call_ids: dict[int, str] = {}
        for idx, tc in sorted(calls.items()):
            cid = tc.id or f"call_{uuid.uuid4().hex[:8]}"
            call_ids[idx] = cid
        assistant_msg["tool_calls"] = [
            {
                "id": call_ids[idx], "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for idx, tc in sorted(calls.items())
        ]
        history.append(assistant_msg)

        for idx, tc in sorted(calls.items()):
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            name = tc.name
            yield {"type": "tool_call", "tool": name, "args": args}

            # 统一授权（fail-closed）：未知/禁用/非 allowlist → 拒绝；
            # 写工具与 MCP 工具 → 确认；只读 → 直接执行
            decision = tool_mod.authorize(ctx, name)
            if decision == "reject":
                status, result = "denied", "工具未启用或不在允许列表，已拒绝"
                yield {"type": "tool_result", "tool": name, "status": status, "result": result}
            elif decision == "confirm":
                request_id = uuid.uuid4().hex
                pend = PendingConfirm(tool=name, summary=tool_mod.tool_summary(name, args))
                ctx.registry.register(request_id, pend)
                yield {"type": "confirm", "request_id": request_id, "tool": name, "summary": pend.summary}
                decision2 = ctx.registry.wait(request_id, timeout=60)
                if decision2 != "allow":
                    status, result = "denied", "用户拒绝执行该操作"
                    yield {"type": "confirm_denied", "tool": name}
                else:
                    status, result = tool_mod.execute(ctx, name, args)
                yield {"type": "tool_result", "tool": name, "status": status, "result": result}
            else:
                status, result = tool_mod.execute(ctx, name, args)
                yield {"type": "tool_result", "tool": name, "status": status, "result": result}

            history.append({
                "role": "tool",
                "tool_call_id": call_ids[idx],
                "name": name,
                "content": result,
            })
    else:
        # for-else：循环耗尽（模型持续要求工具但已达 max_iterations）
        yield {"type": "error", "error": f"已达到最大迭代轮数（{max_iterations}），任务可能未完成"}
