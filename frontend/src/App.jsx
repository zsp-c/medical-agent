import { useCallback, useEffect, useRef, useState } from 'react';
import GraphView from './components/GraphView';
import Login from './components/Login';
import PracticeRun from './components/PracticeRun';
import PracticeView from './components/PracticeView';
import SettingsView from './components/SettingsView';
import Workspace from './components/Workspace';
import { api, clearToken, getLlm, getToken, saveLlm, saveToken } from './api';

export default function App() {
  const [token, setToken] = useState(getToken());
  const [username, setUsername] = useState('');
  const [userId, setUserId] = useState('');
  const [llm, setLlm] = useState(getLlm());
  const [meta, setMeta] = useState(null);
  // workspace 常驻（切页不丢消息与流式状态），其余页面以覆盖层展示。
  // 支持深链：/?view=practice|settings|graph|files&entity=<entity_key>
  const params = new URLSearchParams(window.location.search);
  const viewParam = params.get('view');
  const initialView = ['practice', 'settings', 'graph'].includes(viewParam) ? viewParam : 'workspace';
  const [view, setView] = useState(initialView);
  const [entityKey, setEntityKey] = useState(params.get('entity') || '');
  const [pendingPrompt, setPendingPrompt] = useState('');
  // 练习页（一次多题）：source 记录这组题怎么来的，「再来一组」按同样方式再取
  const [run, setRun] = useState(null);
  const [runLoading, setRunLoading] = useState(false);
  // 每组题的序号：作为 PracticeRun 的 key，「再来一组」时强制重置已答状态
  const runSeq = useRef(0);

  const refreshMeta = useCallback(() => {
    api
      .meta()
      .then(setMeta)
      .catch(() => setMeta(null));
  }, []);

  useEffect(() => {
    refreshMeta();
  }, [refreshMeta]);

  useEffect(() => {
    if (!token) return;
    api
      .me(token)
      .then((u) => {
        setUsername(u.username);
        setUserId(u.user_id);
      })
      .catch(() => {
        clearToken();
        setToken('');
        setUsername('');
        setUserId('');
      });
  }, [token]);

  const signOut = useCallback(() => {
    clearToken();
    setToken('');
    setUsername('');
    setUserId('');
    setView('workspace');
  }, []);

  const openGraph = useCallback((key) => {
    if (!key) return;
    setEntityKey(key);
    setView('graph');
  }, []);

  const backToChat = useCallback(() => setView('workspace'), []);

  const askPractice = useCallback((subject) => {
    setView('workspace');
    setPendingPrompt(subject ? `帮我出一道${subject}的题` : '帮我出一道题');
  }, []);

  // 练习页组卷：redo = 错题重练（可按学科/单题），drill = 题库随机抽题
  const loadRun = useCallback(
    async (source = {}) => {
      const isDrill = source.kind === 'drill';
      const subject = source.subject || '';
      const single = !isDrill && (source.quizIds || []).length === 1;
      runSeq.current += 1;
      setRun({
        gen: runSeq.current,
        title: isDrill ? '随机练习' : single ? '重练这道题' : '重练错题',
        subtitle: subject || (isDrill ? '题库随机抽题' : ''),
        quizzes: [],
        error: '',
        source,
      });
      setRunLoading(true);
      setView('run');
      try {
        const data = isDrill
          ? await api.practiceDrill(token, { count: source.count || 5, subject })
          : await api.redoPractice(token, {
              quizIds: source.quizIds,
              subject,
              limit: source.limit || 10,
            });
        setRun((prev) => ({ ...(prev || {}), quizzes: data.quizzes || [] }));
      } catch (e) {
        setRun((prev) => ({ ...(prev || {}), quizzes: [], error: e.message || '组卷失败' }));
      } finally {
        setRunLoading(false);
      }
    },
    [token]
  );

  if (!token) {
    return (
      <Login
        meta={meta}
        onLogin={(t, name, uid) => {
          saveToken(t);
          setToken(t);
          setUsername(name);
          setUserId(uid);
          setView('workspace');
        }}
      />
    );
  }

  if (!username) return <div className="boot">加载中…</div>;

  return (
    <div style={{ height: '100%', position: 'relative' }}>
      <div style={{ height: '100%', visibility: view === 'workspace' ? 'visible' : 'hidden' }}>
        <Workspace
          token={token}
          username={username}
          userId={userId}
          llm={llm}
          meta={meta}
          onOpenGraph={openGraph}
          onOpenPractice={() => setView('practice')}
          onOpenSettings={() => setView('settings')}
          onLogout={signOut}
          pendingPrompt={pendingPrompt}
          onConsumePrompt={() => setPendingPrompt('')}
          initialMode={viewParam === 'files' ? 'files' : 'chat'}
        />
      </div>

      {view === 'graph' && entityKey && (
        <div style={{ position: 'absolute', inset: 0, background: '#fff', zIndex: 10 }}>
          <GraphView
            token={token}
            entityKey={entityKey}
            onBack={backToChat}
            onOpenGraph={openGraph}
            meta={meta}
          />
        </div>
      )}

      {view === 'practice' && (
        <div style={{ position: 'absolute', inset: 0, background: '#fff', zIndex: 10 }}>
          <PracticeView
            token={token}
            onBack={backToChat}
            onRedo={(payload) => loadRun({ ...payload })}
            onDrill={(count = 5) => loadRun({ kind: 'drill', count })}
            onAskPractice={(subject) => askPractice(subject)}
          />
        </div>
      )}

      {view === 'run' && run && (
        <div style={{ position: 'absolute', inset: 0, background: '#fff', zIndex: 10 }}>
          <PracticeRun
            key={run.gen}
            token={token}
            title={run.title}
            subtitle={run.subtitle}
            quizzes={run.quizzes}
            loading={runLoading}
            loadError={run.error}
            onBack={() => setView('practice')}
            onExit={backToChat}
            onAgain={() => loadRun(run.source)}
            onStartDrill={() =>
              loadRun({ kind: 'drill', count: 5, subject: run.source?.subject })
            }
          />
        </div>
      )}

      {view === 'settings' && (
        <div style={{ position: 'absolute', inset: 0, background: '#fff', zIndex: 10 }}>
          <SettingsView
            username={username}
            userId={userId}
            token={token}
            llm={llm}
            onBack={backToChat}
            onStatusChange={refreshMeta}
            onSave={(cfg) => {
              saveLlm(cfg);
              setLlm(cfg && cfg.api_key ? cfg : null);
            }}
            onClearMemory={async () => {
              if (!window.confirm('清空你的长期记忆？此操作不可恢复。')) return;
              try {
                const r = await api.clearMemory(token);
                window.alert(r.ok ? '长期记忆已清空' : '记忆引擎未就绪，未能清空');
              } catch (e) {
                window.alert(e.message || '清空失败');
              }
            }}
            onLogout={signOut}
          />
        </div>
      )}
    </div>
  );
}
