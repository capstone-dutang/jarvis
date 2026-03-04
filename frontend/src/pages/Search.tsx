import { useState, useRef } from 'react'

interface SearchResult {
  id: number
  date: string
  type: string
  typeColor: string
  content: string
  score: number
}

// Mock 검색 결과 — 실제 백엔드 연동 전 시뮬레이션용
const allRecords: SearchResult[] = [
  {
    id: 1,
    date: '03-01',
    type: '영화',
    typeColor: 'text-violet-400 bg-violet-400/10 border-violet-400/30',
    content: '레전드 - 톰 하디 주연 느와르 영화. 크레이 형제 실화 기반.',
    score: 0.97,
  },
  {
    id: 2,
    date: '02-28',
    type: '아이디어',
    typeColor: 'text-amber-400 bg-amber-400/10 border-amber-400/30',
    content: 'JARVIS 업무 정의 UI 개선 아이디어 - 드래그앤드롭으로 순서 변경',
    score: 0.84,
  },
  {
    id: 3,
    date: '02-27',
    type: '회의',
    typeColor: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
    content: '캡스톤 팀 킥오프 - 역할 분담 완료',
    score: 0.71,
  },
  {
    id: 4,
    date: '02-25',
    type: '영화',
    typeColor: 'text-violet-400 bg-violet-400/10 border-violet-400/30',
    content: '인터스텔라 재감상 - 3막 블랙홀 장면 인상적. 한스 짐머 OST.',
    score: 0.65,
  },
  {
    id: 5,
    date: '02-22',
    type: '메모',
    typeColor: 'text-blue-400 bg-blue-400/10 border-blue-400/30',
    content: 'pgvector 코사인 유사도 검색 - 임베딩 차원 1536, 인덱스 타입 HNSW',
    score: 0.58,
  },
]

function mockSearch(query: string): SearchResult[] {
  if (!query.trim()) return []
  const q = query.toLowerCase()
  return allRecords
    .filter(
      (r) =>
        r.content.toLowerCase().includes(q) ||
        r.type.toLowerCase().includes(q)
    )
    .sort((a, b) => b.score - a.score)
}

function ScoreBar({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1 bg-white/10 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-400/60 rounded-full"
          style={{ width: `${score * 100}%` }}
        />
      </div>
      <span className="text-white/20 text-[10px] font-mono">{(score * 100).toFixed(0)}%</span>
    </div>
  )
}

