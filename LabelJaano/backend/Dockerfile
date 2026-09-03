# Label Jaano backend — lean, mock-capable image.
#
# Build from the LabelJaano/ directory (one level up) so the rulepacks/ that live
# beside backend/ can be copied in:
#
#     cd LabelJaano
#     docker build -f backend/Dockerfile -t labeljaano-backend .
#     docker run --rm -p 8000:8000 labeljaano-backend
#
# This installs requirements-core.txt plus the two libraries a real vision read needs
# (google-genai, pillow), so a deployed container does real optical extraction as soon
# as GEMINI_API_KEY is set in its environment — and falls back to mock, loudly, if it
# is not. What it still leaves out is the OCR stack (paddleocr/paddlepaddle/opencv in
# requirements.txt), which only supplies the pixel geometry for the Rule 8 font-height
# check; that check SKIPS without it rather than failing.
FROM python:3.12-slim

# PYTHONUNBUFFERED  — logs (labeljaano.*) reach `docker logs` immediately, unbuffered.
# *DONTWRITEBYTECODE — no .pyc litter in a layer that is rebuilt on every code change.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LABEL_JAANO_RULEPACKS=/rulepacks \
    LABEL_JAANO_DB=/data/labeljaano.db

WORKDIR /app

# Dependencies first, on their own layer: this layer is rebuilt only when the pinned
# core deps change, not on every source edit.
COPY backend/requirements-core.txt ./
RUN pip install -r requirements-core.txt "google-genai>=1.0" "pillow>=10.0"

# Then the application and the rulepacks it enforces. rulepacks/ is a sibling of
# backend/ in the build context, hence the LabelJaano/ context root documented above.
COPY backend/ /app/
COPY rulepacks/ /rulepacks/

# Run as a non-root user. The code in /app stays root-owned and read-only to the app;
# the ONLY thing the process writes is the SQLite history, which we point at a
# dedicated /data volume owned by that user (VOLUME comes AFTER the chown so the
# ownership is baked into the image and not discarded).
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data
VOLUME /data
USER appuser

EXPOSE 8000

# curl is not in python:*-slim, so probe /health with the stdlib. Non-200 (or a
# refused connection) exits non-zero and marks the container unhealthy. PORT is read
# inside Python rather than interpolated by the shell, so the probe follows the port
# the server was actually told to bind.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/health', timeout=2).status == 200 else 1)"

# Managed hosts (Render, Railway, Cloud Run) inject the port they expect the process
# to bind, and a container that ignores it never passes their health check — it just
# looks dead. Shell form so $PORT is expanded at run time; the default keeps a plain
# `docker run -p 8000:8000` working exactly as before.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
