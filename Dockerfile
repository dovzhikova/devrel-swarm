FROM python:3.12-slim

WORKDIR /app

# System deps for optional features
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY src/ src/
COPY pyproject.toml ./
RUN pip install -e .
COPY config/ config/
COPY knowledge_base/ knowledge_base/
COPY optimize/ optimize/

# Create output directories
RUN mkdir -p deliverables context_archive

# Default: run the full weekly cycle
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "devrel_swarm.core.atlas", "--weekly-cycle"]
