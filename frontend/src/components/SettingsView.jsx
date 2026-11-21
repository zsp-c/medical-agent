import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { formatNumber } from '../utils';
import { IconInfo, IconShield } from './Icons';

const PRESETS = [
  { name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { name: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-plus' },
  {
    name: '阿里百炼',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
  },
];

// P09 设置：BYOK 模型配置（只存浏览器）+ 服务状态 + 账号信息
export default function SettingsView({
  username,
  userId,
  token,
  llm,
  onSave,
  onBack,
  onClearMemory,
  onLogout,
  onStatusChange,
}) {
  const [apiKey, setApiKey] = useState(llm?.api_key || '');
  const [baseUrl, setBaseUrl] = useState(llm?.base_url || 'https://api.deepseek.com/v1');
  const [model, setModel] = useState(llm?.model || 'deepseek-chat');
  const [showKey, setShowKey] = useState(false);
  const [saved, setSaved] = useState('');
  // 服务状态：图谱 / 向量 / 题库 / 模型，进页面探测一次，可手动刷新
  const [status, setStatus] = useState(null);
  const [statusErr, setStatusErr] = useState('');
  const [statusBusy, setStatusBusy] = useState('');

  const loadStatus = useCallback(async () => {
    if (!token) return;
    setStatusBusy('check');
    setStatusErr('');
    try {
      setStatus(await api.status(token));
    } catch (e) {
      setStatusErr(e.message || '读取服务状态失败');
    } finally {
      setStatusBusy('');
    }
  }, [token]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  async function reconnect() {
    setStatusBusy('reconnect');
    setStatusErr('');
    try {
      const graph = await api.reconnectGraph(token);
      await loadStatus();
      if (!graph.degraded) setSaved('图谱已连上全量数据');
      else setStatusErr(`仍然连不上：${graph.reason || 'Neo4j 无响应'}`);
      onStatusChange?.();
    } catch (e) {
      setStatusErr(e.message || '重连失败');
    } finally {
      setStatusBusy('');
    }
  }

  function applyPreset(p) {
    setBaseUrl(p.base_url);
    setModel(p.model);
    setSaved('');
  }

  function submit(e) {
    e.preventDefault();
    if (!apiKey.trim()) {
      onSave(null);
      setSaved('已清除：后续请求使用服务端默认模型配置');
      return;
    }
    if (!baseUrl.trim() || !model.trim()) {
      setSaved('Base URL 与模型名都要填写');
      return;
    }
    onSave({ api_key: apiKey.trim(), base_url: baseUrl.trim(), model: model.trim() });
    setSaved('已保存到浏览器本地，下一条消息生效');
  }

  function clearKey() {
    setApiKey('');
    setShowKey(false);
    onSave(null);
    setSaved('Key 已清除，服务端无副本');
  }

  return (
    <div className="page">
      <div className="phead">
        <div className="crumb">
          <span className="link" onClick={onBack}>
            对话
          </span>
          /<b>设置</b>
        </div>
        <div style={{ flex: 1 }} />
        {saved && <span className="tag ok">{saved}</span>}
        <button className="btn-line" style={{ height: 28 }} onClick={onBack}>
          返回对话
        </button>
      </div>

      <div className="pbody" style={{ display: 'flex', flexDirection: 'column' }}>
        <div className="settings-wrap" style={{ margin: 'auto' }}>
          <div className="settings-title">模型与隐私</div>
          <div className="settings-desc">
            你的模型 Key 只存在于当前浏览器，服务端不存储、不写日志、不进入任何 meta 字段。
          </div>

          <form onSubmit={submit}>
            <div className="panel" style={{ marginBottom: 14 }}>
              <div className="ph">BYOK 模型配置（OpenAI 兼容）</div>
              <div className="pb" style={{ padding: 16 }}>
                <div className="presets">
                  <span className="label">快捷填充：</span>
                  {PRESETS.map((p) => (
                    <button
                      type="button"
                      key={p.name}
                      className="preset"
                      onClick={() => applyPreset(p)}
                    >
                      {p.name}
                    </button>
                  ))}
                </div>

                <div className="field">
                  <label>API Key</label>
                  <div className="key-wrap">
                    <input
                      type={showKey ? 'text' : 'password'}
                      value={apiKey}
                      placeholder="sk-…（留空则使用服务端默认配置）"
                      onChange={(e) => {
                        setApiKey(e.target.value);
                        setSaved('');
                      }}
                      autoComplete="off"
                    />
                    <div className="acts">
                      <button type="button" className="mini" onClick={() => setShowKey((v) => !v)}>
                        {showKey ? '隐藏' : '显示'}
                      </button>
                      <button type="button" className="mini" onClick={clearKey}>
                        清除
                      </button>
                    </div>
                  </div>
                  <div className="tip">建议使用有限额、可随时撤销的 Key；清除后立即失效，服务端无副本。</div>
                </div>

                <div className="field">
                  <label>Base URL</label>
                  <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
                  <div className="tip">填完整的 OpenAI 兼容根路径（含 /v1），例如 https://api.deepseek.com/v1</div>
                </div>

                <div className="field" style={{ marginBottom: 0 }}>
                  <label>模型名</label>
                  <input value={model} onChange={(e) => setModel(e.target.value)} />
                  <div className="tip">
                    视觉识别附件图片时会复用此配置；模型不支持视觉时该文件标记 failed 并明确提示。
                  </div>
                </div>

                <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                  <button className="btn-primary teal" style={{ height: 34 }} type="submit">
                    保存配置
                  </button>
                  <button
                    className="btn-line"
                    style={{ height: 34 }}
                    type="button"
                    onClick={() => {
                      setApiKey(llm?.api_key || '');
                      setBaseUrl(llm?.base_url || 'https://api.deepseek.com/v1');
                      setModel(llm?.model || 'deepseek-chat');
                      setSaved('');
                    }}
                  >
                    撤销修改
                  </button>
                </div>
              </div>
            </div>
          </form>

          <div className="panel" style={{ marginBottom: 14 }}>
            <div className="ph">账号</div>
            <div className="pb account-row">
              <div className="avatar" style={{ width: 36, height: 36, fontSize: 13 }}>
                {(username || 'U').slice(0, 2).toUpperCase()}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="n">{username}</div>
                <div className="id">user_id {userId || '—'}</div>
              </div>
              <button className="btn-line" style={{ height: 30 }} onClick={onLogout}>
                退出登录
              </button>
            </div>
          </div>

          <div className="panel" style={{ marginBottom: 14 }}>
            <div className="ph" style={{ display: 'flex', alignItems: 'center' }}>
              <span>服务状态</span>
              <span style={{ flex: 1 }} />
              <button
                className="mini"
                type="button"
                onClick={loadStatus}
                disabled={statusBusy === 'check'}
              >
                {statusBusy === 'check' ? '检测中…' : '重新检测'}
              </button>
            </div>
            <div className="pb" style={{ padding: '2px 16px 12px' }}>
              <div className="status-row">
                <span className="sn">知识图谱</span>
                <span className="sv">
                  {status
                    ? status.graph.degraded
                      ? `样例数据 · ${formatNumber(status.graph.nodes)} 实体 / ${formatNumber(status.graph.edges)} 关系`
                      : `Neo4j · ${formatNumber(status.graph.nodes)} 实体 / ${formatNumber(status.graph.edges)} 关系`
                    : '检测中…'}
                </span>
                {status &&
                  (status.graph.degraded ? (
                    <span className="tag warn">降级</span>
                  ) : (
                    <span className="tag ok">正常</span>
                  ))}
              </div>

              {status?.graph?.degraded && (
                <div
                  className="snote"
                  title={`GRAPH_BACKEND=${status.graph.configured} · ${status.graph.uri} / ${status.graph.database}`}
                >
                  <b>原因：</b>
                  {status.graph.reason || 'Neo4j 未连接'}
                  <button
                    className="mini"
                    type="button"
                    style={{ marginLeft: 8 }}
                    onClick={reconnect}
                    disabled={statusBusy === 'reconnect'}
                  >
                    {statusBusy === 'reconnect' ? '重连中…' : '重连图谱'}
                  </button>
                </div>
              )}

              <div className="status-row">
                <span className="sn">向量服务</span>
                <span className="sv">
                  {status
                    ? status.embedding.ready
                      ? `${status.embedding.model} · 题库检索与长期记忆可用`
                      : `${status.embedding.endpoint} 未响应（题库退回关键词匹配）`
                    : '检测中…'}
                </span>
                {status &&
                  (status.embedding.ready ? (
                    <span className="tag ok">就绪</span>
                  ) : (
                    <span className="tag warn">未连接</span>
                  ))}
              </div>

              <div className="status-row">
                <span className="sn">题库</span>
                <span className="sv">
                  {status ? `${formatNumber(status.questions)} 道真题` : '检测中…'}
                </span>
                {status && <span className="tag">{status.questions ? '可检索' : '空'}</span>}
              </div>

              <div className="status-row">
                <span className="sn">当前模型</span>
                <span className="sv">
                  {llm?.model ? `${llm.model}（你的 Key）` : status ? status.llm.model : '检测中…'}
                </span>
                <span className="tag">{llm?.model ? 'BYOK' : '服务端默认'}</span>
              </div>

              {statusErr && <div className="snote bad">{statusErr}</div>}
            </div>
          </div>

          <div className="panel" style={{ marginBottom: 14 }}>
            <div className="ph">长期记忆</div>
            <div className="pb" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ flex: 1, fontSize: 11.5, color: 'var(--sub)', lineHeight: 1.8, display: 'flex', gap: 8 }}>
                <span style={{ color: 'var(--brand)', flex: 'none' }}>
                  <IconInfo size={13} />
                </span>
                跨会话沉淀的偏好与病史信息保存在服务端；清空后不可恢复，也不影响已有会话记录。
              </div>
              <button className="btn-line" style={{ height: 30 }} onClick={onClearMemory}>
                清空长期记忆
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 14, fontSize: 10.5, color: 'var(--mute)' }}>
            <IconShield size={12} />
            Key 仅以明文存放在浏览器 localStorage，共享电脑请用完清除。
          </div>
        </div>
      </div>
    </div>
  );
}
