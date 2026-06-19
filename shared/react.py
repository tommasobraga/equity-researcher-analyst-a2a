"""Native Anthropic SDK ReAct loop — Reason → Act → Observe → repeat.

Implements the ReAct pattern directly on Claude's tool_use protocol without
intermediate frameworks. Claude's tool_use protocol is structurally a ReAct loop:
  - stop_reason="tool_use"  → ACT   (Claude wants to call a tool)
  - tool execution          → OBSERVE (we execute and return the result)
  - stop_reason="end_turn"  → final REASON + response

Usage:
    result = await react_loop(
        client=anthropic_client,
        system="...",
        user_prompt="...",
        tools=[{"name": "my_tool", "description": "...", "input_schema": {...}}],
        executors={"my_tool": async_fn},
        model="claude-sonnet-4-6",
    )
"""
import asyncio
from typing import Any, Callable

import anthropic


async def react_loop(
    client: anthropic.Anthropic,
    system: str,
    user_prompt: str,
    tools: list[dict[str, Any]],
    executors: dict[str, Callable[[dict], Any]],
    model: str,
    max_tokens: int = 4096,
    max_iterations: int = 10,
) -> str:
    """Run a ReAct loop using Claude's native tool_use protocol.

    Args:
        client:         Anthropic SDK client instance
        system:         System prompt
        user_prompt:    Initial user message
        tools:          Tool definitions in Anthropic tool_use format
        executors:      {tool_name: async callable(input_dict) -> str}
        model:          Claude model ID
        max_tokens:     Max tokens per LLM call
        max_iterations: Safety cap on Reason→Act→Observe cycles

    Returns:
        Final text response from the model after all tool calls are resolved.
        Returns a timeout message if max_iterations is reached.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]

    for _ in range(max_iterations):
        response = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        # REASON → final response
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        # ACT → Claude calls one or more tools
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            # OBSERVE → execute each tool call and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    executor = executors.get(block.name)
                    if executor:
                        try:
                            output = await executor(block.input)
                        except Exception as e:
                            output = f"Tool error: {e}"
                    else:
                        output = f"Unknown tool: {block.name}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            break

    return f"ReAct loop reached max iterations ({max_iterations}) without final answer."
