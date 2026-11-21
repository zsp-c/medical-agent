import { IconChart, IconGear, IconPlus, IconChevron } from './Icons';
import { dayLabel, groupSessions } from '../utils';

// 左侧栏：新建对话 / 答题统计 / 设置 + 会话分组列表 + 当前用户
export default function Sidebar({
  sessions,
  active,
  busy,
  username,
  userId,
  llm,
  onNew,
  onOpen,
  onDelete,
  onOpenPractice,
  onOpenSettings,
}) {
  const groups = groupSessions(sessions);
  const initials = (username || 'U').slice(0, 2).toUpperCase();

  return (
    <aside className="side">
      <div className="shead">
        <button className="btn-primary" style={{ flex: 1, height: 32 }} onClick={onNew}>
          <IconPlus />
          新建对话
        </button>
        <button
          className="ghost"
          style={{ width: 32, padding: 0, height: 32 }}
          title="答题统计"
          onClick={onOpenPractice}
        >
          <IconChart />
        </button>
        <button
          className="ghost"
          style={{ width: 32, padding: 0, height: 32 }}
          title="设置"
          onClick={onOpenSettings}
        >
          <IconGear />
        </button>
      </div>

      <div className="slist">
        {!groups.length && (
          <div className="empty-tip">
            还没有历史会话。
            <br />
            在右侧提问后会自动保存。
          </div>
        )}
        {groups.map((g) => (
          <div key={g.label}>
            <div className="grp">{g.label}</div>
            {g.items.map((s) => (
              <div
                key={s.session_id}
                className={`sitem${s.session_id === active ? ' on' : ''}`}
                onClick={() => onOpen(s.session_id)}
              >
                <div className="t">{s.title || '新对话'}</div>
                <div className="m">
                  <span>{busy && s.session_id === active ? '正在生成' : dayLabel(s.updated_at)}</span>
                  <span>·</span>
                  <span>{s.message_count || 0} 条消息</span>
                </div>
                <button
                  className="x"
                  title="删除会话"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(s.session_id);
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div className="suser" onClick={onOpenSettings} style={{ cursor: 'pointer' }}>
        <div className="avatar">{initials}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="n">{username}</div>
          <div className="e">个人用户 · {llm ? '自定义模型' : '默认模型'}</div>
        </div>
        <button className="more" title="设置">
          <IconChevron />
        </button>
      </div>
    </aside>
  );
}
