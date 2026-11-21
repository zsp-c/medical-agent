import { useEffect, useRef, useState } from 'react';
import Message from './Message';
import { IconDoc, IconPaperclip, IconQuiz, IconSearch, IconSend, IconStop } from './Icons';
import { formatNumber } from '../utils';

const QUICK_PROMPTS = [
  '我父亲有高血压，平时要注意什么？',
  '二甲双胍常见不良反应有哪些？',
  '帮我出一道药理学的单选题',
  '高血压可能累及哪些器官？',
];

const CAPS = [
  {
    icon: <IconSearch />,
    title: '查疾病 · 查用药',
    desc: '症状、药物、饮食、就诊科室，多跳关系一次问清',
    prompt: '高血压有哪些常见症状和并发症？',
  },
  {
    icon: <IconQuiz />,
    title: '练一道题',
    desc: '从 346 个学科的真题目录里抽题，作答后逐项讲解',
    prompt: '帮我出一道药理学的单选题',
  },
  {
    icon: <IconDoc />,
    title: '看懂报告',
    desc: '上传体检单 / 指南 / 图片，解析成文本后带入本轮对话',
    files: true,
  },
];

export default function ChatView({
  meta,
  sessionTitle,
  messages,
  busy,
  model,
  memoryUsed,
  lastMeta,
  notice,
  input,
  setInput,
  onSend,
  onStop,
  onOpenFiles,
  onOpenGraph,
  onQuizSubmit,
  onQuizRetry,
  onRegenerate,
  attachments,
  onRemoveAttachment,
  attachmentsBusy,
  onOpenSettings,
}) {
  const bottomRef = useRef(null);
  const taRef = useRef(null);
  // 生成中的秒表：让「已用 x.xs」真实走动，而不是等结束后才出现
  const [, setTick] = useState(0);
  // 降级提示条可以被用户收起，换会话不再打扰
  const [bannerOff, setBannerOff] = useState(false);

  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(() => setTick((v) => v + 1), 200);
    return () => clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  function autoSize() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 110)}px`;
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!busy && input.trim()) onSend();
    }
  }

  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant' && !m.streaming);
  const followUps =
    lastAssistant && !busy && messages[messages.length - 1] === lastAssistant
      ? ['帮我出一道相关练习题', '这个病需要做哪些检查？', '日常饮食要注意什么？']
      : [];

  // 图谱后端：以本轮回答的 meta 为准（最真实），没有回答时退回启动时拿到的 meta
  const graphBackend = lastMeta?.graph_backend || meta?.graph?.backend || '';
  const graphDegraded = Boolean(graphBackend) && graphBackend !== 'neo4j';
  const graphOnline = meta?.graph?.backend === 'neo4j';
  const graphReason = meta?.graph?.reason || '';
  const graphNodes = meta?.graph?.nodes || 0;
  // 提示条以服务级状态为准（meta），拿不到 meta 时退回本轮回答的真实后端
  const showGraphBanner =
    !bannerOff &&
    (meta ? Boolean(meta.graph?.degraded) : lastMeta?.graph_backend === 'sample');

  return (
    <div className="main">
      <div className="mhead">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="title">{sessionTitle || '新对话'}</div>
        </div>
        {busy ? (
          <>
            <span className="tag ok">
              <span className="dot" /> 已连接
            </span>
            <span className="tag">SSE 流式</span>
            <span className="tag">{model}</span>
          </>
        ) : lastMeta ? (
          <>
            {graphDegraded ? (
              <span className="tag warn" title={graphReason || '当前回答基于内置样例数据'}>
                <span className="dot" /> 图谱降级
              </span>
            ) : (
              <span className="tag ok">
                <span className="dot" /> 图谱 {graphBackend || 'neo4j'}
              </span>
            )}
            {memoryUsed > 0 && <span className="tag blue">记忆已注入 {memoryUsed} 条</span>}
            {lastMeta.elapsed_ms ? (
              <span className="tag">{(lastMeta.elapsed_ms / 1000).toFixed(1)}s</span>
            ) : null}
          </>
        ) : (
          <>
            {graphDegraded ? (
              <span className="tag warn" title={graphReason || '当前使用内置样例数据'}>
                <span className="dot" /> 图谱降级 · 样例 {formatNumber(graphNodes)} 实体
              </span>
            ) : graphOnline ? (
              <span className="tag teal">
                <span className="dot" /> 图谱在线 · {formatNumber(graphNodes)} 实体
              </span>
            ) : (
              <span className="tag warn">
                <span className="dot" /> 图谱未连接
              </span>
            )}
            <span className="tag">{model}</span>
          </>
        )}
      </div>

      {showGraphBanner && (
        <div className="banner">
          <span className="bi">!</span>
          <div className="bt">
            <b>知识图谱服务未连接</b>
            <span>
              {graphReason || 'Neo4j 不可用'}
              ；当前基于内置 {formatNumber(graphNodes)} 条样例数据回答，覆盖范围有限。可在「设置 → 服务状态」重连图谱，无需重启后端。
            </span>
          </div>
          <button className="btn-line" style={{ height: 28 }} onClick={onOpenSettings}>
            去检查
          </button>
          <button className="bclose" title="收起提示" onClick={() => setBannerOff(true)}>
            ×
          </button>
        </div>
      )}

      <div className="chat">
        <div className="thread">
          {!messages.length ? (
            <div className="welcome">
              <div className="hi">
                你好，我是<span>医智问答</span>
              </div>
              <p className="desc">
                我会先在中文医疗知识图谱（OpenCMKG）与 {formatNumber(meta?.questions || 0)} 道医学题库中检索事实，
                再用模型为你组织答案。回答里的每个证据标签都可以点开，看到它在图谱中的邻居与关系。
              </p>

              <div className="capgrid">
                {CAPS.map((c) => (
                  <button
                    key={c.title}
                    className="cap"
                    onClick={() => (c.files ? onOpenFiles() : onSend(c.prompt))}
                  >
                    <div className="ic">{c.icon}</div>
                    <div className="h">{c.title}</div>
                    <div className="d">{c.desc}</div>
                  </button>
                ))}
              </div>

              <div className="quick">
                {QUICK_PROMPTS.map((q) => (
                  <button key={q} className="q" onClick={() => onSend(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => (
              <Message
                key={i}
                m={m}
                onOpenGraph={onOpenGraph}
                onQuizSubmit={onQuizSubmit}
                onQuizRetry={onQuizRetry}
                onRegenerate={onRegenerate}
                onFollowUp={onSend}
                followUps={i === messages.length - 1 ? followUps : []}
              />
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {notice && (
        <div style={{ padding: '0 24px' }}>
          <div className="notice">{notice}</div>
        </div>
      )}

      <div className="composer">
        <div className="box">
          {attachments.length > 0 && (
            <div className="attach-tag">
              {attachments.map((f) => (
                <span key={f.file_id} className="tag teal" title={f.name}>
                  📎 {f.name.length > 18 ? `${f.name.slice(0, 18)}…` : f.name}
                  <button
                    className="mini"
                    style={{ marginLeft: 4, padding: '0 5px' }}
                    onClick={() => onRemoveAttachment(f)}
                  >
                    ×
                  </button>
                </span>
              ))}
              <span className="tag">将注入本轮 query</span>
            </div>
          )}
          <textarea
            ref={taRef}
            rows={1}
            value={input}
            placeholder={
              busy
                ? '回答生成中，可继续输入…'
                : messages.length
                ? '继续追问…'
                : '描述你的问题，例如：我父亲有高血压，平时要注意什么？'
            }
            onChange={(e) => {
              setInput(e.target.value);
              autoSize();
            }}
            onKeyDown={onKeyDown}
          />
          <div className="row">
            <button className="icon-btn" title="上传文件" onClick={onOpenFiles} disabled={attachmentsBusy}>
              <IconPaperclip />
            </button>
            <div className="spacer" />
            <span style={{ fontSize: 10.5, color: 'var(--mute)' }}>Enter 发送 · Shift+Enter 换行</span>
            {busy ? (
              <>
                <button className="btn-line" style={{ height: 28 }} onClick={onStop}>
                  停止生成
                </button>
                <button className="send" title="停止">
                  <IconStop />
                </button>
              </>
            ) : (
              <button className="send" disabled={!input.trim()} onClick={() => onSend()}>
                <IconSend />
              </button>
            )}
          </div>
        </div>
        <div className="hintline">
          回答基于知识图谱检索结果生成，不构成诊疗建议；用药请遵医嘱。
        </div>
      </div>
    </div>
  );
}
