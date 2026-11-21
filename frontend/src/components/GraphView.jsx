import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { IconEntity, IconInfo } from './Icons';
import {
  NEUTRAL_COLOR,
  entityName,
  entityTypeOf,
  relationLabel,
  typeColor,
  typeLabel,
} from '../utils';

const W = 620;
const H = 300;
const CX = W / 2;
const CY = H / 2;

// 一跳邻居按椭圆均匀铺开：节点少时保持呼吸感，节点多时自动收缩半径
function layout(nodes) {
  const list = nodes.slice(0, 12);
  const rx = 240;
  const ry = 112;
  const n = list.length || 1;
  return list.map((node, i) => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
    return {
      ...node,
      x: CX + rx * Math.cos(angle),
      y: CY + ry * Math.sin(angle),
      r: 22,
      angle,
    };
  });
}

function GraphCanvas({ center, neighbors, edges, onOpenGraph }) {
  const placed = useMemo(() => layout(neighbors), [neighbors]);
  const posMap = useMemo(() => {
    const m = { [center.key]: { x: CX, y: CY } };
    for (const p of placed) m[p.key] = { x: p.x, y: p.y };
    return m;
  }, [center.key, placed]);

  const lines = edges
    .filter((e) => posMap[e.from] && posMap[e.to])
    .slice(0, 40);

  return (
    <div className="graphbox">
      <svg width="100%" height="100%" viewBox={`0 0 ${W} ${H}`}>
        <g stroke="#C9DDE4" strokeWidth="1.2">
          {lines.map((e, i) => (
            <line
              key={i}
              x1={posMap[e.from].x}
              y1={posMap[e.from].y}
              x2={posMap[e.to].x}
              y2={posMap[e.to].y}
            />
          ))}
        </g>

        <g fontFamily="Inter, PingFang SC, sans-serif" fontSize="10.5">
          {lines.slice(0, 8).map((e, i) => {
            const a = posMap[e.from];
            const b = posMap[e.to];
            return (
              <text
                key={`l${i}`}
                x={(a.x + b.x) / 2}
                y={(a.y + b.y) / 2 - 3}
                fill="#8A99A8"
                fontSize="9"
                textAnchor="middle"
              >
                {e.label}
              </text>
            );
          })}

          {placed.map((p) => {
            const c = typeColor(p.type);
            return (
              <g key={p.key} className="graph-node" onClick={() => onOpenGraph(p.key)}>
                <circle cx={p.x} cy={p.y} r={p.r} fill={c.bg} stroke={c.bd} />
                <text x={p.x} y={p.y + 4} fill={c.fg} textAnchor="middle" fontSize="10.5">
                  {p.name.length > 5 ? `${p.name.slice(0, 5)}…` : p.name}
                </text>
              </g>
            );
          })}

          <g className="graph-node" onClick={() => onOpenGraph(center.key)}>
            <circle cx={CX} cy={CY} r="32" fill="#0E7C86" stroke="#0B6A73" />
            <text x={CX} y={CY + 4} fill="#fff" textAnchor="middle" fontSize="11.5" fontWeight="600">
              {center.name.length > 5 ? `${center.name.slice(0, 5)}…` : center.name}
            </text>
          </g>
        </g>
      </svg>
      <div className="legend-tip">节点按实体类型着色 · 边按关系类型标注 · 点击邻居跳转</div>
    </div>
  );
}

