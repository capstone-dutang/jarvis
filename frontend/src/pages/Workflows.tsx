import { useState } from 'react'

type TriggerType = 'manual' | 'scheduled' | 'event'

interface Workflow {
  id: number
  name: string
  trigger: TriggerType
  description: string
  active: boolean
}

const triggerConfig: Record<TriggerType, { label: string; color: string }> = {
  manual: {
    label: 'manual',
    color: 'text-blue-400 bg-blue-400/10 border-blue-400/30',
  },
  scheduled: {
    label: 'scheduled',
    color: 'text-amber-400 bg-amber-400/10 border-amber-400/30',
  },
  event: {
    label: 'event',
    color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  },
}

const initialWorkflows: Workflow[] = [
  {
    id: 1,
    name: '일기 글감 수집',
    trigger: 'manual',
    description: '짧은 입력을 글감으로 저장. AI 확장 없음.',
    active: true,
  },
  {
    id: 2,
    name: '비교과 프로그램 알림',
    trigger: 'event',
    description: '6시간마다 크롤링 → 새 프로그램 감지 시 알림',
    active: true,
  },
  {
    id: 3,
    name: '일일 요약 일기',
    trigger: 'scheduled',
    description: '매일 21:00 오늘 글감 → 3줄 요약 일기 생성',
    active: false,
  },
]

function Toggle({ active, onToggle }: { active: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      className={[
        'relative w-9 h-5 rounded-full transition-colors duration-200 shrink-0',
        active ? 'bg-blue-500' : 'bg-white/15',
      ].join(' ')}
      aria-label={active ? '비활성화' : '활성화'}
    >
      <span
        className={[
          'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200',
          active ? 'translate-x-4' : 'translate-x-0',
        ].join(' ')}
      />
    </button>
  )
}

function AddWorkflowModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 배경 오버레이 */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* 모달 본체 */}
      <div className="relative w-full max-w-md mx-4 bg-[#111118] border border-white/15 rounded-2xl overflow-hidden">
        {/* 헤더 */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-white/10">
          <div>
            <h2 className="text-white/90 font-semibold text-base">새 업무 추가</h2>
            <p className="text-white/30 text-xs mt-0.5 font-mono">새로운 자동화 업무를 정의합니다</p>
          </div>
          <button
            onClick={onClose}
            className="text-white/30 hover:text-white/70 transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* 폼 */}
        <div className="px-6 py-5 flex flex-col gap-4">
          <div>
            <label className="block text-white/40 text-xs font-mono mb-1.5 uppercase tracking-wider">업무명</label>
            <input
              type="text"
              placeholder="예: 독서 기록 수집"
              className="w-full bg-[#0a0a0f] border border-white/10 rounded-lg px-4 py-2.5 text-white/80 text-sm placeholder-white/20 outline-none focus:border-blue-500/50 transition-colors"
            />
          </div>
          <div>
            <label className="block text-white/40 text-xs font-mono mb-1.5 uppercase tracking-wider">트리거 타입</label>
            <select className="w-full bg-[#0a0a0f] border border-white/10 rounded-lg px-4 py-2.5 text-white/80 text-sm outline-none focus:border-blue-500/50 transition-colors">
              <option value="manual">manual — 수동 입력</option>
              <option value="scheduled">scheduled — 주기 실행</option>
              <option value="event">event — 이벤트 감지</option>
            </select>
          </div>
          <div>
            <label className="block text-white/40 text-xs font-mono mb-1.5 uppercase tracking-wider">설명</label>
            <textarea
              placeholder="업무 동작을 구체적으로 설명하세요"
              rows={3}
              className="w-full bg-[#0a0a0f] border border-white/10 rounded-lg px-4 py-2.5 text-white/80 text-sm placeholder-white/20 outline-none focus:border-blue-500/50 resize-none transition-colors"
            />
          </div>
        </div>

        {/* 하단 버튼 */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-white/10">
          <button
            onClick={onClose}
            className="px-4 py-2 text-white/40 hover:text-white/70 text-sm transition-colors"
          >
            취소
          </button>
          <button
            onClick={onClose}
            className="px-5 py-2 bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium rounded-lg transition-colors"
          >
            추가
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Workflows() {
  const [workflows, setWorkflows] = useState<Workflow[]>(initialWorkflows)
  const [showModal, setShowModal] = useState(false)

  const toggleActive = (id: number) => {
    setWorkflows((prev) =>
      prev.map((w) => (w.id === id ? { ...w, active: !w.active } : w))
    )
  }

  const activeCount = workflows.filter((w) => w.active).length

  return (
    <div className="min-h-screen px-8 py-10 max-w-3xl mx-auto">
      {/* 헤더 */}
      <div className="flex items-start justify-between mb-10">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="w-1 h-4 bg-blue-400 rounded-full" />
            <span className="text-white/40 text-xs font-mono tracking-widest uppercase">Workflows</span>
          </div>
          <h1 className="text-2xl font-semibold text-white/90 tracking-tight">업무 목록</h1>
          <p className="text-white/30 text-sm mt-1">
            {activeCount}/{workflows.length}개 활성화
          </p>
        </div>

        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium rounded-lg transition-colors duration-150"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          새 업무 추가
        </button>
      </div>

      {/* 업무 목록 */}
      <div className="flex flex-col gap-3">
        {workflows.map((workflow) => {
          const trigger = triggerConfig[workflow.trigger]
          return (
            <div
              key={workflow.id}
              className={[
                'bg-[#111118] border rounded-xl px-6 py-5 transition-colors duration-150',
                workflow.active ? 'border-white/10' : 'border-white/5 opacity-60',
              ].join(' ')}
            >
              <div className="flex items-start justify-between gap-4">
                {/* 왼쪽 내용 */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2.5 mb-2">
                    <h3 className="text-white/90 font-medium text-base">{workflow.name}</h3>
                    <span
                      className={`inline-flex px-2 py-0.5 rounded text-[11px] font-mono border ${trigger.color}`}
                    >
                      {trigger.label}
                    </span>
                  </div>
                  <p className="text-white/40 text-sm leading-relaxed">{workflow.description}</p>
                </div>

                {/* 토글 */}
                <div className="flex items-center gap-3 shrink-0 pt-0.5">
                  <span className="text-white/25 text-xs font-mono">
                    {workflow.active ? 'ON' : 'OFF'}
                  </span>
                  <Toggle active={workflow.active} onToggle={() => toggleActive(workflow.id)} />
                </div>
              </div>

              {/* 하단 메타 */}
              <div className="flex items-center gap-4 mt-4 pt-4 border-t border-white/5">
                <div className="flex items-center gap-1.5">
                  <div
                    className={`w-1.5 h-1.5 rounded-full ${workflow.active ? 'bg-emerald-400' : 'bg-white/20'}`}
                  />
                  <span className="text-white/25 text-xs font-mono">
                    {workflow.active ? 'ACTIVE' : 'INACTIVE'}
                  </span>
                </div>
                <span className="text-white/15 text-xs">ID: {String(workflow.id).padStart(3, '0')}</span>
              </div>
            </div>
          )
        })}
      </div>

      {/* 모달 */}
      {showModal && <AddWorkflowModal onClose={() => setShowModal(false)} />}
    </div>
  )
}
