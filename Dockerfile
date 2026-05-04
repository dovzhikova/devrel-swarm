FROM python:3.12-slim

WORKDIR /app

# System deps for Vox (video TTS pipeline)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install via pyproject.toml (single source of truth for deps)
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Optional maintainer assets — useful when the container is the dev-environment
# rather than just a runtime. Drop these COPYs if you only need the CLI.
COPY config/ config/
COPY knowledge_base/ knowledge_base/
COPY optimize/ optimize/

# Default: drop into the CLI. Override with e.g. `devrel doctor`.
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["devrel"]
CMD ["--help"]
