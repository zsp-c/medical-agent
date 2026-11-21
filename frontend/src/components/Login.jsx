import { useState } from 'react';
import { api } from '../api';
import { IconLogo, IconShield } from './Icons';
import { formatNumber } from '../utils';

// P01 登录 / 注册：左侧品牌说明，右侧表单；规模数字来自免鉴权的 /api/meta
export default function Login({ meta, onLogin }) {
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const canRegister = mode === 'login' || meta?.allow_registration !== false;

  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    const name = username.trim();
    if (name.length < 3) {
      setError('用户名至少 3 位（字母、数字、下划线）');
      return;
    }
    if (password.length < 8) {
      setError('密码不少于 8 位');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const fn = mode === 'login' ? api.login : api.register;
      const data = await fn(name, password);
      onLogin(data.access_token, data.username, data.user_id);
    } catch (err) {
      setError(err.message || '请求失败，请稍后重试');
    } finally {
      setBusy(false);
    }
  }

  const stats = [
    { v: formatNumber(meta?.graph?.nodes ?? 0), l: '图谱实体' },
    { v: formatNumber(meta?.graph?.edges ?? 0), l: '关系边' },
    { v: formatNumber(meta?.questions ?? 0), l: '医学试题' },
  ];

  return (
    <div className="login">
      <div className="brandside">
        <svg className="bg-svg" width="420" height="420" viewBox="0 0 200 200" fill="none" stroke="#fff" strokeWidth="1">
          <circle cx="100" cy="100" r="26" />
          <circle cx="40" cy="58" r="15" />
          <circle cx="158" cy="52" r="12" />
          <circle cx="150" cy="150" r="17" />
          <circle cx="48" cy="150" r="13" />
          <path d="M100 74 55 66M100 74l47-16M100 126l42 18M100 126l-44 18M52 72l82-14M56 138l74 12" />
        </svg>

        <div className="brand">
          <span className="mark">
            <IconLogo />
          </span>
          医智问答
          <span className="en">MEDGRAPH AGENT</span>
        </div>

        <div className="pitch">
          <h1>
            让每一个医学回答
            <br />
            都能<em>追溯到证据</em>
          </h1>
          <p>
            基于中文医疗知识图谱的问答智能体。它先检索图谱与题库取得事实，
            再用大模型解释与组织语言——每一句结论都能点开到来源实体。
          </p>
          <div className="nums">
            {stats.map((s, i) => (
              <div key={s.l} style={{ display: 'flex', gap: 26 }}>
                {i > 0 && <div className="sep" />}
                <div>
                  <div className="v">{s.v}</div>
                  <div className="l">{s.l}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="stackline">
          OpenCMKG · Chroma · Neo4j · openjiuwen ReActAgent
        </div>
      </div>

      <div className="formside">
        <form onSubmit={submit}>
          <div className="h">{mode === 'login' ? '欢迎回来' : '创建账号'}</div>
          <div className="s">
            {mode === 'login'
              ? '登录后即可开始问诊式问答与题库练习'
              : '注册后自动登录，数据按账号隔离'}
          </div>

          <div className="field" style={{ marginTop: 26 }}>
            <label>用户名</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="3~32 位字母、数字或下划线"
              disabled={busy}
            />
          </div>

          <div className="field">
            <label>密码</label>
            <div style={{ position: 'relative' }}>
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                placeholder="不少于 8 位"
                style={{ paddingRight: 52 }}
                disabled={busy}
              />
              <button
                type="button"
                className="mini"
                style={{ position: 'absolute', right: 8, top: 8, height: 20 }}
                onClick={() => setShowPwd((v) => !v)}
              >
                {showPwd ? '隐藏' : '显示'}
              </button>
            </div>
            <div className="tip">用户名 3~32 位字母数字，密码不少于 8 位</div>
          </div>

          {error && <div className="err">{error}</div>}

          <button
            className="btn-primary block"
            style={{ height: 40, marginTop: 12, fontSize: 13 }}
            disabled={busy}
          >
            {busy ? '处理中…' : mode === 'login' ? '登 录' : '注册并登录'}
          </button>

          {canRegister && (
            <>
              <div className="divider-or">
                <i />或<i />
              </div>
              <button
                type="button"
                className="btn-line"
                style={{ width: '100%', height: 38 }}
                onClick={() => {
                  setMode(mode === 'login' ? 'register' : 'login');
                  setError('');
                }}
              >
                {mode === 'login' ? '注册新账号' : '已有账号，去登录'}
              </button>
            </>
          )}

          <div className="privacy">
            <span style={{ color: 'var(--brand)', marginTop: 1, display: 'inline-flex' }}>
              <IconShield />
            </span>
            <div>
              模型 Key 由你自带（BYOK），<b>只保存在浏览器本地</b>，服务端不落库、不写日志。
            </div>
          </div>

          <div className="foot">回答基于图谱检索结果生成，不构成诊疗建议</div>
        </form>
      </div>
    </div>
  );
}
