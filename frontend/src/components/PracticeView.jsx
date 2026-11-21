import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { fullTime, percent } from '../utils';

const RANGES = [
  { days: 30, label: '近 30 天' },
  { days: 90, label: '近 90 天' },
];

function TrendChart({ trend }) {
  const W = 620;
  const H = 196;
  const left = 38;
  const right = 10;
  const top = 14;
  const bottom = 24;
  const plotW = W - left - right;
  const plotH = H - top - bottom;
  const points = trend || [];
  const stepX = points.length > 1 ? plotW / (points.length - 1) : 0;
  const yOf = (v) => top + ((100 - v) / 100) * plotH;

  // 逐段绘制：空白周（无作答）断开折线，不臆造数据
  const segments = [];
  let current = [];
  points.forEach((p, i) => {
    if (p.accuracy === null || p.accuracy === undefined) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push({ x: left + i * stepX, y: yOf(p.accuracy), v: p.accuracy });
  });
  if (current.length) segments.push(current);

  return (
    <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>
      <g stroke="#EFF4F8" strokeWidth="1">
        {[100, 75, 50, 25, 0].map((v) => (
          <line key={v} x1={left} y1={yOf(v)} x2={W - right} y2={yOf(v)} />
        ))}
      </g>
      <g fontSize="9" fill="#8A99A8" fontFamily="monospace" textAnchor="end">
        {[100, 75, 50, 25, 0].map((v) => (
          <text key={v} x={left - 6} y={yOf(v) + 3}>
            {v}%
          </text>
        ))}
      </g>

      {segments.map((seg, i) =>
        seg.length > 1 ? (
          <polyline
            key={i}
            fill="none"
            stroke="#12A5A8"
            strokeWidth="2.4"
            strokeLinejoin="round"
            points={seg.map((p) => `${p.x},${p.y}`).join(' ')}
          />
        ) : null
      )}
      {segments.flat().map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r={3} fill="#12A5A8" />
      ))}

      <g fontSize="9" fill="#8A99A8" fontFamily="monospace" textAnchor="middle">
        {points.map((p, i) => (
          <text key={p.week_start} x={left + i * stepX} y={H - 8}>
            {p.label}
          </text>
        ))}
      </g>

      {!segments.length && (
        <text x={W / 2} y={H / 2} fontSize="11" fill="#8A99A8" textAnchor="middle">
          该区间还没有作答数据
        </text>
      )}
      {segments.length === 1 && segments[0].length === 1 && (
        <text x={W / 2} y={H / 2} fontSize="11" fill="#8A99A8" textAnchor="middle">
          只有一周数据，累计两周后才会连成趋势线
        </text>
      )}
    </svg>
  );
}

