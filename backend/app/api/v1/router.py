from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ProcessRequest(BaseModel):
    text: str


class ProcessResponse(BaseModel):
    status: str
    workflow: str
    message: str


@router.post("/process", response_model=ProcessResponse)
async def process_input(req: ProcessRequest):
    """
    사용자 입력을 받아 업무 판단 후 처리.
    Phase 2에서 실제 LLM 판단 로직으로 교체 예정.
    """
    text = req.text.strip()

    # TODO: Phase 2 - LLM 기반 업무 판단으로 교체
    # 임시: 키워드 기반 mock 판단
    if any(k in text for k in ["영화", "드라마", "봄", "봤"]):
        workflow = "미디어 기록"
    elif any(k in text for k in ["회의", "미팅", "팀"]):
        workflow = "회의 기록"
    elif any(k in text for k in ["아이디어", "생각", "기획"]):
        workflow = "아이디어 수집"
    else:
        workflow = "일반 기록"

    return ProcessResponse(
        status="ok",
        workflow=workflow,
        message=f"'{workflow}' 업무로 처리되었습니다.",
    )
