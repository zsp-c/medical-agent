// 后端 HTTP 封装：JWT 请求头 + SSE 流式解析。
// BYOK 配置只存浏览器 localStorage，随请求体发送，服务端不持久化。

const TOKEN_KEY = 'med.jwt';
const LLM_KEY = 'med.byok';

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}
export function saveToken(t) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function getLlm() {
  try {
    return JSON.parse(localStorage.getItem(LLM_KEY) || 'null');
  } catch {
    return null;
  }
}
export function saveLlm(cfg) {
  if (cfg && cfg.api_key) localStorage.setItem(LLM_KEY, JSON.stringify(cfg));
  else localStorage.removeItem(LLM_KEY);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

async function parseError(res) {
  let detail = `HTTP ${res.status}`;
  try {
    const d = await res.json();
    if (d && d.detail) detail = typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail);
  } catch {
    /* 保留默认 */
  }
  return new ApiError(res.status, detail);
}

async function request(path, { method = 'GET', body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export const api = {
  register: (username, password) =>
    request('/api/auth/register', { method: 'POST', body: { username, password } }),
  login: (username, password) =>
    request('/api/auth/login', { method: 'POST', body: { username, password } }),
  me: (token) => request('/api/auth/me', { token }),
  sessions: (token, limit = 100) => request(`/api/sessions?limit=${limit}`, { token }),
  messages: (token, sid) =>
    request(`/api/sessions/${encodeURIComponent(sid)}/messages`, { token }),
  deleteSession: (token, sid) =>
    request(`/api/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE', token }),
  clearMemory: (token) => request('/api/users/me/memory', { method: 'DELETE', token }),
};

// SSE：POST /api/chat/stream，逐事件回调。
// 事件：tool_call / tool_result / delta / done / error；': ping' 心跳注释行自动忽略。
export async function streamChat({ token, sessionId, query, requestId, llm, onEvent, signal }) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers,
    signal,
    body: JSON.stringify({
      session_id: sessionId,
      query,
      request_id: requestId,
      ...(llm ? { llm } : {}),
    }),
  });
  if (!res.ok) throw await parseError(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = 'message';
      const dataLines = [];
      for (const line of block.split('\n')) {
        if (line.startsWith(':')) continue; // 心跳注释行
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
      }
      if (!dataLines.length) continue;
      let data = null;
      try {
        data = JSON.parse(dataLines.join('\n'));
      } catch {
        continue; // 忽略坏块
      }
      onEvent({ event, data });
    }
  }
}
