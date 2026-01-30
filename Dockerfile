# SPDX-FileCopyrightText: Copyright (c) 2025 Massachusetts Institute of Technology
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

############################################################
# Base image
############################################################
FROM python:3.13-slim AS base

# Install any utils needed for execution
ARG DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    nano \
    vim

# Install Python dependencies not covered by deb packages
RUN python3 -m pip install --no-cache-dir aiomqtt anyio exceptiongroup jsonargparse[ruamel,signatures] pyserial

############################################################
# MEP image
############################################################
FROM base AS mep
LABEL org.opencontainers.image.description="MEP tuner control service"

# Users can optionally mount a volume to /app containing
# these scripts, but copy them into the image anyway to allow
# use of these static files without the mounting
COPY --chmod=775 src /app/

# Set up environment variable defaults for this image
ENV RECORDER_CONFIG_PATH=/config

ENV HOME=/app
WORKDIR /app
ENTRYPOINT ["python3", "/app/tuner_control_service.py"]

############################################################
# Default target
############################################################
FROM mep AS default
