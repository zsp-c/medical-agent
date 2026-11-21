// 图标集合：统一 24×24 viewBox、currentColor 描边，尺寸由 size 控制
const base = (size, extra = {}) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.9,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  ...extra,
});

export function IconPlus({ size = 12, strokeWidth = 2.4 }) {
  return (
    <svg {...base(size, { strokeWidth })}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function IconChart({ size = 14 }) {
  return (
    <svg {...base(size)}>
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </svg>
  );
}

export function IconGear({ size = 14 }) {
  return (
    <svg {...base(size)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1A1.7 1.7 0 008 19.4a1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H2a2 2 0 110-4h.1A1.7 1.7 0 004.6 8a1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1A1.7 1.7 0 009 3.6a1.7 1.7 0 001-1.5V2a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9v.1a1.7 1.7 0 001.5 1H22a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" />
    </svg>
  );
}

export function IconSearch({ size = 15 }) {
  return (
    <svg {...base(size)}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </svg>
  );
}

export function IconQuiz({ size = 15 }) {
  return (
    <svg {...base(size)}>
      <path d="M9 11l2 2 4-4" />
      <rect x="3" y="4" width="18" height="16" rx="2" />
    </svg>
  );
}

export function IconDoc({ size = 15 }) {
  return (
    <svg {...base(size)}>
      <path d="M14 3v5h5" />
      <path d="M6 3h9l4 4v14H6z" />
    </svg>
  );
}

export function IconUpload({ size = 26 }) {
  return (
    <svg {...base(size, { strokeWidth: 1.7 })}>
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <path d="M7 9l5-5 5 5M12 4v12" />
    </svg>
  );
}

export function IconPaperclip({ size = 16 }) {
  return (
    <svg {...base(size)}>
      <path d="M21 12.5V19a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h6.5" />
      <path d="M18 2v6M15 5h6" />
    </svg>
  );
}

export function IconSend({ size = 14 }) {
  return (
    <svg {...base(size, { strokeWidth: 2.2 })}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function IconStop({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </svg>
  );
}

export function IconCopy({ size = 13 }) {
  return (
    <svg {...base(size)}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 012-2h10" />
    </svg>
  );
}

export function IconRefresh({ size = 13 }) {
  return (
    <svg {...base(size)}>
      <path d="M3 12a9 9 0 0115-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 01-15 6.7L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

export function IconLike({ size = 13 }) {
  return (
    <svg {...base(size)}>
      <path d="M7 11v9H4a1 1 0 01-1-1v-7a1 1 0 011-1h3z" />
      <path d="M7 11l4-8a2 2 0 012 2v4h6a2 2 0 012 2.2l-1.2 7A2 2 0 0117.8 20H7" />
    </svg>
  );
}

export function IconClose({ size = 12 }) {
  return (
    <svg {...base(size, { strokeWidth: 2.2 })}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export function IconCheck({ size = 12 }) {
  return (
    <svg {...base(size, { strokeWidth: 2.2 })}>
      <path d="M4 12.5l5 5L20 6.5" />
    </svg>
  );
}

export function IconShield({ size = 14 }) {
  return (
    <svg {...base(size)}>
      <path d="M12 3l7 3v6c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-3z" />
    </svg>
  );
}

export function IconInfo({ size = 14 }) {
  return (
    <svg {...base(size)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8h.01M11 12h1v4h1" />
    </svg>
  );
}

export function IconAlert({ size = 13 }) {
  return (
    <svg {...base(size, { strokeWidth: 2 })}>
      <path d="M12 8v5M12 17h.01" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

export function IconLayers({ size = 11 }) {
  return (
    <svg {...base(size, { strokeWidth: 2 })}>
      <path d="M12 3l9 5-9 5-9-5 9-5z" />
      <path d="M3 13l9 5 9-5" />
    </svg>
  );
}

export function IconHeart({ size = 22 }) {
  return (
    <svg {...base(size, { strokeWidth: 1.8 })}>
      <path d="M20.8 6.6a5.5 5.5 0 00-7.8 0L12 7.6l-1-1a5.5 5.5 0 10-7.8 7.8l8.8 8.8 8.8-8.8a5.5 5.5 0 000-7.8z" />
    </svg>
  );
}

export function IconChevron({ size = 14 }) {
  return (
    <svg {...base(size, { strokeWidth: 2 })}>
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}

export function IconLogo({ size = 17 }) {
  return (
    <svg {...base(size, { strokeWidth: 2 })}>
      <path d="M12 3v6M9 6h6" />
      <circle cx="12" cy="16" r="5" />
    </svg>
  );
}

// 实体类型图标（图谱详情页左上角）
export function IconEntity({ type, size = 22 }) {
  if (type === 'drug') {
    return (
      <svg {...base(size, { strokeWidth: 1.8 })}>
        <rect x="3" y="9" width="18" height="6" rx="3" />
        <path d="M12 9v6" />
      </svg>
    );
  }
  if (type === 'food') {
    return (
      <svg {...base(size, { strokeWidth: 1.8 })}>
        <path d="M6 3v8a3 3 0 006 0V3M9 11v10" />
        <path d="M17 3c-1.5 2-1.5 4 0 6v12" />
      </svg>
    );
  }
  if (type === 'department') {
    return (
      <svg {...base(size, { strokeWidth: 1.8 })}>
        <path d="M4 21V8l8-4 8 4v13" />
        <path d="M12 11v6M9 14h6" />
      </svg>
    );
  }
  if (type === 'symptom') {
    return (
      <svg {...base(size, { strokeWidth: 1.8 })}>
        <path d="M3 12h4l2-5 3 10 2-5h7" />
      </svg>
    );
  }
  if (type === 'treatment') {
    return (
      <svg {...base(size, { strokeWidth: 1.8 })}>
        <path d="M12 3v18M3 12h18" />
      </svg>
    );
  }
  if (type === 'producer') {
    return (
      <svg {...base(size, { strokeWidth: 1.8 })}>
        <path d="M3 21h18M5 21V9l7-5 7 5v12" />
        <path d="M9 21v-6h6v6" />
      </svg>
    );
  }
  return <IconHeart size={size} />;
}
