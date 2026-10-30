// Lists .md files in NOTES_DIR over MCP stdio. Read-only.
const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const fsp = require("fs/promises");
const server = new McpServer({ name: "notes", version: "1.0.0" });
server.tool("list_notes", {}, async () => {
  const dir = process.env.NOTES_DIR || ".";
  const names = (await fsp.readdir(dir)).filter((n) => n.endsWith(".md"));
  return { content: [{ type: "text", text: names.join("\n") }] };
});
server.connect(new StdioServerTransport());
