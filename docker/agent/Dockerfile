FROM mcr.microsoft.com/playwright:v1.44.0-jammy

# Install base dependencies for the agent
RUN apt-get update && apt-get install -y \
    git \
    curl \
    python3.12 \
    python3.12-venv \
    python3-pip \
    npm \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Set up a working directory
WORKDIR /workspace

# Install common Python tools to save tokens/time during agent spin-up
RUN python3.12 -m pip install --upgrade pip && \
    python3.12 -m pip install pytest pytest-asyncio pandas numpy requests beautifulsoup4

# Install global node tools 
RUN npm install -g typescript ts-node

# Create a non-root user for the agent to run as safely
RUN useradd -m -s /bin/bash openhands && \
    echo "openhands ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER openhands
ENV PATH="/home/openhands/.local/bin:${PATH}"
