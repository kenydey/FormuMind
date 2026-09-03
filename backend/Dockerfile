FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# No apt layer by default. Every package this image installs — core
# requirements plus the [llm] extras — resolves to a prebuilt wheel on
# linux/x86_64/cp311. The one exception, jieba, ships only an sdist but is pure
# Python and builds with no compiler present, so `build-essential` bought
# nothing except a dependency on the Debian archive being reachable and its
# release metadata current. That is the least reliable step in the whole build:
# it fails with a bare `exit code: 100` on a stale base image, a moved mirror,
# or a proxy that intercepts apt but not pip — none of which have anything to
# do with this application.
#
# Turn it on when you need a compiler in the image, which today means only
# installing an sdist-only extra at runtime through the dependency installer:
#   docker compose build --build-arg INSTALL_BUILD_TOOLCHAIN=true backend
ARG INSTALL_BUILD_TOOLCHAIN=false
RUN if [ "$INSTALL_BUILD_TOOLCHAIN" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            build-essential \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# 1) Install pinned runtime deps first so this layer is cached across app changes.
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

# 2) Install application + ALL extras (LLM, science, optimization, RAG, etc.).
# Excludes parse-pro (docling conflicts with intel's chemcrow via beautifulsoup4).
# Torch pinned to CPU-only wheels to keep the image small on GPU-less hosts.
# alembic.ini rides along so migrations are runnable *inside* the container:
#   docker compose exec backend alembic upgrade head
COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
    -e ".[llm,science,optimize,embedding,bo,pydoe,baybe,file_ingest,intel,export,colbert,color,dev,postgres,heavy,crag]" \
    && pip install --extra-index-url https://download.pytorch.org/whl/cpu \
    pymupdf4llm docling marker-pdf mineru-open-sdk \
    && pip uninstall -y surya-ocr 2>/dev/null; echo "surya-ocr removed (CPU VPS)"

# 3) Local OCR for scanned pages. No build-time model download step is needed:
# rapidocr-onnxruntime ships the Chinese PP-OCRv4 weights inside the wheel
# (16 MB — det + rec + cls), so the container OCRs offline the moment it starts.
# Verified by blocking the network and running a page through it.
#
# opencv-python-headless is installed explicitly and the GUI build removed:
# rapidocr depends on plain opencv-python, which pulls libGL and friends that a
# headless image does not have.
RUN pip install "rapidocr-onnxruntime>=1.4.4" opencv-python-headless \
    && pip uninstall -y opencv-python 2>/dev/null; \
    python -c "import rapidocr_onnxruntime, pathlib, sys; \
m = pathlib.Path(rapidocr_onnxruntime.__file__).parent / 'models'; \
n = len(list(m.glob('*.onnx'))); print(f'rapidocr models bundled: {n}'); \
sys.exit(0 if n >= 3 else 1)"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
