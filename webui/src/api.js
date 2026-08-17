// 世界服务地址。默认空 = 同源相对路径（插件内置托管时页面与 API 同源，
// 无 CORS 问题）；独立部署时经 VITE_WORLD_API 指向世界服务绝对地址。
export const WORLD_API = (import.meta.env.VITE_WORLD_API || "").replace(/\/+$/, "");

// ---------- token（localStorage 持久化，独立域与 dashboard 会话隔离） ----------

const TOKEN_KEY = "worlditor_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token) {
  if (typeof token === "string" && token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    // 防御：非字符串（如误传对象）一律清空，避免 localStorage 存 "[object Object]"
    localStorage.removeItem(TOKEN_KEY);
  }
}

// ---------- REST（快照 / SSE / 身份；非动作，B10） ----------

async function http(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(`${WORLD_API}${path}`, { ...opts, headers });
  if (resp.status === 401 && !path.startsWith("/auth/")) {
    setToken("");
    throw new AuthError("凭据已失效，请重新登录");
  }
  if (!resp.ok) {
    let message = `请求失败（${resp.status}）`;
    try {
      const data = await resp.json();
      if (data.error) message = data.error;
    } catch {
      /* ignore */
    }
    const err = new Error(message);
    err.status = resp.status; // 供调用方区分 400（围观者无实体）等场景
    throw err;
  }
  return resp;
}

export class AuthError extends Error {}

export async function apiGet(path) {
  const resp = await http(path);
  return resp.json();
}

export async function apiPost(path, body) {
  const resp = await http(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return resp.json();
}

// 身份
export const register = (username, password) => apiPost("/auth/register", { username, password });
export const login = (username, password) => apiPost("/auth/login", { username, password });
export const registerAgent = (name) => apiPost("/auth/agent-register", { name });
export const readToken = () => apiGet("/auth/read-token");
export const logout = () => apiPost("/auth/logout");
export const changePassword = (oldPassword, newPassword) =>
  apiPost("/auth/change-password", { old_password: oldPassword, new_password: newPassword });

// 快照
export const getState = () => apiGet("/state");
export const getScene = (entityId) =>
  apiGet(entityId ? `/scene?entity_id=${encodeURIComponent(entityId)}` : "/scene");
export const getBag = () => apiGet("/bag");

// SSE 事件流（play 档；EventSource 无法带 header，用 query token）
export function eventsUrl() {
  return `${WORLD_API}/events?token=${encodeURIComponent(getToken())}`;
}

// 玩法包 web 资源
export function playWebUrl(playId, path) {
  return `${WORLD_API}/plays/${encodeURIComponent(playId)}/web/${path}`;
}
