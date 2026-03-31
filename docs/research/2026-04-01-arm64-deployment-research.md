# Deploying FastAPI + PostgreSQL + pgvector on Oracle Cloud ARM64

> 연구 일자: 2026-04-01
> 성격: Oracle Cloud ARM64 배포 삽질 방지 리서치
> 상태: 활성

**핵심:** 전부 ARM64에서 돌아간다. pgvector은 1st class 지원, PGroonga는 소스 빌드 필요, ML 추론은 ONNX int8 양자화가 최적.

---

## pgvector — 프로덕션 레디

- AWS Graviton2/3에서 테스트 완료, CI에서 ARM64 테스트
- apt로 설치: `sudo apt install postgresql-16-pgvector`
- Docker: `pgvector/pgvector:pg16` (linux/arm64 네이티브)
- HNSW 정상 동작, ARM64 전용 버그 없음
- v0.5.0에서 ARM64 거리 계산 최적화 포함

## PGroonga — 소스 빌드 필요

Docker에서 Groonga + PGroonga 소스 빌드:

```dockerfile
FROM postgres:16-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake wget pkg-config \
    libmsgpack-dev libmecab-dev zlib1g-dev liblz4-dev libzstd-dev \
    postgresql-server-dev-16
RUN cd /tmp \
    && wget https://packages.groonga.org/source/groonga/groonga-14.1.2.tar.gz \
    && tar xzf groonga-14.1.2.tar.gz && cd groonga-14.1.2 \
    && cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr/local \
       -DCMAKE_BUILD_TYPE=Release -DGRN_WITH_MRUBY=OFF \
    && cmake --build build --parallel $(nproc) \
    && cmake --install build && ldconfig && rm -rf /tmp/groonga*
RUN cd /tmp \
    && wget https://packages.groonga.org/source/pgroonga/pgroonga-3.2.5.tar.gz \
    && tar xzf pgroonga-3.2.5.tar.gz && cd pgroonga-3.2.5 \
    && make HAVE_MSGPACK=1 && make install && rm -rf /tmp/pgroonga*
```

- 빌드 시간: 4 ARM 코어에서 15~30분
- 한 번 빌드 → 레지스트리에 push → 재배포 시 pull
- 한국어: 기본 N-gram 토크나이저로 MeCab 없이 동작

## ML 추론 — ONNX int8 양자화가 최적

| 백엔드 | 지연 | 메모리 |
|--------|------|--------|
| PyTorch fp32 | ~15-40ms | ~1.0-1.5GB |
| ONNX fp32 | ~10-30ms | ~600-800MB |
| **ONNX int8 ARM64** | **~5-15ms** | **~300-450MB** |

```python
from sentence_transformers import SentenceTransformer, export_dynamic_quantized_onnx_model

model = SentenceTransformer("dragonkue/multilingual-e5-small-ko", backend="onnx")
export_dynamic_quantized_onnx_model(
    model=model,
    quantization_config="arm64",
    model_name_or_path="./quantized_model",
)
```

- 모델 크기: fp32 ~449MB → int8 **~113MB**
- 24GB 중 ~300-450MB 사용, 23GB+ 여유

## Oracle Cloud 프로비저닝 주의사항

### 인스턴스 확보
- **PAYG 업그레이드 권장** — 프로비저닝 우선순위 상승 + idle 회수 면제
- "Out of host capacity" 빈번 → 자동 재시도 스크립트 사용
- 덜 인기 있는 리전 선택, 1 OCPU로 시작 후 리사이즈

### Idle 회수 주의
- CPU/네트워크/메모리 전부 15% 미만 × 7일 → 경고 → 7일 후 정지
- PAYG 계정은 면제
- 실제 워크로드 돌리면 자연 방지

### 이중 방화벽
- OCI Security List + VM 내부 iptables 둘 다 열어야 함
- UFW 사용 금지 (부팅 실패 가능)

## PostgreSQL 튜닝 (24GB ARM)

```ini
shared_buffers = 6GB
effective_cache_size = 18GB
work_mem = 64MB
maintenance_work_mem = 1GB
max_connections = 50
max_parallel_workers = 4
max_parallel_workers_per_gather = 2
random_page_cost = 1.1
effective_io_concurrency = 200
```

## 전체 스택 메모리 예산

| 구성요소 | 메모리 |
|---------|--------|
| PostgreSQL (shared_buffers) | 6GB |
| ONNX 임베딩 모델 (int8) | ~400MB |
| FastAPI 서버 (2 workers) | ~200MB |
| OS + Docker | ~1GB |
| **합계** | **~7.6GB / 24GB** |
| **여유** | **~16GB (OS 페이지 캐시 등)** |
