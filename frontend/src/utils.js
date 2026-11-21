// 展示层通用小工具：实体类型/关系码中文化、时间格式化、会话分组

export const TYPE_LABELS = {
  disease: '疾病',
  symptom: '症状',
  drug: '药物',
  food: '食物',
  department: '科室',
  producer: '生产商',
  treatment: '治疗手段',
};

export const TYPE_COLORS = {
  disease: { bg: '#FDEFF0', fg: '#C0392B', bd: '#F3C9CD' },
  symptom: { bg: '#FEF3E7', fg: '#A96508', bd: '#F0CFA6' },
  drug: { bg: '#EAF3FE', fg: '#1D5FBF', bd: '#C6DAF8' },
  food: { bg: '#EBF7EF', fg: '#0F8A5F', bd: '#C4E4D1' },
  department: { bg: '#F1EDFD', fg: '#6D4AE0', bd: '#DCD2F8' },
  treatment: { bg: '#E6F6EF', fg: '#0F8A5F', bd: '#CDEBDD' },
  producer: { bg: '#F4F6F9', fg: '#5B6B7C', bd: '#DCE4EC' },
};

export const NEUTRAL_COLOR = { bg: '#F4F6F9', fg: '#5B6B7C', bd: '#DCE4EC' };

export const RELATION_LABELS = {
  disease_has_symptom: '疾病-症状',
  disease_acompany_disease: '并发症',
  disease_need_check: '需要检查',
  disease_need_treatment: '治疗手段',
  disease_belong_department: '所属科室',
  disease_recommand_drug: '推荐药物',
  disease_common_drug: '一般用药',
  disease_recommand_food: '推荐食物',
  disease_eat_food: '宜吃食物',
  disease_noteat_food: '忌吃食物',
  drug_relate_producer: '生产商',
  department_belong_department: '科室层级',
  SameAs: '同义词',
};

// 工具名 → 中文步骤描述（流式检索进度条用）
export const TOOL_STEPS = {
  search_entity: '检索医疗知识图谱',
  get_entity_details: '读取实体属性',
  query_relations: '查询实体关系',
  find_relation_path: '寻找关联路径',
  get_subgraph: '提取局部子图',
  search_questions: '检索医学题库',
  present_quiz: '生成互动题卡',
  read_file: '查阅技能手册',
};

export function relationLabel(code) {
  if (!code) return '关联';
  return RELATION_LABELS[code] || code;
}

export function entityName(brief) {
  if (!brief) return '';
  return brief.name_zh || brief.name || (brief.entity_key || '').split(':').pop() || '';
}

export function entityTypeOf(brief) {
  if (!brief) return '';
  return brief.entity_type || (brief.entity_key || '').split(':')[1] || '';
}

export function typeLabel(type) {
  return TYPE_LABELS[type] || '实体';
}

export function typeColor(type) {
  return TYPE_COLORS[type] || NEUTRAL_COLOR;
}

function toDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

const pad = (n) => String(n).padStart(2, '0');

export function clockOf(iso) {
  const d = toDate(iso);
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
}

export function fullTime(iso) {
  const d = toDate(iso);
  if (!d) return '';
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}`;
}

export function dayLabel(iso) {
  const d = toDate(iso);
  if (!d) return '';
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  const day = 86400000;
  if (t >= start) return '今天';
  if (t >= start - day) return '昨天';
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// 侧栏会话按时间分组（今天 / 近 7 天 / 更早）
export function groupSessions(sessions) {
  const groups = [
    { label: '今天', items: [] },
    { label: '近 7 天', items: [] },
    { label: '更早', items: [] },
  ];
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  for (const s of sessions) {
    const d = toDate(s.updated_at);
    const t = d ? d.getTime() : 0;
    if (t >= start) groups[0].items.push(s);
    else if (t >= start - 7 * 86400000) groups[1].items.push(s);
    else groups[2].items.push(s);
  }
  return groups.filter((g) => g.items.length);
}

export function formatNumber(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString('en-US');
}

export function percent(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value).toFixed(1)}`;
}

// 从用户作答文本里取回选择项字母（历史题卡回放时定位"你选了这项"）
export function choiceLetter(text) {
  const m = /我选\s*([A-H])/i.exec(text || '');
  if (m) return m[1].toUpperCase();
  const head = (text || '').trim()[0];
  return head && /[A-Ha-h]/.test(head) ? head.toUpperCase() : '';
}