export default function PracticeView({ token, onBack, onRedo, onDrill, onAskPractice }) {
  const [days, setDays] = useState(30);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setStats(await api.practiceStats(token, days));
    } catch (e) {
      setError(e.message || '统计加载失败');
    } finally {
      setLoading(false);
    }
  }, [token, days]);

  useEffect(() => {
    load();
  }, [load]);

  const s = stats;
  const subjects = (s?.subjects || []).slice(0, 6);

  return (
    <div className="page">
      <div className="phead">
        <div className="crumb">
          <span className="link" onClick={onBack}>
            对话
          </span>
          /<span>练习中心</span>/<b>答题统计</b>
        </div>
        <div style={{ flex: 1 }} />
        <span className="tag">数据来自 {s?.total_answered ?? 0} 次作答</span>
        {RANGES.map((r) => (
          <button
            key={r.days}
            className="btn-line"
            style={{ height: 28, background: days === r.days ? 'var(--ink)' : undefined, color: days === r.days ? '#fff' : undefined }}
            onClick={() => setDays(r.days)}
          >
            {r.label}
          </button>
        ))}
        <button className="btn-line" style={{ height: 28 }} onClick={() => onDrill(5)}>
          随机 5 题
        </button>
        <button
          className="btn-primary"
          style={{ height: 28, fontSize: 11.5 }}
          onClick={onBack}
          title="回到对话页"
        >
          ← 返回对话
        </button>
      </div>

      <div className="pbody">
        <div style={{ maxWidth: 1080, margin: '0 auto' }}>
          {error && (
            <div className="empty-state">
              <div className="t">统计加载失败</div>
              <div className="d">{error}</div>
              <div className="btns">
                <button className="btn-line" onClick={load}>
                  重试
                </button>
              </div>
            </div>
          )}

          {!error && loading && !s && (
            <div className="empty-state">
              <div className="t">正在统计…</div>
              <div className="d">按作答记录聚合 KPI、趋势与学科正确率</div>
            </div>
          )}

          {!error && s && s.empty && (
            <div className="empty-state" style={{ marginBottom: 14 }}>
              <div className="t">还没有作答记录</div>
              <div className="d">
                在对话里说「帮我出一道题」，或直接从题库随机练一组；作答后这里会记录正确率、连续练习天数与薄弱学科。
              </div>
              <div className="btns">
                <button className="btn-primary" onClick={() => onDrill(5)}>
                  随机练 5 题
                </button>
                <button className="btn-line" onClick={() => onRedo({})}>
                  重练错题
                </button>
                <button className="btn-line" onClick={() => onAskPractice('')}>
                  让 AI 出新题
                </button>
                <button className="btn-line" onClick={onBack}>
                  返回对话
                </button>
              </div>
            </div>
          )}

          {s && (
            <>
              <div className="kpis">
                <div className="kpi">
                  <div className="l">累计作答</div>
                  <div className="v">
                    {s.total_answered}
                    <span className="unit"> 题</span>
                  </div>
                  <div className="d mute">
                    近 {s.window_days} 天 {s.window_answered} 题
                  </div>
                </div>
                <div className="kpi">
                  <div className="l">正确率</div>
                  <div className="v">
                    {percent(s.accuracy)}
                    {s.accuracy === null || s.accuracy === undefined ? null : (
                      <span className="unit">%</span>
                    )}
                  </div>
                  <div className="d">
                    近 {s.window_days} 天 {percent(s.window_accuracy)}
                    {s.window_accuracy === null || s.window_accuracy === undefined ? '' : '%'}
                  </div>
                </div>
                <div className="kpi">
                  <div className="l">连续练习</div>
                  <div className="v">
                    {s.streak_days}
                    <span className="unit"> 天</span>
                  </div>
                  <div className="d mute">最长纪录 {s.longest_streak} 天</div>
                </div>
                <div className="kpi">
                  <div className="l">累计错题</div>
                  <div className="v">
                    {s.wrong_total}
                    <span className="unit"> 题</span>
                  </div>
                  <div className={`d ${s.pending_review ? 'down' : 'mute'}`}>
                    待复习 {s.pending_review} 题
                  </div>
                </div>
              </div>

              <div className="grid-2">
                <div className="chart">
                  <div className="ch">
                    <span className="t">正确率趋势（近 8 周）</span>
                    <span className="sp" />
                    <span className="legend">
                      <span>
                        <i style={{ background: '#12A5A8' }} />
                        每周正确率
                      </span>
                    </span>
                  </div>
                  <TrendChart trend={s.trend} />
                </div>

                <div className="chart">
                  <div className="ch">
                    <span className="t">学科正确率</span>
                    <span className="sp" />
                    <span style={{ fontSize: 10, color: 'var(--mute)' }}>低于 60% 标为薄弱</span>
                  </div>
                  <div className="bars">
                    {subjects.map((x) => (
                      <div className="bar" key={x.subject}>
                        <span className="n" title={x.subject}>
                          {x.subject}
                        </span>
                        <span className="track">
                          <span
                            className="fill"
                            style={{
                              width: `${Math.max(2, Math.min(100, Math.round(x.accuracy)))}%`,
                              background: x.accuracy < 60 ? '#D9B26A' : undefined,
                            }}
                          />
                        </span>
                        <span className="v">{Math.round(x.accuracy)}%</span>
                      </div>
                    ))}
                    {!subjects.length && (
                      <div style={{ fontSize: 11.5, color: 'var(--mute)', padding: '8px 0' }}>
                        暂无已判定的作答
                      </div>
                    )}
                  </div>
                  <div
                    style={{
                      borderTop: '1px solid var(--line-2)',
                      marginTop: 6,
                      paddingTop: 9,
                      fontSize: 10.5,
                      color: 'var(--mute)',
                      lineHeight: 1.7,
                    }}
                  >
                    样本：
                    {subjects.map((x) => `${x.subject} ${x.answered}`).join(' · ') || '—'}
                  </div>
                </div>
              </div>

              <div className="grid-2 wide">
                <div className="chart" style={{ paddingBottom: 0 }}>
                  <div className="ch">
                    <span className="t">最近练习记录</span>
                    <span className="sp" />
                  </div>
                  <table className="tb" style={{ fontSize: 11.5 }}>
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>学科</th>
                        <th>题目</th>
                        <th>结果</th>
                        <th style={{ textAlign: 'right' }}>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(s.recent || []).map((r) => (
                        <tr key={r.record_id}>
                          <td className="mono">{fullTime(r.created_at)}</td>
                          <td>{r.subject}</td>
                          <td className="stem" title={r.stem}>
                            {r.stem}
                          </td>
                          <td>
                            {r.correct === true && <span className="tag ok">正确</span>}
                            {r.correct === false && <span className="tag bad">错误</span>}
                            {r.correct === null && <span className="tag">待判定</span>}
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            <span
                              className="mini"
                              onClick={() => onRedo({ quizIds: [r.quiz_id], limit: 1 })}
                            >
                              重练
                            </span>
                          </td>
                        </tr>
                      ))}
                      {!(s.recent || []).length && (
                        <tr>
                          <td colSpan={5} style={{ color: 'var(--mute)', textAlign: 'center', padding: 18 }}>
                            还没有作答记录
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="chart">
                  <div className="ch">
                    <span className="t">薄弱知识点</span>
                    <span className="sp" />
                    <span className="mini" onClick={() => onRedo({})}>
                      重练错题 ›
                    </span>
                  </div>
                  <div style={{ padding: '2px 0 4px' }}>
                    {(s.weak_points || []).map((w) => (
                      <div className="weak-row" key={w.subject}>
                        <span className="nm">{w.subject}</span>
                        <span className="cnt">
                          {w.wrong}/{w.answered} 错
                        </span>
                        <button className="link" onClick={() => onRedo({ subject: w.subject })}>
                          重练 ›
                        </button>
                      </div>
                    ))}
                    {!(s.weak_points || []).length && (
                      <div style={{ fontSize: 11.5, color: 'var(--mute)', lineHeight: 1.8, padding: '6px 0' }}>
                        暂无薄弱学科：低于 60% 且样本 ≥ 3 题的学科才会列在这里。
                      </div>
                    )}
                  </div>
                  <div className="rule" style={{ marginTop: 6, fontSize: 10.5 }}>
                    统计口径：仅统计<b>已提交</b>的作答；「换一题」丢弃的题目不计入；
                    同一张题卡重复提交由 quiz_id 幂等拦截；自拟问答题无标准答案，不参与正确率；
                    <b>重练</b>会生成新题卡，重新计入一次作答。
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
