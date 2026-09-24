# [Choice] Python version (use -bullseye variants on local arm64/Apple Silicon): 3, 3.10, 3.9, 3.8, 3.7, 3.6, 3-bullseye, 3.10-bullseye, 3.9-bullseye, 3.8-bullseye, 3.7-bullseye, 3.6-bullseye, 3-buster, 3.10-buster, 3.9-buster, 3.8-buster, 3.7-buster, 3.6-buster
# ARG VARIANT=3-bullseye
FROM mcr.microsoft.com/devcontainers/python:2-3.13-bookworm

ENV PYTHONUNBUFFERED 1

# [Choice] Node.js version: none, lts/*, 16, 14, 12, 10
ARG NODE_VERSION="none"
RUN if [ "${NODE_VERSION}" != "none" ]; then su vscode -c "umask 0002 && . /usr/local/share/nvm/nvm.sh && nvm install ${NODE_VERSION} 2>&1"; fi

# [Optional] Uncomment this section to install additional OS packages.
# Remove yarn source list to avoid conflicts to fix the build error on apple silicon
RUN rm -f /etc/apt/sources.list.d/yarn.list && apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    apt-transport-https \
	ca-certificates \
	netbase \
	wget \
    redis

WORKDIR /workspace

# Install the ffmpeg to compress/decompress video files
RUN apt-get update && apt-get install -y ffmpeg ripgrep

# [Optional] If your requirements rarely change, uncomment this section to add them to the image.
COPY requirements.txt /tmp
RUN python -m pip --no-cache-dir install --upgrade 'pip>=26.2' \
    && python -m pip --no-cache-dir install -r /tmp/requirements.txt
