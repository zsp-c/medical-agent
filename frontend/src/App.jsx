import { useEffect, useState } from 'react';
import ChatPanel from './ChatPanel';
import Login from './Login';
import SettingsModal from './SettingsModal';
import { api, clearToken, getLlm, getToken, saveLlm } from './api';

export default function App() {
  const [token, setToken] = useState(getToken());
  const [username, setUsername] = useState('');
  const [llm, setLlm] = useState(getLlm());
  const [showSettings, setShowSettings] = useState(false);

  // 刷新/重开页面时用已存令牌换回用户信息；令牌失效则回登录页
  useEffect(() => {
    if (!token) return;
    api
      .me(token)
      .then((u) => setUsername(u.username))
      .catch(() => {
        clearToken();
        setToken('');
      });
  }, [token]);

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
  if (!username) return <div className="boot">加载中…</div>;

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
