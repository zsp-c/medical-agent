import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { ApiError, api, streamChat } from './api';

const TOOL_LABELS = {
  search_entities: '搜索实体',
  get_entity_details: '查实体详情',
  get_neighbors: '查关联关系',
  find_path: '找最短路径',
  get_subgraph: '提取子图',
  translate_terms: '翻译术语',
};

function Message({ m }) {
  return (
    <div className={`msg ${m.role}`}>
      <div className="bubble">
        {m.status?.length > 0 && (
          <div className="tool-trace">
            {m.status.map((s, i) => (
              <div key={i} className="tool-line">
                {s}
              </div>
            ))}
          </div>
        )}
        {m.role === 'assistant' ? (
          <>
            <ReactMarkdown>{m.content || (m.streaming ? '' : '…')}</ReactMarkdown>
            {m.streaming && <span className="caret">▍</span>}
          </>
        ) : (
          m.content
        )}
        {m.error && <div className="msg-error">出错了：{m.error}</div>}
        {m.evidence?.length > 0 && (
          <div className="evidence">
            <div className="evidence-title">证据实体</div>
            {m.evidence.map((e) => (
              <span key={e.entity_key} className="chip">
                {e.name_zh || e.name}
                <span className="chip-type">{e.entity_type}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ChatPanel({ token, username, llm, onLogout, onOpenSettings, onAuthExpired }) {
  const [sessions, setSessions] = useState([]);
  const [active, setActive] = useState('');
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const bottomRef = useRef(null);

  const handle401 = useCallback(
    (e) => {
      if (e instanceof ApiError && e.status === 401) {
        onAuthExpired();
        return true;
      }
      return false;
    },
    [onAuthExpired]
  );

  const refreshSessions = useCallback(async () => {
    try {
      const d = await api.sessions(token);
      setSessions(d.sessions);
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
    }
  }, [token, handle401]);

  // 进入即载入会话列表，并准备一个新会话（首轮发送时后端自动登记）
  useEffect(() => {
    refreshSessions();
    setActive(crypto.randomUUID());
    setMessages([]);
  }, [refreshSessions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function openSession(sid) {
    if (busy || sid === active) return;
    setActive(sid);
    setMessages([]);
    setNotice('');
    try {
      const d = await api.messages(token, sid);
      setMessages(d.messages.map((m) => ({ role: m.role, content: m.content })));
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
    }
  }

  function newSession() {
    if (busy) return;
    setActive(crypto.randomUUID());
    setMessages([]);
    setNotice('');
  }

  async function removeSession(sid) {
    if (!window.confirm('删除该会话及其全部消息？')) return;
    try {
      await api.deleteSession(token, sid);
      if (sid === active) newSession();
      await refreshSessions();
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
    }
  }

  async function clearMemory() {
    if (!window.confirm('清空你的长期记忆？此操作不可恢复。')) return;
    try {
      const r = await api.clearMemory(token);
      setNotice(r.ok ? '长期记忆已清空' : '记忆引擎未就绪，未能清空');
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
    }
  }

  // 只改最后一条消息（流式追加 delta / 工具状态 / done 收尾共用）
  function patchLast(fn) {
    setMessages((ms) => {
      if (!ms.length) return ms;
      const next = ms.slice();
      const last = { ...next[next.length - 1] };
      fn(last);
      next[next.length - 1] = last;
      return next;
    });
  }

  async function send() {
    const query = input.trim();
    if (!query || busy) return;
    setInput('');
    setBusy(true);
    setNotice('');
    const requestId = crypto.randomUUID();
    setMessages((ms) => [
      ...ms,
      { role: 'user', content: query },
      { role: 'assistant', content: '', status: [], streaming: true },
    ]);
    try {
      await streamChat({
        token,
        sessionId: active,
        query,
        requestId,
        llm,
        onEvent: ({ event, data }) => {
          if (event === 'delta') {
            patchLast((m) => {
              m.content += data.text || '';
            });
          } else if (event === 'tool_call') {
            patchLast((m) => {
              m.status = [...(m.status || []), `调用工具：${TOOL_LABELS[data.name] || data.name}`];
            });
          } else if (event === 'tool_result') {
            patchLast((m) => {
              const s = (m.status || []).slice();
              if (s.length) s[s.length - 1] += '，已返回';
              m.status = s;
            });
          } else if (event === 'done') {
            patchLast((m) => {
              m.content = data.answer || m.content;
              m.evidence = data.evidence_entities || [];
              m.streaming = false;
            });
          } else if (event === 'error') {
            patchLast((m) => {
              m.error = data.detail || '对话中断';
              m.streaming = false;
            });
          }
        },
      });
    } catch (e) {
      patchLast((m) => {
        if (m.streaming) {
          m.error = e.message;
          m.streaming = false;
        }
      });
      handle401(e);
    } finally {
      setBusy(false);
      refreshSessions();
    }
  }

  function onComposerKey(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="side-head">
          <span>会话</span>
          <button className="icon-btn" title="新建会话" onClick={newSession}>
            ＋
          </button>
        </div>
        <ul className="session-list">
          {sessions.map((s) => (
            <li key={s.session_id} className={s.session_id === active ? 'on' : ''}>
              <button className="sess" onClick={() => openSession(s.session_id)}>
                {s.session_id.slice(0, 8)}…
              </button>
              <button className="del" title="删除会话" onClick={() => removeSession(s.session_id)}>
                ×
              </button>
            </li>
          ))}
          {!sessions.length && <li className="empty-tip">还没有历史会话</li>}
        </ul>
        <div className="side-foot">
          <div className="user-row">
            <span className="avatar">{username.slice(0, 1).toUpperCase()}</span>
            <span className="uname">{username}</span>
          </div>
          <button onClick={onOpenSettings}>模型设置{llm ? '（自定义）' : '（默认）'}</button>
          <button onClick={clearMemory}>清空长期记忆</button>
          <button onClick={onLogout}>退出登录</button>
        </div>
      </aside>

      <main className="chat">
        <div className="msgs">
          {!messages.length && (
            <div className="welcome">
              <h2>中文医疗知识图谱问答</h2>
              <p>试试：「高血压常用什么药？」「二甲双胍有什么不良反应？」</p>
              <p className="dim">回答基于知识图谱证据，不构成诊疗建议。</p>
            </div>
          )}
          {messages.map((m, i) => (
            <Message key={i} m={m} />
          ))}
          <div ref={bottomRef} />
        </div>
        {notice && <div className="notice">{notice}</div>}
        <div className="composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onComposerKey}
            placeholder="输入医疗问题，Enter 发送，Shift+Enter 换行"
            rows={2}
          />
          <button className="primary" disabled={busy || !input.trim()} onClick={send}>
            {busy ? '生成中…' : '发送'}
          </button>
        </div>
      </main>
    </div>
  );
}
