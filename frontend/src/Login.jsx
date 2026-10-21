import { useState } from 'react';
import { api, saveToken } from './api';

export default function Login({ onLogin }) {
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const r =
        mode === 'login'
          ? await api.login(username.trim(), password)
          : await api.register(username.trim(), password);
      saveToken(r.access_token);
      onLogin(r.access_token, r.username);
    } catch (err) {
      setError(err.message || '请求失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>医疗知识图谱问答</h1>
        <p className="login-sub">基于 PrimeKG-Plus 的中文医疗 Agent</p>
        <label>
          用户名
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="3~32 位字母、数字、_、-"
            autoComplete="username"
            required
          />
        </label>
        <label>
          密码
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === 'register' ? '至少 8 位' : ''}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
          />
        </label>
        {error && <div className="login-error">{error}</div>}
        <button className="primary" disabled={busy} type="submit">
          {busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录'}
        </button>
        <button
          className="link"
          type="button"
          onClick={() => {
            setMode(mode === 'login' ? 'register' : 'login');
            setError('');
          }}
        >
          {mode === 'login' ? '没有账号？注册一个' : '已有账号？去登录'}
        </button>
      </form>
    </div>
  );
}
