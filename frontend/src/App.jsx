import { useCallback, useEffect, useState } from 'react';
import ChatPanel from './ChatPanel';
import Login from './Login';
import SettingsModal from './SettingsModal';
import { api, clearToken, getLlm, getToken, saveLlm } from './api';

export default function App() {
  const [token, setToken] = useState(getToken());
  const [username, setUsername] = useState('');
  const [llm, setLlm] = useState(getLlm());
  const [showSettings, setShowSettings] = useState(false);
  const [bootError, setBootError] = useState('');

  // 刷新/重开页面时用已存令牌换回用户信息。
  // 只有 401 才说明令牌真的失效；断网、后端重启等错误保留令牌，让用户重试
  const verifyToken = useCallback(() => {
    if (!token) return;
    setBootError('');
    api
      .me(token)
      .then((u) => setUsername(u.username))
      .catch((e) => {
        if (e?.status === 401) {
          clearToken();
          setToken('');
        } else {
          setBootError(e.message || '无法连接服务器');
        }
      });
  }, [token]);

  useEffect(() => {
    verifyToken();
  }, [verifyToken]);

  function signOut() {
    clearToken();
    setToken('');
    setUsername('');
  }

  if (!token) {
    return (
      <Login
        onLogin={(t, name) => {
          setToken(t);
          setUsername(name);
        }}
      />
    );
  }
  if (!username) {
    if (bootError) {
      return (
        <div className="boot">
          <div className="boot-panel">
            <p>{bootError}</p>
            <button className="primary" onClick={verifyToken}>
              重试
            </button>
          </div>
        </div>
      );
    }
    return <div className="boot">加载中…</div>;
  }

  return (
    <>
      <ChatPanel
        token={token}
        username={username}
        llm={llm}
        onOpenSettings={() => setShowSettings(true)}
        onLogout={signOut}
        onAuthExpired={signOut}
      />
      {showSettings && (
        <SettingsModal
          initial={llm}
          onClose={() => setShowSettings(false)}
          onSave={(cfg) => {
            saveLlm(cfg);
            setLlm(cfg && cfg.api_key ? cfg : null);
            setShowSettings(false);
          }}
        />
      )}
    </>
  );
}
