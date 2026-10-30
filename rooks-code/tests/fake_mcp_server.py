"""A tiny stdio MCP server for tests: one tool, `shout`, that upper-cases its text."""

import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif msg["method"] == "tools/list":
        result = {"tools": [{"name": "shout", "description": "Upper-case the text",
                             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                                             "required": ["text"]}}]}
    elif msg["method"] == "tools/call":
        result = {"content": [{"type": "text", "text": msg["params"]["arguments"]["text"].upper()}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no such method"}}),
              flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
