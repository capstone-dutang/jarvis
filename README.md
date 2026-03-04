# JARVIS

AI 기반 개인 비서 / 지식관리 시스템. 사용자 정의 업무(Workflow) 실행 엔진.

## 빠른 시작

```bash
# 환경변수 설정
cp backend/.env.example backend/.env
# OPENAI_API_KEY 설정 필요

# Docker로 전체 실행
docker-compose up -d

# 백엔드: http://localhost:8000
# 프론트엔드: http://localhost:5173
# API 문서: http://localhost:8000/docs
```

## 기술 스택
- Backend: FastAPI (Python 3.12)
- DB: PostgreSQL 16 + pgvector + FTS
- Cache/Queue: Redis + Celery
- Frontend: React + TypeScript + Vite
- AI: OpenAI API (GPT-4o + Embeddings + Whisper)
