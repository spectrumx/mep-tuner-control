# SPDX-FileCopyrightText: Copyright (c) 2026 Massachusetts Institute of Technology
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

import dataclasses
import json
import logging
import os
import socket
import time
import traceback
from typing import Optional

import aiomqtt
import anyio
import exceptiongroup
import jsonargparse

from mep_tuners import TunerBase, ValonTunerParams

logger = logging.getLogger("tuner_control_service")
logger.setLevel(os.environ.get("TUNER_CONTROL_SERVICE_LOG_LEVEL", "NOTSET"))
logger.propagate = False
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG)
logger.addHandler(_console_handler)


# Holds tuner object parameters to make available through jsonargparse
# (types are ordered by preference)
@dataclasses.dataclass(kw_only=True)
class TunerConfig:
    valon: ValonTunerParams = dataclasses.field(
        default_factory=lambda: ValonTunerParams(name="valon")
    )


@dataclasses.dataclass(kw_only=True)
class TunerControlService:
    # service configuration variables
    announce_topic: str = "announce/{service.name}"
    command_topic: str = "{service.name}/command"
    cfg: TunerConfig = dataclasses.field(default_factory=lambda: TunerConfig())
    name: str = "tuner_control"
    node_id: Optional[str] = None
    status_topic: str = "{service.name}/status"
    # service state variables
    tuner: Optional[TunerBase] = dataclasses.field(default=None, init=False)

    def __post_init__(self):
        if self.node_id is None:
            self.node_id = os.getenv("NODE_ID", socket.gethostname())

        self.announce_topic = self.announce_topic.format(service=self)
        self.command_topic = self.command_topic.format(service=self)
        self.status_topic = self.status_topic.format(service=self)

        init_tuner(self)


def init_tuner(service, force_tuner=None):
    """Iterate through known tuners and take the first ready one"""
    if force_tuner is not None:
        tuner_config = getattr(service.cfg, force_tuner)
        service.tuner = tuner_config.create_tuner()
    for name, tuner_config in service.cfg.__dict__.items():
        try:
            service.tuner = tuner_config.create_tuner()
        except Exception:
            logger.info(f"Failed to init tuner: {name}", exc_info=True)
        else:
            break
    else:
        service.tuner = None


async def send_announce(client, service):
    payload = {
        "title": "Tuner control",
        "description": "Control and monitor MEP tuner devices",
        "author": "Ryan Volz <rvolz@mit.edu>",
        "url": "ghcr.io/spectrumx/mep-tuner-control:latest",
        "source": "https://github.com/spectrumx/mep-tuner-control",
        "version": "0.1",
        "type": "service",
        "time_started": time.time(),
    }
    await client.publish(service.announce_topic, json.dumps(payload), retain=True)


async def send_status(client, service):
    payload = {
        "state": "no_tuners",
        "timestamp": time.time(),
    }
    if service.tuner is not None:
        payload["state"] = "ready"
        payload["tuner"] = dataclasses.asdict(service.tuner)
    await client.publish(service.status_topic, json.dumps(payload), retain=True)


async def send_response(client, service, message, response_topic=None):
    if response_topic is None:
        response_topic = service.status_topic
    payload = {
        "message": message,
        "timestamp": time.time(),
    }
    await client.publish(response_topic, json.dumps(payload))


async def process_tuner_command(client, service, payload):
    args = payload.get("arguments", {})
    response_topic = payload.get("response_topic", None)
    try:
        cmd = payload["task_name"]
        fun = service.tuner.getattr(cmd)
        result = fun(**args)
        msg = f"{service.tuner.name}.{cmd}: {result if result is not None else 'Done'}"
        await send_response(client, service, msg, response_topic)
        await send_status(client, service)
    except Exception:
        msg = f"ERROR tuner command:\n{traceback.format_exc()}"
        await send_response(client, service, msg, response_topic)


async def process_commands(client, service):
    async for message in client.messages:
        payload = json.loads(message.payload.decode())
        if payload["task_name"] == "init_tuner":
            init_tuner(service, force_tuner=payload.get("force_tuner", None))
            await send_status(client, service)
        elif payload["task_name"] == "restart_tuner":
            response_topic = payload.get("response_topic", None)
            if service.tuner is not None:
                try:
                    service.tuner = service.tuner.replace()
                except Exception:
                    msg = f"ERROR restarting tuner:\n{traceback.format_exc()}"
                    await send_response(client, service, msg, response_topic)
                else:
                    msg = "Restarted tuner successfully"
                    await send_response(client, service, msg, response_topic)
            await send_status(client, service)
        elif payload["task_name"] == "status":
            await send_status(client, service)
        else:
            await process_tuner_command(client, service, payload)


async def main(service):
    will = aiomqtt.Will(
        service.status_topic,
        payload=json.dumps({"state": "offline"}),
        qos=0,
        retain=True,
    )
    client = aiomqtt.Client(
        "localhost",
        1883,
        keepalive=60,
        will=will,
    )
    interval = 5  # seconds
    while True:
        try:
            async with client:
                await client.subscribe(service.command_topic)
                await send_announce(client, service)
                await send_status(client, service)
                with exceptiongroup.catch(
                    {
                        Exception: lambda exc: traceback.print_exc(),
                    }
                ):
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(process_commands, client, service)
        except aiomqtt.MqttError:
            msg = (
                "Connection to MQTT server lost;"
                f" Reconnecting in {interval} seconds ..."
            )
            print(msg)
            await anyio.sleep(interval)


if __name__ == "__main__":
    service = jsonargparse.auto_cli(
        TunerControlService, env_prefix="TUNER", default_env=True
    )
    anyio.run(main, service)
