import { NavLink } from 'react-router-dom'

const navItems = [
  {
    to: '/',
    label: '홈',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        <polyline points="9 22 9 12 15 12 15 22" />
      </svg>
    ),
  },
  {
    to: '/workflows',
    label: '업무 관리',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="7" />
        <rect x="14" y="3" width="7" height="7" />
        <rect x="14" y="14" width="7" height="7" />
        <rect x="3" y="14" width="7" height="7" />
      </svg>
    ),
  },
  {
    to: '/search',
    label: '검색',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
    ),
  },
]

export default function Sidebar() {
  return (
    <aside className="w-56 min-h-screen bg-[#0d0d14] border-r border-white/10 flex flex-col shrink-0">
      {/* 로고 영역 */}
      <div className="px-6 py-6 border-b border-white/10">
        <div className="flex items-center gap-2.5">
          {/* 로고 아이콘 */}
          <div className="relative w-7 h-7">
            <div className="absolute inset-0 rounded-sm bg-blue-500/20 border border-blue-500/50" />
            <div className="absolute inset-[3px] rounded-sm bg-blue-500/30 border border-blue-400/60" />
            <div className="absolute inset-[6px] rounded-sm bg-blue-400/60" />
          </div>
          <div>
            <div className="text-white font-bold text-sm tracking-[0.15em] font-mono">JARVIS</div>
            <div className="text-white/30 text-[10px] tracking-widest font-mono">AI ASSISTANT</div>
          </div>
        </div>
      </div>

      {/* 네비게이션 */}
      <nav className="flex-1 px-3 py-4 flex flex-col gap-1">
        <div className="text-white/25 text-[10px] font-mono tracking-widest px-3 mb-2 uppercase">Navigation</div>
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              [
                'flex items-center gap-3 px-3 py-2.5 rounded text-sm transition-all duration-150 group',
                isActive
                  ? 'bg-blue-500/15 text-blue-400 border border-blue-500/30'
                  : 'text-white/40 hover:text-white/80 hover:bg-white/5 border border-transparent',
              ].join(' ')
            }
          >
            {({ isActive }) => (
              <>
                <span className={isActive ? 'text-blue-400' : 'text-white/30 group-hover:text-white/60'}>
                  {item.icon}
                </span>
                <span className="font-medium">{item.label}</span>
                {isActive && (
                  <span className="ml-auto w-1 h-1 rounded-full bg-blue-400" />
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* 하단 상태 표시 */}
      <div className="px-6 py-4 border-t border-white/10">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-white/30 text-[11px] font-mono">SYSTEM ONLINE</span>
        </div>
        <div className="text-white/15 text-[10px] font-mono mt-1">v0.1.0-alpha</div>
      </div>
    </aside>
  )
}
