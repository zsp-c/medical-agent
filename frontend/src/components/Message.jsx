import ReactMarkdown from 'react-markdown';
import QuizCard from './QuizCard';
import { IconCopy, IconLayers, IconLike, IconRefresh, IconAlert } from './Icons';
import { entityName, entityTypeOf, clockOf, typeLabel } from '../utils';

function Steps({ steps, streaming }) {
  const items = [
    { label: '理解问题意图', state: 'done' },
    ...steps.map((s) => ({
      label: s.label,
      tool: s.name,
      state: s.status === 'done' ? 'done' : 'now',
    })),
    {
      label: '汇总证据并组织回答',
      state: streaming ? (steps.length ? 'pending' : 'now') : 'done',
    },
  ];
  return (
    <div className="steps">
      {items.map((it, i) => (
        <div key={i} className={`step ${it.state === 'pending' ? '' : it.state}`}>
          <span className="k">{it.state === 'done' ? '✓' : ''}</span>
          {it.label}
          {it.tool && <span className="tool">{it.tool}</span>}
          {it.state === 'now' && <span className="res">检索中…</span>}
        </div>
      ))}
    </div>
  );
}

function Evidence({ evidence, onOpenGraph }) {
  // 全量图谱一轮可能命中几十个实体（药品会挂到各生产厂家），
  // 全部铺开会把回答淹掉：只展示前 12 个，其余折叠成计数。
  const MAX_CHIPS = 12;
  if (!evidence || !evidence.length) return null;
  const shown = evidence.slice(0, MAX_CHIPS);
  const rest = evidence.length - shown.length;
  return (
    <div className="evidence">
      <div className="eh">
        <IconLayers />
        本轮引用图谱证据 · {evidence.length} 个实体（点击查看图谱位置）
      </div>
      <div className="chips">
        {shown.map((e, i) => {
          const type = entityTypeOf(e);
          return (
            <span
              key={e.entity_key || e.key || i}
              className="chip"
              data-ty={type}
              title={e.entity_key || e.key}
              onClick={() => onOpenGraph(e.entity_key || e.key)}
            >
              {entityName(e)} <span className="ty">{typeLabel(type)}</span>
            </span>
          );
        })}
        {rest > 0 && (
          <span className="chip more" title="其余实体可在图谱页按实体名检索">
            +{rest} 个
          </span>
        )}
      </div>
    </div>
  );
}

function statLine(m) {
  const s = m.stats || {};
  const tokens =
    typeof s.tokens === 'number' ? s.tokens : (s.tokens?.prompt || 0) + (s.tokens?.completion || 0);
  const parts = [];
  if (s.model_calls) parts.push(`模型 ${s.model_calls} 次`);
  if (s.tool_calls) parts.push(`工具 ${s.tool_calls} 次`);
  if (tokens) parts.push(`${tokens.toLocaleString('en-US')} tokens`);
  if (s.elapsed_ms) parts.push(`${(s.elapsed_ms / 1000).toFixed(1)}s`);
  return parts.join(' · ');
}

export default function Message({
  m,
  onOpenGraph,
  onQuizSubmit,
  onQuizRetry,
  onRegenerate,
  onFollowUp,
  followUps = [],
}) {
  if (m.role === 'user') {
    return (
      <div className="msg user">
        <div className="bubble-user">
          {m.files?.length > 0 && (
            <div className="files">
              {m.files.map((f, i) => (
                <span key={i}>📎 {typeof f === 'string' ? f : f.name || f.filename || '附件'}</span>
              ))}
            </div>
          )}
          {m.content}
        </div>
      </div>
    );
  }

  const streaming = m.streaming;
  // 注意：必须是布尔值。`m.steps && m.steps.length` 在 steps 为空数组时返回 0，
  // `{0 && ...}` 会把字面量 0 渲染到回答上方。
  const showSteps = Boolean(streaming || m.steps?.length);
  const hasAnswer = Boolean(m.content);

  return (
    <div className="msg">
      <div className="who">
        <div className="avatar" style={{ width: 24, height: 24, fontSize: 10 }}>
          医
        </div>
        <div className="n">医智问答</div>
        <span className="tm">{streaming ? '正在回答…' : clockOf(m.at)}</span>
      </div>

      {showSteps && <Steps steps={m.steps || []} streaming={streaming} />}

      {hasAnswer && (
        <div className="answer">
          <ReactMarkdown>{m.content}</ReactMarkdown>
          {streaming && <span className="caret">▍</span>}
        </div>
      )}

      {!hasAnswer && streaming && (
        <div className="answer">
          <p style={{ color: 'var(--mute)' }}>
            <span className="typing">
              <i />
              <i />
              <i />
            </span>
          </p>
        </div>
      )}

      {m.quiz && (
        <div style={{ marginTop: 12 }}>
          <QuizCard
            quiz={m.quiz}
            locked={m.quizLocked}
            chosen={m.quizChosen}
            correct={m.quizCorrect}
            answer={m.quizAnswer}
            disabled={streaming}
            onSubmit={(text, info) => onQuizSubmit(m, text, info)}
            onRetry={() => onQuizRetry(m)}
          />
        </div>
      )}

      {m.error && (
        <div className="explain" style={{ borderColor: '#F8D6D6', background: 'var(--bad-soft)', color: '#9C2B2B' }}>
          <IconAlert /> 出错了：{m.error}
        </div>
      )}

      <Evidence evidence={m.evidence} onOpenGraph={onOpenGraph} />

      {!streaming && hasAnswer && (
        <div className="msgfoot">
          <button
            className="icon-btn"
            title="复制回答"
            onClick={() => navigator.clipboard?.writeText(m.content || '')}
          >
            <IconCopy />
          </button>
          <button className="icon-btn" title="重新生成" onClick={() => onRegenerate(m)}>
            <IconRefresh />
          </button>
          <button className="icon-btn" title="有帮助">
            <IconLike />
          </button>
          <span style={{ fontSize: 10.5, color: 'var(--mute)', marginLeft: 4 }}>{statLine(m)}</span>
        </div>
      )}

      {!streaming && followUps.length > 0 && (
        <div className="quick" style={{ marginTop: 12, marginBottom: 0 }}>
          {followUps.map((q) => (
            <button key={q} className="q" onClick={() => onFollowUp(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
