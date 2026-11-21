import { useState } from 'react';
import { api, saveToken } from './api';

export default function Login({ onLogin }) {
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // 与后端 AuthBody 的校验规则保持一致；原生 pattern/minLength 之外再拦一层，
  // 避免不同浏览器（或自动化填值）绕过原生校验后直接打出 422
  function validate(u, p) {
    if (!/^[A-Za-z0-9_-]{3,32}$/.test(u)) return '用户名需为 3~32 位字母、数字、下划线或短横线';
    if (p.length < 8) return '密码至少 8 位';
    if (p.length > 128) return '密码最多 128 位';
    return '';
  }

  async function submit(e) {
    e.preventDefault();
    const u = username.trim();
    const invalid = validate(u, password);
    if (invalid) {
      setError(invalid);
      return;
    }
    setError('');
    setBusy(true);
    try {
      const r =
        mode === 'login'
          ? await api.login(u, password)
          : await api.register(u, password);
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
        <div className="login-brand">
          <img src="/school-crest.png" alt="校徽" />
          <div>
            <h1>医疗知识图谱问答</h1>
            <p className="login-sub">基于OpenCMKG的中文医疗 Agent</p>
          </div>
        </div>
        <label>
          用户名
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="3~32 位字母、数字、_、-"
            autoComplete="username"
            pattern="[A-Za-z0-9_-]{3,32}"
            title="3~32 位字母、数字、下划线或短横线"
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
            minLength={8}
            maxLength={128}
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
        <div className="login-foot">
          <img src="/college-crest.png" alt="院徽" />
        </div>
      </form>
    </div>
  );
}
