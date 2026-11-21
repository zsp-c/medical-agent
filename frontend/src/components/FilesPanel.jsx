import { useRef, useState } from 'react';
import { IconInfo, IconPaperclip, IconSend, IconUpload } from './Icons';

const ACCEPT = '.txt,.md,.docx,.pptx,.png,.jpg,.jpeg,.webp,.gif';

function extClass(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext)) return 'i';
  if (['docx', 'pptx'].includes(ext)) return 'd';
  return 'x';
}

function extLabel(name) {
  const ext = (name.split('.').pop() || '?').toUpperCase();
  return ext.length > 4 ? ext.slice(0, 4) : ext;
}

function sizeLabel(bytes) {
  if (!bytes) return '0 KB';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// P08 附件：拖拽/选择上传 → 服务端解析成纯文本 → 发消息时按 file_id 注入
export default function FilesPanel({
  sessionTitle,
  files,
  uploading,
  notice,
  input,
  setInput,
  onUpload,
  onRemove,
  onSend,
  onBack,
}) {
  const inputRef = useRef(null);
  const [over, setOver] = useState(false);

  const ready = files.filter((f) => f.status === 'ready');
  const maxFiles = 3;

  function pick(e) {
    const picked = Array.from(e.target.files || []);
    e.target.value = '';
    if (picked.length) onUpload(picked);
  }

  function drop(e) {
    e.preventDefault();
    setOver(false);
    const picked = Array.from(e.dataTransfer.files || []);
    if (picked.length) onUpload(picked);
  }

  return (
    <div className="main">
      <div className="mhead">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="title">附件 · {sessionTitle || '新对话'}</div>
        </div>
        <span className="tag">单次最多 {maxFiles} 个文件</span>
        <span className="tag">单文件 ≤ 20 MB</span>
        <button className="btn-line" style={{ height: 28 }} onClick={onBack}>
          返回对话
        </button>
      </div>

      <div className="chat">
        <div className="thread" style={{ paddingTop: 8 }}>
          <input ref={inputRef} type="file" multiple hidden accept={ACCEPT} onChange={pick} />
          <div
            className={`drop${over ? ' over' : ''}`}
            role="button"
            tabIndex={0}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click();
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={drop}
          >
            <div className="ic">
              <IconUpload />
            </div>
            <div className="t">{uploading ? '解析中，请稍候…' : '拖拽文件到这里，或点击选择'}</div>
            <div className="d">
              支持 .txt / .md / .docx / .pptx / .png / .jpg / .webp / .gif
              <br />
              上传后立即解析为纯文本；图片经视觉模型转写为描述文字
            </div>
          </div>

          {notice && <div className="notice" style={{ marginTop: 10 }}>{notice}</div>}

          <div className="section-label">本次会话附件（{files.length}）</div>

          {!files.length && (
            <div style={{ fontSize: 11.5, color: 'var(--mute)', padding: '10px 2px', lineHeight: 1.8 }}>
              还没有附件。上传后会自动附加到本会话的下一轮提问。
            </div>
          )}

          {files.map((f) => {
            const failed = f.status !== 'ready';
            return (
              <div key={f.file_id} className={`file${failed ? ' failed' : ''}`}>
                <div className={`ext ${failed ? 'x' : extClass(f.name)}`}>{extLabel(f.name)}</div>
                <div className="body">
                  <div className="n">{f.name}</div>
                  <div className={`m${failed ? ' err' : ''}`}>
                    {failed
                      ? f.error || '解析失败，请重试'
                      : `${sizeLabel(f.size)} · 已解析 ${(f.chars || 0).toLocaleString('en-US')} 字 · 注入时按 6,000 字截断`}
                  </div>
                </div>
                <span className={`tag ${failed ? 'bad' : 'ok'}`}>{failed ? 'failed' : 'ready'}</span>
                <button className="mini" onClick={() => onRemove(f)}>
                  移除
                </button>
              </div>
            );
          })}

          <div className="tipbox">
            <span style={{ color: 'var(--brand)', flex: 'none' }}>
              <IconInfo />
            </span>
            <div>
              文件解析只在上传时执行一次，之后每轮对话直接读库；
              <b>仅本人可见</b>，删除时同时清理磁盘与数据行。
            </div>
          </div>

          <div className="quick" style={{ marginTop: 14 }}>
            <button className="q" onClick={() => onSend('结合这份附件，有哪些异常指标需要关注？')}>
              结合这份附件，有哪些异常指标需要关注？
            </button>
            <button className="q" onClick={() => onSend('帮我把附件里的关键指标解释一遍')}>
              帮我把附件里的关键指标解释一遍
            </button>
          </div>
        </div>
      </div>

      <div className="composer">
        <div className="box">
          <div className="attach-tag">
            {ready.length > 0 ? (
              <>
                <span className="tag teal">已附加 {ready.length} 个文件</span>
                <span className="tag">将注入本轮 query</span>
              </>
            ) : (
              <span className="tag">未附加文件 · 可直接提问</span>
            )}
          </div>
          <textarea
            rows={1}
            value={input}
            placeholder="结合附件提问，例如：这份体检报告有什么异常？"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                onSend();
              }
            }}
          />
          <div className="row">
            <button className="icon-btn" title="上传文件" onClick={() => inputRef.current?.click()}>
              <IconPaperclip />
            </button>
            <div className="spacer" />
            <button className="send" disabled={!input.trim()} onClick={() => onSend()}>
              <IconSend />
            </button>
          </div>
        </div>
        <div className="hintline">附件内容会作为参考资料拼在本轮问题前，不会改变检索范围之外的结论。</div>
      </div>
    </div>
  );
}
