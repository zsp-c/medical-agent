import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { ApiError, api, streamChat } from './api';

const TOOL_LABELS = {
  search_entity: '搜索实体',
  get_entity_details: '查实体详情',
  query_relations: '查关联关系',
  find_relation_path: '找关联路径',
  get_subgraph: '提取子图',
  search_questions: '检索题库',
  present_quiz: '出题',
};

// 互动题卡：模型 present_quiz 推送的题目。有选项即单选，无选项即问答；
// 用户作答后回发一条消息，由模型判题
function QuizCard({ quiz, disabled, onSubmit }) {
  const [selected, setSelected] = useState(-1);
  const [text, setText] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const options = quiz.options || [];
  const isOpen = quiz.kind === 'open' || !options.length;
  const canSubmit = isOpen ? text.trim().length > 0 : selected >= 0;

  function submit() {
    if (!canSubmit || submitted || disabled) return;
    setSubmitted(true);
    if (isOpen) {
      onSubmit(`我的回答：${text.trim()}`);
      return;
    }
    const letter = String.fromCharCode(65 + selected);
    onSubmit(`我选${letter}：${options[selected]}`);
  }

  return (
    <div className="quiz-card">
      <div className="quiz-head">
        <span className="quiz-tag">{isOpen ? '随堂问答' : '随堂测验'}</span>
        {quiz.subject && <span className="quiz-subject">{quiz.subject}</span>}
        {quiz.source === 'self' && <span className="quiz-subject">自拟题</span>}
      </div>
      <div className="quiz-stem">{quiz.stem}</div>
      {isOpen ? (
        <textarea
          className="quiz-answer"
          rows={3}
          placeholder="写下你的答案…"
          value={text}
          disabled={submitted}
          onChange={(e) => setText(e.target.value)}
        />
      ) : (
        <div className="quiz-options">
          {options.map((opt, i) => (
            <button
              key={i}
              className={`quiz-option${selected === i ? ' on' : ''}`}
              disabled={submitted}
              onClick={() => setSelected(i)}
            >
              <span className="quiz-letter">{String.fromCharCode(65 + i)}</span>
              <span>{opt.replace(/^[A-H][.、．]\s*/, '')}</span>
            </button>
          ))}
        </div>
      )}
      <div className="quiz-foot">
        {submitted ? (
          <span className="quiz-done">已提交，等待判题…</span>
        ) : (
          <button
            className="primary"
            disabled={!canSubmit || disabled}
            onClick={submit}
          >
            提交答案
          </button>
        )}
      </div>
    </div>
  );
}

function Message({ m, quizDisabled, onQuizSubmit }) {
  return (
    <div className={`msg ${m.role}`}>
      <div className="bubble">
        {m.files?.length > 0 && (
          <div className="msg-files">
            {m.files.map((name, i) => (
              <span key={i} className="msg-file">
                📎 {name}
              </span>
            ))}
          </div>
        )}
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
        {m.quiz && <QuizCard quiz={m.quiz} disabled={quizDisabled} onSubmit={onQuizSubmit} />}
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
  // 待发送的附件 chip：{key, name, status: uploading/ready/failed, fileId, chars, error}
  const [files, setFiles] = useState([]);
  const fileInputRef = useRef(null);
  const bottomRef = useRef(null);
  const MAX_FILES = 3;

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

  // ---------- 附件 ----------
  function patchChip(key, patch) {
    setFiles((fs) => fs.map((f) => (f.key === key ? { ...f, ...patch } : f)));
  }

  async function pickFiles(e) {
    const picked = Array.from(e.target.files || []);
    e.target.value = ''; // 允许再次选择同一文件
    if (!picked.length) return;
    const room = MAX_FILES - files.length;
    if (picked.length > room) {
      setNotice(`一条消息最多带 ${MAX_FILES} 个文件`);
    }
    const batch = picked.slice(0, room);
    const chips = batch.map((f) => ({
      key: crypto.randomUUID(),
      name: f.name,
      status: 'uploading',
      fileId: '',
      chars: 0,
      error: '',
    }));
    setFiles((fs) => [...fs, ...chips]);
    try {
      const d = await api.uploadFiles(token, batch, llm);
      // 服务端按顺序逐个处理，响应与请求一一对应
      d.files.forEach((r, i) => {
        patchChip(chips[i].key, {
          status: r.status,
          fileId: r.file_id,
          chars: r.chars,
          error: r.error,
        });
      });
    } catch (err) {
      chips.forEach((c) => patchChip(c.key, { status: 'failed', error: err.message }));
      handle401(err);
    }
  }

  async function removeFile(chip) {
    setFiles((fs) => fs.filter((f) => f.key !== chip.key));
    if (chip.fileId) {
      try {
        await api.deleteFile(token, chip.fileId);
      } catch (err) {
        if (!handle401(err)) setNotice(err.message);
      }
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

  async function send(override) {
    const query = (typeof override === 'string' ? override : input).trim();
    if (!query || busy) return;
    // 已上传成功且就绪的附件随消息带走；上传中/失败的附件留在 chip 区不进消息
    const ready = files.filter((f) => f.status === 'ready' && f.fileId);
    const fileNames = ready.map((f) => f.name);
    const attachments = ready.map((f) => f.fileId);
    if (typeof override !== 'string') setInput('');
    if (attachments.length) {
      setFiles((fs) => fs.filter((f) => !(f.status === 'ready' && f.fileId)));
    }
    setBusy(true);
    setNotice('');
    const requestId = crypto.randomUUID();
    setMessages((ms) => [
      ...ms,
      { role: 'user', content: query, files: fileNames },
      { role: 'assistant', content: '', status: [], streaming: true },
    ]);
    try {
      await streamChat({
        token,
        sessionId: active,
        query,
        requestId,
        llm,
        attachments,
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
          } else if (event === 'quiz') {
            patchLast((m) => {
              m.quiz = data;
            });
          } else if (event === 'done') {
            patchLast((m) => {
              m.content = data.answer || m.content;
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
              <p className="dim">回答基于知识图谱证据，不构成诊疗建议。</p>
            </div>
          )}
          {messages.map((m, i) => (
            <Message key={i} m={m} quizDisabled={busy} onQuizSubmit={(text) => send(text)} />
          ))}
          <div ref={bottomRef} />
        </div>
        {notice && <div className="notice">{notice}</div>}
        {files.length > 0 && (
          <div className="chips">
            {files.map((f) => (
              <span
                key={f.key}
                className={`chip ${f.status}`}
                title={f.status === 'failed' ? f.error : f.name}
              >
                <span className="chip-name">📎 {f.name}</span>
                <em className="chip-state">
                  {f.status === 'uploading' && '解析中…'}
                  {f.status === 'ready' && `${f.chars} 字`}
                  {f.status === 'failed' && (f.error || '解析失败')}
                </em>
                <button className="chip-x" title="移除" onClick={() => removeFile(f)}>
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="composer">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            accept=".txt,.md,.docx,.pptx,.png,.jpg,.jpeg,.webp,.gif"
            onChange={pickFiles}
          />
          <button
            className="icon-attach"
            title="上传文件（Word/PPT/文本/图片，每条消息最多 3 个）"
            disabled={busy || files.length >= MAX_FILES}
            onClick={() => fileInputRef.current?.click()}
          >
            📎
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onComposerKey}
            placeholder="输入医疗问题，Enter 发送，Shift+Enter 换行"
            rows={2}
          />
          <button className="primary" disabled={busy || !input.trim()} onClick={() => send()}>
            {busy ? '生成中…' : '发送'}
          </button>
        </div>
      </main>
    </div>
  );
}
