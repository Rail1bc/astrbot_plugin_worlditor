// 轻量 MCP client（B10：WebUI 也是 MCP 客户端——动作统一走 MCP 通道）。
// streamable HTTP：POST JSON-RPC + Bearer token + session id。

import { getToken, WORLD_API } from "./api";

let seq = 0;

export class McpClient {
  constructor() {
    this.sessionId = null;
  }

  async _post(body, expectBody = true) {
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-06-18",
    };
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    if (this.sessionId) headers["MCP-Session-Id"] = this.sessionId;
    const resp = await fetch(`${WORLD_API}/world/mcp`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    const sessionHeader = resp.headers.get("mcp-session-id");
    if (sessionHeader) this.sessionId = sessionHeader;
    if (resp.status !== 200) {
      let message = `MCP 错误（${resp.status}）`;
      try {
        const data = await resp.json();
        if (data.error) message = data.error;
      } catch {
        /* ignore */
      }
      throw new Error(message);
    }
    if (!expectBody) return null;
    const text = await resp.text();
    if (!text) return null;
    // streamable HTTP 兼容：可能返回 SSE 帧（data: {...}）或纯 JSON
    if (text.trimStart().startsWith("event:")) {
      const match = text.match(/data: (.*)/s);
      if (!match) return null;
      return JSON.parse(match[1]);
    }
    return JSON.parse(text);
  }

  async initialize() {
    const resp = await this._post({
      jsonrpc: "2.0",
      id: ++seq,
      method: "initialize",
      params: {
        protocolVersion: "2025-06-18",
        capabilities: {},
        clientInfo: { name: "worlditor-webui", version: "0.1.0" },
      },
    });
    if (resp && resp.error) throw new Error(resp.error.message || "MCP 握手失败");
    // notifications/initialized（无响应体）
    await this._post({ jsonrpc: "2.0", method: "notifications/initialized" }, false);
  }

  /** 调用世界工具；返回结构化 JSON {text, ui, effects}。 */
  async callTool(name, args = {}) {
    const resp = await this._post({
      jsonrpc: "2.0",
      id: ++seq,
      method: "tools/call",
      params: { name, arguments: args },
    });
    if (!resp) throw new Error("空响应");
    if (resp.error) throw new Error(resp.error.message || "工具调用失败");
    const content = resp.result && resp.result.content && resp.result.content[0];
    if (!content || !content.text) return {};
    try {
      return JSON.parse(content.text);
    } catch {
      return { text: content.text };
    }
  }
}