export default function Search() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[] | null>(null)
  const [isSearching, setIsSearching] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSearch = () => {
    const trimmed = query.trim()
    if (!trimmed) {
      setResults(null)
      return
    }

    setIsSearching(true)
    // 백엔드 연동 전: setTimeout으로 로딩 시뮬레이션
    setTimeout(() => {
      setResults(mockSearch(trimmed))
      setIsSearching(false)
    }, 400)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleSearch()
    }
    if (e.key === 'Escape') {
      setQuery('')
      setResults(null)
    }
  }

  const handleClear = () => {
    setQuery('')
    setResults(null)
    inputRef.current?.focus()
  }

  const hasQuery = query.trim().length > 0
  const hasResults = results !== null

  return (
    <div className="min-h-screen px-8 py-10 max-w-3xl mx-auto">
      {/* 헤더 */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-1 h-4 bg-blue-400 rounded-full" />
          <span className="text-white/40 text-xs font-mono tracking-widest uppercase">Search</span>
        </div>
        <h1 className="text-2xl font-semibold text-white/90 tracking-tight">기억 검색</h1>
        <p className="text-white/30 text-sm mt-1">기억나는 내용으로 기록을 찾아드립니다.</p>
      </div>

      {/* 검색창 */}
      <div className="relative mb-8">
        <div className="flex items-center gap-3 bg-[#111118] border border-white/10 focus-within:border-blue-500/50 rounded-xl px-5 py-3.5 transition-colors duration-200">
          {/* 검색 아이콘 */}
          <svg
            className="text-white/30 shrink-0"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>

          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="기억나는 내용으로 검색..."
            className="flex-1 bg-transparent text-white/80 text-base placeholder-white/20 outline-none"
          />

          {/* 로딩 스피너 */}
          {isSearching && (
            <svg className="animate-spin text-blue-400 shrink-0 w-4 h-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}

          {/* 지우기 버튼 */}
          {hasQuery && !isSearching && (
            <button
              onClick={handleClear}
              className="text-white/25 hover:text-white/60 transition-colors shrink-0"
              aria-label="검색어 지우기"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="15" y1="9" x2="9" y2="15" />
                <line x1="9" y1="9" x2="15" y2="15" />
              </svg>
            </button>
          )}

          {/* 검색 버튼 */}
          <button
            onClick={handleSearch}
            disabled={!hasQuery || isSearching}
            className="shrink-0 px-3 py-1 bg-blue-500 hover:bg-blue-400 disabled:bg-white/10 disabled:text-white/25 text-white text-sm rounded-lg transition-colors duration-150"
          >
            검색
          </button>
        </div>

        {/* 단축키 힌트 */}
        <div className="flex items-center gap-3 mt-2 px-1">
          <span className="text-white/15 text-[11px] font-mono">Enter 검색 · Esc 초기화</span>
        </div>
      </div>

      {/* 결과 영역 */}
      {!hasResults ? (
        /* 초기 상태 */
        <div className="flex flex-col items-center justify-center py-20 gap-4">
          <div className="w-14 h-14 rounded-2xl bg-[#111118] border border-white/8 flex items-center justify-center">
            <svg className="text-white/20" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </div>
          <div className="text-center">
            <p className="text-white/35 text-sm">검색어를 입력하세요</p>
            <p className="text-white/20 text-xs mt-1 font-mono">기억나는 키워드, 내용, 유형으로 검색 가능합니다</p>
          </div>
        </div>
      ) : results.length === 0 ? (
        /* 결과 없음 */
        <div className="flex flex-col items-center justify-center py-20 gap-4">
          <div className="w-14 h-14 rounded-2xl bg-[#111118] border border-white/8 flex items-center justify-center">
            <svg className="text-white/20" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <div className="text-center">
            <p className="text-white/35 text-sm">
              "<span className="text-white/55">{query}</span>"에 대한 결과가 없습니다
            </p>
            <p className="text-white/20 text-xs mt-1 font-mono">다른 키워드로 시도해보세요</p>
          </div>
        </div>
      ) : (
        /* 검색 결과 */
        <div>
          <div className="flex items-center gap-3 mb-4">
            <span className="text-white/50 text-xs font-mono tracking-widest uppercase">
              Results
            </span>
            <span className="text-blue-400 text-xs font-mono">{results.length}개</span>
            <div className="flex-1 h-px bg-white/5" />
          </div>

          <div className="flex flex-col gap-3">
            {results.map((result) => (
              <div
                key={result.id}
                className="group bg-[#111118] border border-white/8 hover:border-white/15 rounded-xl px-5 py-4 transition-colors duration-150 cursor-default"
              >
                <div className="flex items-start gap-4">
                  {/* 날짜 */}
                  <div className="shrink-0 text-white/25 text-xs font-mono pt-0.5 w-10">
                    {result.date}
                  </div>

                  {/* 내용 */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span
                        className={`inline-flex px-2 py-0.5 rounded text-[11px] font-medium border ${result.typeColor}`}
                      >
                        {result.type}
                      </span>
                      <ScoreBar score={result.score} />
                    </div>
                    <p className="text-white/60 text-sm leading-relaxed">{result.content}</p>
                  </div>

                  {/* 화살표 */}
                  <div className="shrink-0 text-white/15 group-hover:text-white/30 pt-1 transition-colors">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="9 18 15 12 9 6" />
                    </svg>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
