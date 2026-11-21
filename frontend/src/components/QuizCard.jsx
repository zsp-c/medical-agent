import { useState } from 'react';
import { IconCheck, IconClose, IconQuiz } from './Icons';

const LETTER = 'ABCDEFGH';

function stripPrefix(text) {
  return String(text || '').replace(/^[A-Ha-h][.、．)）]\s*/, '');
}

// 互动题卡：未作答时可点选提交；提交后锁定，按服务端判题结果着色
export default function QuizCard({
  quiz,
  locked = false,
  chosen = '',
  correct = null,
  answer = '',
  disabled = false,
  onSubmit,
  onRetry,
  // 练习页不换题（换题会丢进度），也不走模型判题，文案与按钮需可配
  hideRetry = false,
  note = '提交后由模型判题，答案不下发前端',
}) {
  const options = quiz.options || [];
  const isOpen = quiz.kind === 'open' || !options.length;
  const [selected, setSelected] = useState(-1);
  const [text, setText] = useState('');

  const canSubmit = isOpen ? text.trim().length > 0 : selected >= 0;
  const judged = locked && correct !== null && correct !== undefined;
  const pickedLetter =
    chosen || (selected >= 0 ? LETTER[selected] : '') || '';

  function submit() {
    if (!canSubmit || locked || disabled) return;
    if (isOpen) {
      onSubmit(`我的回答：${text.trim()}`);
      return;
    }
    onSubmit(`我选${LETTER[selected]}：${stripPrefix(options[selected])}`, {
      chosen: LETTER[selected],
    });
  }

  const headClass = judged ? (correct ? 'qh ok' : 'qh bad') : 'qh';
  const headIcon = judged ? (
    correct ? (
      <IconCheck />
    ) : (
      <IconClose />
    )
  ) : (
    <IconQuiz size={12} />
  );
  const headText = judged
    ? `${correct ? '回答正确' : '本题未答对'}${answer ? ` · 正确答案 ${answer}` : ''}`
    : locked
    ? '已提交 · 等待讲解'
    : `单选题${quiz.subject ? ` · ${quiz.subject}` : ''}`;

  return (
    <div className="quiz">
      <div className={headClass}>
        {headIcon}
        {isOpen ? (locked ? headText : '随堂问答') : headText}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: 'var(--mute)', fontWeight: 400, marginLeft: 8 }}>
          {locked
            ? '已作答，题卡锁定'
            : quiz.source === 'question_bank'
            ? '来源：医学考试题库'
            : '来源：按知识点自拟'}
        </span>
      </div>

      <div className="qb">
        <div className="stem">{quiz.stem}</div>

        {isOpen ? (
          locked ? (
            <div className="explain" style={{ marginTop: 0 }}>
              你的作答：{chosen || text || '—'}
            </div>
          ) : (
            <textarea
              className="quiz-answer"
              rows={3}
              placeholder="写下你的答案…"
              value={text}
              disabled={disabled}
              onChange={(e) => setText(e.target.value)}
            />
          )
        ) : (
          options.map((opt, i) => {
            const letter = LETTER[i];
            const isRight = judged && answer === letter;
            const isWrongPick = judged && !correct && pickedLetter === letter;
            const cls = [
              'opt',
              !locked && selected === i ? 'sel' : '',
              isRight ? 'right' : '',
              isWrongPick ? 'wrong' : '',
            ]
              .filter(Boolean)
              .join(' ');
            return (
              <button
                key={i}
                className={cls}
                disabled={locked || disabled}
                onClick={() => setSelected(i)}
              >
                <span className="k">{letter}</span>
                <span>{stripPrefix(opt)}</span>
                {isRight && <span className="mark">正确答案</span>}
                {isWrongPick && <span className="mark">你选了这项</span>}
              </button>
            );
          })
        )}
      </div>

      {!locked && (
        <div className="qf">
          <button className="btn-primary" style={{ height: 32 }} disabled={!canSubmit || disabled} onClick={submit}>
            提交答案
          </button>
          {!hideRetry && (
            <button className="btn-line" style={{ height: 32 }} disabled={disabled} onClick={onRetry}>
              换一题
            </button>
          )}
          <span className="note">{note}</span>
        </div>
      )}
    </div>
  );
}