export default function GraphView({ token, entityKey, onBack, onOpenGraph, meta }) {
  const [entity, setEntity] = useState(null);
  const [relations, setRelations] = useState(null);
  const [subgraph, setSubgraph] = useState(null);
  const [tab, setTab] = useState('relations');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [detail, rel, sub] = await Promise.all([
        api.entity(token, entityKey),
        api.relations(token, entityKey, { direction: 'both', limit: 50 }),
        api.subgraph(token, entityKey, { depth: 1, limit: 50 }),
      ]);
      setEntity(detail);
      setRelations(rel);
      setSubgraph(sub);
    } catch (e) {
      setError(e.message || '图谱查询失败');
    } finally {
      setLoading(false);
    }
  }, [token, entityKey]);

  useEffect(() => {
    load();
    setTab('relations');
  }, [load]);

  const name = entity?.found ? entity.name_zh || entity.name : entityKey.split(':').pop();
  const type = entity?.entity_type || entityKey.split(':')[1] || '';
  const color = typeColor(type);

  const neighbors = useMemo(() => {
    if (!subgraph) return [];
    const selfKey = entity?.entity_key || entityKey;
    const seen = new Map();
    for (const edge of subgraph.edges || []) {
      const otherKey = edge.src_key === selfKey ? edge.dst_key : edge.src_key;
      const otherName = edge.src_key === selfKey ? edge.dst_name : edge.src_name;
      if (!otherKey || otherKey === selfKey || seen.has(otherKey)) continue;
      const node = (subgraph.nodes || []).find((n) => n.entity_key === otherKey);
      seen.set(otherKey, {
        key: otherKey,
        name: otherName || entityName(node) || otherKey.split(':').pop(),
        type: node?.entity_type || otherKey.split(':')[1] || '',
      });
    }
    return [...seen.values()];
  }, [subgraph, entity, entityKey]);

  const canvasEdges = useMemo(() => {
    if (!subgraph) return [];
    const selfKey = entity?.entity_key || entityKey;
    const seen = new Set();
    const out = [];
    for (const edge of subgraph.edges || []) {
      const other = edge.src_key === selfKey ? edge.dst_key : edge.src_key;
      if (!other || other === selfKey) continue;
      // 图谱每条关系正反各存一行，按「邻居 + 关系」去重后只画一条
      const sig = [other, edge.relation_code].sort().join('|');
      if (seen.has(sig)) continue;
      seen.add(sig);
      out.push({ from: selfKey, to: other, label: relationLabel(edge.relation_code) });
    }
    return out;
  }, [subgraph, entity, entityKey]);

  const relationRows = useMemo(() => {
    if (!relations) return [];
    const selfKey = entity?.entity_key || entityKey;
    const seen = new Set();
    const rows = [];
    for (const e of relations.edges || []) {
      const otherKey = e.src_key === selfKey ? e.dst_key : e.src_key;
      const otherName = e.src_key === selfKey ? e.dst_name : e.src_name;
      const sig = `${otherKey}|${e.relation_code}`;
      if (!otherKey || otherKey === selfKey || seen.has(sig)) continue;
      seen.add(sig);
      rows.push({
        key: otherKey,
        name: otherName || otherKey.split(':').pop(),
        type: e.dst_type || otherKey.split(':')[1] || '',
        relation: e.relation_code,
      });
    }
    return rows.slice(0, 60);
  }, [relations, entity, entityKey]);

  const counts = useMemo(() => {
    const raw = relations?.meta?.relation_counts || {};
    const rows = Object.entries(raw)
      .filter(([, v]) => Number(v) > 0)
      .map(([code, v]) => ({ code, label: relationLabel(code), count: Number(v) }))
      .sort((a, b) => b.count - a.count);
    const max = rows.length ? rows[0].count : 1;
    return { rows, max };
  }, [relations]);

  const truncated = subgraph?.truncated;

  function copyKey() {
    navigator.clipboard?.writeText(entity?.entity_key || entityKey);
  }

  return (
    <div className="page">
      <div className="phead">
        <div className="crumb">
          <span className="link" onClick={onBack}>
            对话
          </span>
          /<span>知识图谱</span>/<b>{name}</b>
        </div>
        <div style={{ flex: 1 }} />
        {type && <span className="tag teal">{typeLabel(type)}</span>}
        {entity?.found && <span className="tag">度数 {entity.degree ?? 0}</span>}
        {meta?.graph?.degraded ? (
          <span className="tag warn" title={meta.graph.reason || '当前使用内置样例数据'}>
            图谱降级 · 样例 {meta.graph.nodes || 0} 实体
          </span>
        ) : meta?.graph?.backend === 'neo4j' ? (
          <span className="tag ok">Neo4j 全量图谱</span>
        ) : null}
        <button className="btn-line" style={{ height: 28 }} onClick={copyKey}>
          复制实体键
        </button>
        <button className="btn-line" style={{ height: 28 }} onClick={onBack}>
          返回对话
        </button>
      </div>

      <div className="pbody">
        {loading ? (
          <div className="empty-state" style={{ maxWidth: 1080, margin: '0 auto' }}>
            <div className="t">正在读取图谱…</div>
            <div className="d">实体属性、一跳关系与子图并行加载</div>
          </div>
        ) : error ? (
          <div className="empty-state" style={{ maxWidth: 1080, margin: '0 auto' }}>
            <div className="t">图谱查询失败</div>
            <div className="d">{error}</div>
            <div className="btns">
              <button className="btn-line" onClick={load}>
                重试
              </button>
              <button className="btn-primary" onClick={onBack}>
                返回对话
              </button>
            </div>
          </div>
        ) : !entity?.found ? (
          <div className="empty-state" style={{ maxWidth: 1080, margin: '0 auto' }}>
            <div className="t">图谱里没有「{name}」这个实体</div>
            <div className="d">
              实体键：{entityKey}
              <br />
              该实体可能属于其他来源库，或名称与图谱记录不一致。
            </div>
            <div className="btns">
              <button className="btn-primary" onClick={onBack}>
                返回对话
              </button>
            </div>
          </div>
        ) : (
          <div className="wrap">
            <div>
              <div className="panel" style={{ marginBottom: 14 }}>
                <div className="pb entity-head">
                  <div className="icon" style={{ background: color.bg, color: color.fg }}>
                    <IconEntity type={type} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="name">{name}</div>
                    <div className="key">{entity.entity_key}</div>
                    <div className="meta-row">
                      <span className="tag">来源库 {entity.source_dataset || '—'}</span>
                      <span className="tag">source_id {entity.source_id || '—'}</span>
                      <span className="tag ok">一跳邻居 {neighbors.length} 个</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="panel">
                <div className="tabs">
                  <button
                    className={`tab${tab === 'relations' ? ' on' : ''}`}
                    onClick={() => setTab('relations')}
                  >
                    关系<span className="c">{relationRows.length}</span>
                  </button>
                  <button
                    className={`tab${tab === 'attrs' ? ' on' : ''}`}
                    onClick={() => setTab('attrs')}
                  >
                    属性<span className="c">6</span>
                  </button>
                  <button
                    className={`tab${tab === 'source' ? ' on' : ''}`}
                    onClick={() => setTab('source')}
                  >
                    出处<span className="c">1</span>
                  </button>
                </div>

                <div className="pb" style={{ padding: '4px 14px 10px' }}>
                  {tab === 'relations' && (
                    <>
                      <div style={{ fontSize: 10.5, color: 'var(--mute)', padding: '9px 4px 3px' }}>
                        一跳关系（点实体名继续下钻）
                      </div>
                      {!relationRows.length && (
                        <div style={{ fontSize: 11.5, color: 'var(--mute)', padding: '8px 4px' }}>
                          该实体没有可展示的关系记录。
                        </div>
                      )}
                      {relationRows.map((r) => (
                        <button key={`${r.key}-${r.relation}`} className="rel" onClick={() => onOpenGraph(r.key)}>
                          <span className="rc">{relationLabel(r.relation)}</span>
                          <span className="nm">{r.name}</span>
                          <span className="go">{typeLabel(r.type)} · ›</span>
                        </button>
                      ))}
                    </>
                  )}

                  {tab === 'attrs' && (
                    <div className="kv" style={{ padding: '10px 0' }}>
                      <div className="k">实体键</div>
                      <div className="v">{entity.entity_key}</div>
                      <div className="k">实体类型</div>
                      <div className="v">{typeLabel(type)}（{type}）</div>
                      <div className="k">名称</div>
                      <div className="v">{entity.name || '—'}</div>
                      <div className="k">中文名</div>
                      <div className="v">{entity.name_zh || '—'}</div>
                      <div className="k">来源 ID</div>
                      <div className="v">{entity.source_id || '—'}</div>
                      <div className="k">度数</div>
                      <div className="v">{entity.degree ?? 0}</div>
                    </div>
                  )}

                  {tab === 'source' && (
                    <div className="kv" style={{ padding: '10px 0' }}>
                      <div className="k">来源数据集</div>
                      <div className="v">{entity.source_dataset || '—'}</div>
                      <div className="k">后台</div>
                      <div className="v">{entity.backend || '—'}</div>
                      <div className="k">描述</div>
                      <div className="v" style={{ fontFamily: 'inherit' }}>
                        {entity.description || '图谱未提供描述字段'}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="panel" style={{ marginTop: 14 }}>
                <div className="ph">
                  一跳子图 <span className="sp" />
                  <span className="hint">
                    depth={subgraph?.meta?.depth ?? 1} · limit={subgraph?.meta?.limit ?? 50} ·
                    truncated={String(Boolean(truncated))}
                  </span>
                </div>
                <div className="pb">
                  {neighbors.length ? (
                    <GraphCanvas
                      center={{ key: entity.entity_key, name }}
                      neighbors={neighbors}
                      edges={canvasEdges}
                      onOpenGraph={onOpenGraph}
                    />
                  ) : (
                    <div className="graphbox" style={{ display: 'grid', placeItems: 'center' }}>
                      <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>该实体暂无子图</span>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div>
              <div className="panel" style={{ marginBottom: 14 }}>
                <div className="ph">实体属性</div>
                <div className="pb">
                  <div className="kv">
                    <div className="k">实体键</div>
                    <div className="v">{entity.entity_key}</div>
                    <div className="k">实体类型</div>
                    <div className="v">{typeLabel(type)}</div>
                    <div className="k">名称</div>
                    <div className="v">{entity.name || '—'}</div>
                    <div className="k">来源 ID</div>
                    <div className="v">{entity.source_id || '—'}</div>
                    <div className="k">来源数据集</div>
                    <div className="v">{entity.source_dataset || '—'}</div>
                    <div className="k">度数</div>
                    <div className="v">{entity.degree ?? 0}</div>
                  </div>
                </div>
              </div>

              <div className="panel" style={{ marginBottom: 14 }}>
                <div className="ph">
                  关系类型分布 <span className="sp" />
                  <span className="hint">不受 limit 截断</span>
                </div>
                <div className="pb" style={{ padding: '12px 14px' }}>
                  {counts.rows.length ? (
                    <div className="bars">
                      {counts.rows.map((r) => (
                        <div className="bar" key={r.code}>
                          <span className="n">{r.label}</span>
                          <span className="track">
                            <span
                              className="fill"
                              style={{ width: `${Math.max(6, Math.round((r.count / counts.max) * 100))}%` }}
                            />
                          </span>
                          <span className="v">{r.count}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>暂无关系分布</div>
                  )}
                </div>
              </div>

              <div className="panel">
                <div className="ph">数据使用提示</div>
                <div className="pb" style={{ fontSize: 11.5, lineHeight: 1.85, color: 'var(--sub)' }}>
                  <div className="rule">
                    <b>关联 ≠ 因果。</b>图中「推荐用药」是文献与指南的关联记录，不是处方；多跳路径只表示「可达」。
                  </div>
                  <div className="rule neutral">
                    每条关系正反各存一行，<b>方向无语义</b>，不可据此推断因果。
                  </div>
                  <div style={{ fontSize: 10.5, color: 'var(--mute)', marginTop: 6, display: 'flex', gap: 6 }}>
                    <IconInfo size={12} />
                    图谱不含副作用 / 禁忌 / 相互作用类数据，这类问题以模型知识说明并提示遵医嘱。
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
