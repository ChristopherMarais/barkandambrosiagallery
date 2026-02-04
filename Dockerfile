FROM ubuntu:22.04

# Avoid prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies for Pixi + Postgres drivers
RUN apt-get update && apt-get install -y \
    curl ca-certificates libglib2.0-0 libpq-dev git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Pixi
RUN curl -fsSL https://pixi.sh/install.sh | bash
ENV PATH="/root/.pixi/bin:$PATH"

# Copy Pixi Manifests
COPY pixi.toml pixi.lock /app/

# Install Environment (Frozen)
RUN pixi install --frozen

# Copy Code
COPY . /app/

# Collect Static Files
# We use a dummy secret key just for the build process
RUN SECRET_KEY=build_process_only pixi run collectstatic

# Expose Port
EXPOSE 8000

# Default Command (Production)
CMD ["pixi", "run", "gunicorn"]