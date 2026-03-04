import { useState, useRef } from 'react'

// Mock 최근 기록 데이터
const recentRecords = [
  {
    id: 1,
    date: '03-01',
    type: '영화',
    typeColor: 'text-violet-400 bg-violet-400/10 border-violet-400/30',
    content: '레전드 - 톰 하디 주연 느와르 영화. 크레이 형제 실화 기반.',
  },
  {
    id: 2,
    date: '02-28',
    type: '아이디어',
    typeColor: 'text-amber-400 bg-amber-400/10 border-amber-400/30',
    content: 'JARVIS 업무 정의 UI 개선 아이디어 - 드래그앤드롭으로 순서 변경',
  },
  {
    id: 3,
    date: '02-27',
    type: '회의',
    typeColor: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
    content: '캡스톤 팀 킥오프 - 역할 분담 완료',
  },
]

// 오늘 날짜 포맷 (KST 기준)
function getTodayLabel() {
  const now = new Date()
  const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000)
  const days = ['일요일', '월요일', '화요일', '수요일', '목요일', '금요일', '토요일']
  const month = kst.getUTCMonth() + 1
  const date = kst.getUTCDate()
  const day = days[kst.getUTCDay()]
  return `${month}월 ${date}일 ${day}`
}

// 시간 인사
function getGreeting() {
  const now = new Date()
  const kstHour = (now.getUTCHours() + 9) % 24
  if (kstHour < 6) return '늦은 밤입니다'
  if (kstHour < 12) return '좋은 아침입니다'
  if (kstHour < 18) return '좋은 오후입니다'
  return '좋은 저녁입니다'
}

export default function Home() {
  const [input, setInput] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [lastResult, setLastResult] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSubmit = async () => {
    const trimmed = input.trim()
    if (!trimmed || isSubmitting) return

    setIsSubmitting(true)
    setLastResult(null)

    try {
      const res = await fetch('http://localhost:8000/api/v1/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: trimmed }),
      })
      if (res.ok) {
        const data = await res.json()
        setLastResult(data.message ?? '처리 완료')
      } else {
        setLastResult(`오류: ${res.status}`)
      }
    } catch {
      setLastResult('백엔드에 연결할 수 없습니다 (localhost:8000)')
    } finally {
      setIsSubmitting(false)
      setInput('')
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    // 자동 높이 조절
    const el = e.target
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }

  return (
    <div className="min-h-screen px-8 py-10 max-w-3xl mx-auto">
      {/* 상단 인사 */}
      <div className="mb-10">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-1 h-4 bg-blue-400 rounded-full" />
          <span className="text-white/40 text-xs font-mono tracking-widest uppercase">
            {getTodayLabel()}
          </span>
        </div>
        <h1 className="text-2xl font-semibold text-white/90 tracking-tight">
          안녕하세요.{' '}
          <span className="text-white/40 font-normal">{getGreeting()}</span>
        </h1>
        <p className="text-white/30 text-sm mt-1">
          무슨 일이 있었는지 말씀해 주세요. JARVIS가 처리합니다.
        </p>
      </div>

      {/* 입력 영역 */}
      <div className="mb-10">
        <div className="relative bg-[#111118] border border-white/10 rounded-xl overflow-hidden focus-within:border-blue-500/50 transition-colors duration-200">
          {/* 상단 레이블 */}
          <div className="flex items-center gap-2 px-5 pt-4 pb-2 border-b border-white/5">
            <div className="w-1.5 h-1.5 rounded-full bg-blue-400/60" />
            <span className="text-white/25 text-[11px] font-mono tracking-wider uppercase">Input</span>
          </div>

          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder="무슨 일이 있었나요?"
            rows={4}
            className="w-full bg-transparent px-5 py-4 text-white/85 text-base placeholder-white/20 resize-none outline-none font-sans leading-relaxed"
          />

          {/* 하단 액션 바 */}
          <div className="flex items-center justify-between px-5 py-3 border-t border-white/5">
            <span className="text-white/20 text-xs font-mono">
              Ctrl+Enter 또는 버튼으로 전송
            </span>
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || isSubmitting}
              className="flex items-center gap-2 px-4 py-1.5 bg-blue-500 hover:bg-blue-400 disabled:bg-white/10 disabled:text-white/25 text-white text-sm font-medium rounded-lg transition-colors duration-150"
            >
              {isSubmitting ? (
                <>
                  <svg className="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  처리 중
                </>
              ) : (
                <>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <line x1="22" y1="2" x2="11" y2="13" />
                    <polygon points="22 2 15 22 11 13 2 9 22 2" />
                  </svg>
                  전송
                </>
              )}
            </button>
          </div>
        </div>

        {/* 처리 결과 메시지 */}
        {lastResult && (
          <div className="mt-3 flex items-start gap-2.5 px-4 py-3 bg-[#111118] border border-white/10 rounded-lg">
            <div className="w-1.5 h-1.5 mt-1.5 rounded-full bg-emerald-400 shrink-0" />
            <span className="text-white/60 text-sm">{lastResult}</span>
          </div>
        )}
      </div>

      {/* 최근 기록 */}
      <div>
        <div className="flex items-center gap-3 mb-4">
          <span className="text-white/50 text-xs font-mono tracking-widest uppercase">Recent</span>
          <div className="flex-1 h-px bg-white/5" />
        </div>

        <div className="flex flex-col gap-3">
          {recentRecords.map((record) => (
            <div
              key={record.id}
              className="group flex gap-4 bg-[#111118] border border-white/8 hover:border-white/15 rounded-xl px-5 py-4 transition-colors duration-150 cursor-default"
            >
              {/* 날짜 */}
              <div className="shrink-0 text-white/25 text-xs font-mono pt-0.5 w-10">
                {record.date}
              </div>

              {/* 내용 */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1.5">
                  <span
                    className={`inline-flex px-2 py-0.5 rounded text-[11px] font-medium border ${record.typeColor}`}
                  >
                    {record.type}
                  </span>
                </div>
                <p className="text-white/60 text-sm leading-relaxed truncate">{record.content}</p>
              </div>

              {/* 화살표 */}
              <div className="shrink-0 text-white/15 group-hover:text-white/30 pt-1 transition-colors">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
