import { useMemo, useState } from 'react';
import QuizCard from './QuizCard';
import { api } from '../api';

// 练习页（一次多题）：错题重练 / 随机组卷都走这里。
// 与对话里的题卡共用 QuizCard，但判题直接打 /api/practice/submit（不经过模型），
// 因此答案只在这里拿到，题卡本身仍然不含 answer。
export default function PracticeRun({
  token,
  title = '重练错题',
  subtitle = '',
  quizzes = [],
  loading = false,
  loadError = '',
  onBack,
  onExit,
  onAgain,
  onStartDrill,
  againLabel = '再来一组',
}) {
  const [answers, setAnswers] = useState({});
  const [error, setError] = useState('');

  const total = quizzes.length;
  // 只统计当前这组题：切组后旧 quiz_id 的答案不应影响进度
  const current = useMemo(
    () =>
      Object.entries(answers).filter(([qid]) =>
        quizzes.some((q) => q.quiz_id === qid)
      ),
    [answers, quizzes]
  );
  const answered = useMemo(
    () => current.filter(([, a]) => !a.pending && a.correct !== undefined).length,
    [current]
  );
  const correct = useMemo(
    () => current.filter(([, a]) => a.correct === true).length,
    [current]
  );
  const finished = total > 0 && answered >= total;

  async function submit(quiz, chosen, extra = {}) {
    if (!quiz?.quiz_id || !chosen) return;
    setError('');
    setAnswers((prev) => ({ ...prev, [quiz.quiz_id]: { chosen, pending: true } }));
    try {
      const r = await api.submitPractice(token, quiz.quiz_id, extra.chosen || chosen);
      setAnswers((prev) => ({
        ...prev,
        [quiz.quiz_id]: {
          chosen: r.chosen || chosen,
          correct: r.correct,
          answer: r.answer,
          pending: false,
        },
      }));
    } catch (e) {
      setAnswers((prev) => ({ ...prev, [quiz.quiz_id]: { pending: false } }));
      setError(e.message || '提交失败，请重试');
    }
  }

  return (
    <div className="page">
      <div className="phead">
        <div className="crumb">
          <span className="link" onClick={onExit || onBack}>
            对话
          </span>
          /<span className="link" onClick={onBack}>
            练习中心
          </span>
          /<b>{title}</b>
        </div>
        <div style={{ flex: 1 }} />
        {total > 0 && (
          <>
            <span className="tag">
              已答 {answered}/{total}
            </span>
            <span className={`tag ${answered && correct === answered ? 'ok' : ''}`}>
              对 {correct} 题
            </span>
          </>
        )}
        {subtitle && <span className="tag">{subtitle}</span>}
        <button className="btn-line" style={{ height: 28 }} onClick={onBack}>
          返回统计
        </button>
      </div>

      <div className="pbody">
        <div className="run-wrap">
          {loadError && (
            <div className="run-err" style={{ marginBottom: 12 }}>
              组卷失败：{loadError}
            </div>
          )}

          {loading && (
            <div className="empty-state">
              <div className="t">正在组卷…</div>
              <div className="d">从你的作答记录里取出需要重练的题目</div>
            </div>
          )}

          {!loading && !total && (
            <div className="empty-state">
              <div className="t">这里暂时没有可练的题</div>
              <div className="d">
                {title.includes('错题')
                  ? '你还没有答错的题。可以先从题库随机练一组，或回对话让 AI 按知识点出题。'
                  : '题库暂时取不到题目，可能是题库未导入。可以回对话让 AI 按知识点出题。'}
              </div>
              <div className="btns">
                <button
                  className="btn-primary"
                  onClick={onStartDrill || onAgain}
                >
                  {onStartDrill ? '随机练 5 题' : againLabel}
                </button>
                <button className="btn-line" onClick={onExit || onBack}>
                  返回对话
                </button>
              </div>
            </div>
          )}

          {!loading && total > 0 && (
            <>
              <div className="runbar" title={`已完成 ${answered}/${total}`}>
                <span
                  className="fill"
                  style={{ width: `${Math.round((answered / total) * 100)}%` }}
                />
              </div>

              {finished && (
                <div className={`run-summary ${correct === total ? 'ok' : ''}`}>
                  <div>
                    <div className="sv">
                      {correct}
                      <span className="su">/{total}</span>
                    </div>
                    <div className="sd">
                      正确率 {Math.round((correct / total) * 100)}%
                      {correct === total ? ' · 全部答对' : ` · 错 ${total - correct} 题`}
                    </div>
                  </div>
                  <div className="acts">
                    <button className="btn-line" style={{ height: 30 }} onClick={onAgain}>
                      {againLabel}
                    </button>
                    <button className="btn-primary" style={{ height: 30 }} onClick={onBack}>
                      返回统计
                    </button>
                  </div>
                </div>
              )}

              {error && <div className="run-err">{error}</div>}

              {quizzes.map((q, i) => {
                const a = answers[q.quiz_id] || {};
                return (
                  <div className="runq" key={q.quiz_id}>
                    <div className="runq-no">
                      <b>{i + 1}</b>
                      <span>/ {total}</span>
                    </div>
                    <QuizCard
                      quiz={q}
                      locked={!a.pending && a.correct !== undefined}
                      chosen={a.chosen || ''}
                      correct={a.correct}
                      answer={a.answer || ''}
                      disabled={Boolean(a.pending)}
                      hideRetry
                      note="提交后由服务端判题，答案不下发前端"
                      onSubmit={(text, extra) => submit(q, text, extra)}
                    />
                  </div>
                );
              })}

              {!finished && (
                <div className="run-foot">
                  一次可以练多道题：每题独立提交、独立判题；全部答完会给出本组正确率。
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
