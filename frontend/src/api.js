// 后端 HTTP 封装：JWT 请求头 + SSE 流式解析。
// BYOK 配置只存浏览器 localStorage，随请求体发送，服务端不持久化。

const TOKEN_KEY = 'med.jwt';
const LLM_KEY = 'med.byok';
const SID_KEY = 'med.sid';

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}
export function saveToken(t) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(SID_KEY);
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

export function getActiveSession() {
  return localStorage.getItem(SID_KEY) || '';
}
export function saveActiveSession(id) {
  if (id) localStorage.setItem(SID_KEY, id);
  else localStorage.removeItem(SID_KEY);
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

  // 免鉴权：登录页与侧栏的规模标签
  meta: () => request('/api/meta'),

  // 依赖服务实时健康（图谱 / 向量 / 题库 / 模型），设置页"服务状态"用
  status: (token) => request('/api/status', { token }),

  // Neo4j 晚于后端启动时，重建图谱连接而不必重启进程
  reconnectGraph: (token) =>
    request('/api/status/graph/reconnect', { method: 'POST', token }),

  sessions: (token, limit = 100) => request(`/api/sessions?limit=${limit}`, { token }),

  messages: (token, sid) =>
    request(`/api/sessions/${encodeURIComponent(sid)}/messages`, { token }),

  deleteSession: (token, sid) =>
    request(`/api/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE', token }),

  clearMemory: (token) => request('/api/users/me/memory', { method: 'DELETE', token }),

  listFiles: (token, sessionId) =>
    request(
      sessionId ? `/api/files?session_id=${encodeURIComponent(sessionId)}` : '/api/files',
      { token }
    ),

  deleteFile: (token, fid) =>
    request(`/api/files/${encodeURIComponent(fid)}`, { method: 'DELETE', token }),

  // multipart 上传：不能走 request()（它会强行设 JSON Content-Type，boundary 会丢）
  uploadFiles: async (token, files, llm, sessionId) => {
    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    if (llm) fd.append('llm', JSON.stringify(llm));
    if (sessionId) fd.append('session_id', sessionId);
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch('/api/files', { method: 'POST', headers, body: fd });
    if (!res.ok) throw await parseError(res);
    return res.json();
  },

  // 图谱：实体详情 / 一跳关系 / 子图
  entity: (token, key) =>
    request(`/api/entities/${encodeURIComponent(key)}`, { token }),

  relations: (token, key, { direction = 'both', limit = 50 } = {}) =>
    request(
      `/api/entities/${encodeURIComponent(key)}/relations?direction=${direction}&limit=${limit}`,
      { token }
    ),

  subgraph: (token, key, { depth = 1, limit = 50 } = {}) =>
    request(
      `/api/entities/${encodeURIComponent(key)}/subgraph?depth=${depth}&limit=${limit}`,
      { token }
    ),

  // 练习中心
  submitPractice: (token, quizId, chosen) =>
    request('/api/practice/submit', {
      method: 'POST',
      token,
      body: { quiz_id: quizId, chosen },
    }),

  practiceStats: (token, days = 30) =>
    request(`/api/practice/stats?days=${days}`, { token }),

  practiceRecords: (token, limit = 20) =>
    request(`/api/practice/records?limit=${limit}`, { token }),

  // 错题重练：把错题复制成一组新题卡（一次多题），可指定 quiz_ids 或学科
  redoPractice: (token, { quizIds, subject, limit = 10 } = {}) =>
    request('/api/practice/redo', {
      method: 'POST',
      token,
      body: { quiz_ids: quizIds || null, subject: subject || null, limit },
    }),

  // 随机组卷：直接从题库抽题，不依赖向量检索
  practiceDrill: (token, { count = 5, subject } = {}) =>
    request('/api/practice/drill', {
      method: 'POST',
      token,
      body: { count, subject: subject || null },
    }),
};

// SSE：POST /api/chat/stream，逐事件回调。
// 事件：tool_call / tool_result / quiz / delta / done / error；': ping' 心跳注释行自动忽略。
export async function streamChat({
  token,
  sessionId,
  query,
  requestId,
  llm,
  attachments,
  onEvent,
  signal,
}) {
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
      ...(attachments && attachments.length ? { attachments } : {}),
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
