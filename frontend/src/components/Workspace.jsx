import { useCallback, useEffect, useRef, useState } from 'react';
import ChatView from './ChatView';
import FilesPanel from './FilesPanel';
import Sidebar from './Sidebar';
import { ApiError, api, getActiveSession, saveActiveSession, streamChat } from '../api';
import { TOOL_STEPS } from '../utils';

const newId = () =>
  (crypto.randomUUID ? crypto.randomUUID() : `s-${Date.now()}-${Math.random().toString(16).slice(2)}`);

function newSessionId() {
  return newId();
}

export default function Workspace({
  token,
  username,
  userId,
  llm,
  meta,
  onOpenGraph,
  onOpenPractice,
  onOpenSettings,
  onLogout,
  pendingPrompt,
  onConsumePrompt,
  initialMode = 'chat',
}) {
  const [sessions, setSessions] = useState([]);
  const [sessionsReady, setSessionsReady] = useState(false);
  const [active, setActive] = useState(() => getActiveSession() || newSessionId());
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [mode, setMode] = useState(initialMode);
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const abortRef = useRef(null);
  const loadedRef = useRef('');

  const handle401 = useCallback(
    (e) => {
      if (e instanceof ApiError && e.status === 401) {
        onLogout();
        return true;
      }
      return false;
    },
    [onLogout]
  );

  const refreshSessions = useCallback(async () => {
    try {
      const d = await api.sessions(token);
      setSessions(d.sessions || []);
      return d.sessions || [];
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
      return [];
    } finally {
      setSessionsReady(true);
    }
  }, [token, handle401]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    saveActiveSession(active);
  }, [active]);

  const loadMessages = useCallback(
    async (sid) => {
      try {
        const d = await api.messages(token, sid);
        setMessages(
          (d.messages || []).map((m) => {
            const quiz = m.meta?.quiz;
            const stats = m.meta?.stats;
            const evidence = m.meta?.evidence || [];
            const hasState = quiz && (quiz.chosen || quiz.correct !== undefined);
            return {
              id: newId(),
              role: m.role,
              content: m.content,
              at: m.created_at,
              evidence,
              stats,
              quiz,
              quizLocked: Boolean(quiz && (quiz.chosen || quiz.correct !== undefined || hasState)),
              quizChosen: quiz?.chosen || '',
              quizCorrect: quiz?.correct ?? null,
              quizAnswer: quiz?.answer || '',
              files: m.meta?.files || [],
            };
          })
        );
        loadedRef.current = sid;
      } catch (e) {
        if (!handle401(e) && e.status !== 404) setNotice(e.message);
      }
    },
    [token, handle401]
  );

  // 切到已有会话时载入历史（新会话 id 还没入库，不去请求）；
  // 每个会话只载入一次，后续以本地消息为准，避免流式结束后被历史覆盖
  useEffect(() => {
    if (!active || loadedRef.current === active || !sessionsReady) return;
    const exists = sessions.some((s) => s.session_id === active);
    if (exists) {
      loadMessages(active);
    } else {
      loadedRef.current = active;
      setMessages([]);
    }
  }, [active, sessions, sessionsReady, loadMessages]);

  const loadFiles = useCallback(
    async (sid) => {
      try {
        const d = await api.listFiles(token, sid);
        setFiles(d.files || []);
      } catch (e) {
        if (!handle401(e)) setNotice(e.message);
      }
    },
    [token, handle401]
  );

  useEffect(() => {
    loadFiles(active);
  }, [active, loadFiles]);

  function patchById(id, patch) {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }

  function patchAssistant(id, fn) {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)));
  }

  async function send(override) {
    const query = (typeof override === 'string' ? override : input).trim();
    if (!query || busy) return;

    const attached = files.filter((f) => f.status === 'ready');
    const attIds = attached.map((f) => f.file_id);
    const attNames = attached.map((f) => f.name);

    if (typeof override !== 'string') setInput('');
    if (mode !== 'chat') setMode('chat');
    setNotice('');

    const userId_ = newId();
    const asstId = newId();
    setMessages((ms) => [
      ...ms,
      { id: userId_, role: 'user', content: query, files: attNames, at: new Date().toISOString() },
      { id: asstId, role: 'assistant', content: '', steps: [], streaming: true, at: new Date().toISOString() },
    ]);
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;
    let stopped = false;

    try {
      await streamChat({
        token,
        sessionId: active,
        query,
        requestId: newId(),
        llm,
        attachments: attIds,
        signal: controller.signal,
        onEvent: ({ event, data }) => {
          if (event === 'delta') {
            patchAssistant(asstId, (m) => ({ content: (m.content || '') + (data.text || '') }));
          } else if (event === 'tool_call') {
            patchAssistant(asstId, (m) => ({
              steps: [
                ...(m.steps || []),
                {
                  name: data.name,
                  label: TOOL_STEPS[data.name] || '调用工具',
                  status: 'running',
                },
              ],
            }));
          } else if (event === 'tool_result') {
            patchAssistant(asstId, (m) => {
              const steps = [...(m.steps || [])];
              for (let i = steps.length - 1; i >= 0; i -= 1) {
                if (steps[i].name === data.name && steps[i].status === 'running') {
                  steps[i] = { ...steps[i], status: 'done' };
                  break;
                }
              }
              return { steps };
            });
          } else if (event === 'quiz') {
            patchAssistant(asstId, () => ({ quiz: data, quizLocked: false }));
          } else if (event === 'done') {
            const resp = data || {};
            const timing = resp.meta?.timing || {};
            patchAssistant(asstId, () => ({
              content: resp.answer || '',
              streaming: false,
              evidence: resp.evidence_entities || [],
              stats: {
                elapsed_ms: resp.meta?.elapsed_ms,
                graph_backend: resp.meta?.graph_backend,
                memory_used: resp.meta?.memory_used || 0,
                model_calls: timing.model_call_count || 0,
                tool_calls: (timing.tool_calls || []).length,
                tokens:
                  (timing.prompt_tokens_total || 0) + (timing.completion_tokens_total || 0),
              },
            }));
          } else if (event === 'error') {
            patchAssistant(asstId, () => ({
              error: data.detail || '对话中断',
              streaming: false,
            }));
          }
        },
      });
    } catch (e) {
      if (e.name === 'AbortError') {
        stopped = true;
        patchAssistant(asstId, (m) => ({
          streaming: false,
          error: m.content ? '' : '已停止生成',
        }));
      } else if (!handle401(e)) {
        patchAssistant(asstId, () => ({ error: e.message || '请求失败', streaming: false }));
      }
    } finally {
      abortRef.current = null;
      if (stopped) patchAssistant(asstId, () => ({ streaming: false }));
      setBusy(false);
      refreshSessions();
      loadFiles(active);
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  // 练习中心「继续练题 / 重练」带过来的提问：回对话页直接发出
  useEffect(() => {
    if (!pendingPrompt) return;
    onConsumePrompt();
    send(pendingPrompt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingPrompt]);

  async function submitQuiz(msg, text, info) {
    if (!msg.quiz) return;
    let patch = { quizLocked: true, quizChosen: info?.chosen || '' };
    if (msg.quiz.quiz_id) {
      try {
        const r = await api.submitPractice(token, msg.quiz.quiz_id, info?.chosen || text);
        patch = {
          quizLocked: true,
          quizChosen: r.chosen || patch.quizChosen,
          quizCorrect: r.correct,
          quizAnswer: r.answer || '',
        };
      } catch (e) {
        if (!handle401(e)) {
          /* 判题失败不阻断讲解：题卡保持"已提交待讲解" */
        }
      }
    }
    patchById(msg.id, patch);
    send(text);
  }

  function retryQuiz(msg) {
    const subject = msg.quiz?.subject;
    send(subject ? `再帮我出一道${subject}的题` : '再帮我出一道题');
  }

  function regenerate(msg) {
    const idx = messages.findIndex((m) => m.id === msg.id);
    for (let i = idx - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'user') {
        send(messages[i].content);
        return;
      }
    }
  }

  async function newSession() {
    if (busy) return;
    loadedRef.current = '';
    setActive(newSessionId());
    setMessages([]);
    setNotice('');
    setMode('chat');
  }

  async function openSession(sid) {
    if (busy) {
      if (sid === active) setMode('chat');
      return;
    }
    if (sid === active) {
      setMode('chat');
      return;
    }
    loadedRef.current = '';
    setActive(sid);
    setMessages([]);
    setNotice('');
    setMode('chat');
  }

  async function removeSession(sid) {
    if (!window.confirm('删除该会话及其全部消息？')) return;
    try {
      await api.deleteSession(token, sid);
      if (sid === active) await newSession();
      await refreshSessions();
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
    }
  }

  async function upload(batch) {
    const room = 3 - files.length;
    if (room <= 0) {
      setNotice('本次会话最多保留 3 个附件，请先移除再上传');
      return;
    }
    const picked = batch.slice(0, room);
    if (batch.length > room) setNotice(`本次会话最多 3 个附件，已只上传前 ${room} 个`);
    else setNotice('');
    setUploading(true);
    try {
      await api.uploadFiles(token, picked, llm, active);
      await loadFiles(active);
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
    } finally {
      setUploading(false);
    }
  }

  async function removeFile(file) {
    setFiles((fs) => fs.filter((f) => f.file_id !== file.file_id));
    try {
      await api.deleteFile(token, file.file_id);
    } catch (e) {
      if (!handle401(e)) setNotice(e.message);
    }
  }

  const activeSession = sessions.find((s) => s.session_id === active);
  const lastStats = [...messages].reverse().find((m) => m.stats)?.stats || null;
  const attachments = files.filter((f) => f.status === 'ready');

  return (
    <div className="app">
      <Sidebar
        sessions={sessions}
        active={active}
        busy={busy}
        username={username}
        userId={userId}
        llm={llm}
        onNew={newSession}
        onOpen={openSession}
        onDelete={removeSession}
        onOpenPractice={onOpenPractice}
        onOpenSettings={onOpenSettings}
      />

      {mode === 'files' ? (
        <FilesPanel
          sessionTitle={activeSession?.title}
          files={files}
          uploading={uploading}
          notice={notice}
          input={input}
          setInput={setInput}
          onUpload={upload}
          onRemove={removeFile}
          onSend={(text) => send(text)}
          onBack={() => setMode('chat')}
        />
      ) : (
        <ChatView
          meta={meta}
          sessionTitle={activeSession?.title}
          messages={messages}
          busy={busy}
          model={llm?.model || meta?.model || '默认模型'}
          memoryUsed={lastStats?.memory_used || 0}
          lastMeta={lastStats}
          notice={notice}
          input={input}
          setInput={setInput}
          onSend={send}
          onStop={stop}
          onOpenFiles={() => setMode('files')}
          onOpenGraph={onOpenGraph}
          onOpenSettings={onOpenSettings}
          onQuizSubmit={submitQuiz}
          onQuizRetry={retryQuiz}
          onRegenerate={regenerate}
          attachments={attachments}
          onRemoveAttachment={removeFile}
          attachmentsBusy={uploading}
        />
      )}
    </div>
  );
}
