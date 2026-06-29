---
type: proof
title: MOP as a set of four MCP tools mounted in-process
capability: In-process MCP protocol layer for agent communication filtering
kind: built
tags: [MCP, claude_agent_sdk, Python, system design]
created: 2026-05-06
confidence: 0.95
sources: [647e89a5]
---
Constructed an in-process MCP server that exposes the filtering protocol as four tools: submit_message(text), submit_justification(reason), get_rules(filter), and get_status(). The server is built using claude_agent_sdk's create_sdk_mcp_server function and runs in the same process as the agent. This design eliminates network overhead and external process management. The tools are the agent's sole path to the human—every outbound message must go through submit_message, which triggers evaluation against the active rules and returns a verdict that the agent must respect or justify.
