import { useState } from 'react';

// BYOK 模型设置：仅保存在浏览器 localStorage，随对话请求发送，服务端不持久化。
export default function SettingsModal({ initial, onSave, onClose }) {
  const [baseUrl, setBaseUrl] = useState(initial?.base_url || '');
  const [model, setModel] = useState(initial?.model || '');
  const [apiKey, setApiKey] = useState(initial?.api_key || '');

  function save(e) {
    e.preventDefault();
    const cfg = {
      base_url: baseUrl.trim(),
      model: model.trim(),
      api_key: apiKey.trim(),
    };
    if (!cfg.api_key || !cfg.base_url || !cfg.model) {
      onSave(null); // 三项缺一即视为不用 BYOK，回退服务端默认模型
      return;
    }
    onSave(cfg);
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <h2>模型设置（BYOK）</h2>
        <p className="modal-tip">
          使用你自己的 OpenAI 兼容 API。配置仅保存在本浏览器，随每次对话请求发送，服务器不存储。
          留空任意一项则使用服务端默认模型。
        </p>
        <label>
          Base URL
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.deepseek.com/v1"
          />
        </label>
        <label>
          模型名
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
        </label>
        <label>
          API Key
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-…"
            autoComplete="off"
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="link" onClick={() => onSave(null)}>
            清除并使用默认模型
          </button>
          <span className="flex" />
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary" type="submit">
            保存
          </button>
        </div>
      </form>
    </div>
  );
}
