import { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';

// 实体类型配色（低饱和医疗风）；未收录类型用中性灰
const TYPE_COLORS = {
  disease: '#e0655f',
  drug: '#4f9cf0',
  symptom: '#e0a44f',
  phenotype: '#8f6fd8',
  gene: '#4fbf8f',
  protein: '#4fbf8f',
  pathway: '#d06fa8',
  anatomy: '#5fb8c9',
};
const FALLBACK = '#98a0ab';

export default function GraphView({ graph, detail }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  useEffect(() => {
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      wheelSensitivity: 0.2,
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'font-size': 11,
            color: '#333a45',
            'text-valign': 'bottom',
            'text-margin-y': 4,
            'text-wrap': 'wrap',
            'text-max-width': 90,
            'background-color': FALLBACK,
            width: 24,
            height: 24,
            'border-width': 1.5,
            'border-color': '#ffffff',
          },
        },
        ...Object.entries(TYPE_COLORS).map(([t, c]) => ({
          selector: `node[type="${t}"]`,
          style: { 'background-color': c },
        })),
        {
          selector: 'node[center]',
          style: { width: 34, height: 34, 'border-width': 3, 'border-color': '#0fdc78' },
        },
        {
          selector: 'edge',
          style: {
            width: 1.2,
            'line-color': '#c2c8d0',
            'target-arrow-shape': 'triangle',
            'target-arrow-color': '#c2c8d0',
            'arrow-scale': 0.8,
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': 8.5,
            color: '#8a919c',
            'text-rotation': 'autorotate',
          },
        },
      ],
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, []);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().remove();
    if (!graph || !graph.nodes || !graph.nodes.length) return;
    const centerKey = graph.meta?.entity_key;
    const els = [];
    for (const n of graph.nodes) {
      els.push({
        data: {
          id: n.entity_key,
          label: n.name_zh || n.name,
          type: n.entity_type,
          center: n.entity_key === centerKey,
        },
      });
    }
    for (const e of graph.edges || []) {
      els.push({
        data: {
          id: `${e.src_key}|${e.relation_code}|${e.dst_key}`,
          source: e.src_key,
          target: e.dst_key,
          label: e.relation_display || e.relation_code,
        },
      });
    }
    cy.add(els);
    cy.layout({ name: 'cose', animate: false, padding: 40, nodeRepulsion: 8000 }).run();
    cy.fit(undefined, 40);
  }, [graph]);

  return (
    <div className="graph-box">
      {detail && detail.found && (
        <div className="entity-card">
          <div className="entity-title">
            {detail.name_zh || detail.name}
            <span className={`tag t-${detail.entity_type}`}>{detail.entity_type}</span>
          </div>
          {detail.name_zh && <div className="entity-name-en">{detail.name}</div>}
          {detail.description && <div className="entity-desc">{detail.description}</div>}
          <div className="entity-meta">
            关联度 {detail.degree} · {detail.entity_key}
          </div>
        </div>
      )}
      <div ref={containerRef} className="graph-canvas" />
      {graph?.truncated && <div className="graph-note">子图过大已截断（depth≤2, limit≤50）</div>}
    </div>
  );
}
