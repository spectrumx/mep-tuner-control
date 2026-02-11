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

import collections
import dataclasses
import functools
import inspect
import logging
import operator
import os
import socket
import time
import traceback
from typing import Optional

import aiomqtt
import anyio
import exceptiongroup
import jsonargparse
import msgspec
from typing_extensions import Format, get_annotations

from mep_tuners import DummyTunerParams, LMX2820TunerParams, TunerBase, ValonTunerParams

logger = logging.getLogger("tuner_control_service")
logger.setLevel(os.environ.get("TUNER_CONTROL_SERVICE_LOG_LEVEL", "INFO"))
logger.propagate = False
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG)
logger.addHandler(_console_handler)


def deep_update(mapping: dict, *updating_mappings: dict) -> dict:
    """Update nested dictionary from another nested dictionary"""
    updated_mapping = mapping.copy()
    for updating_mapping in updating_mappings:
        for k, v in updating_mapping.items():
            if (
                k in updated_mapping
                and isinstance(updated_mapping[k], dict)
                and isinstance(v, dict)
            ):
                updated_mapping[k] = deep_update(updated_mapping[k], v)
            else:
                updated_mapping[k] = v
    return updated_mapping


# Holds tuner object parameters to make available through jsonargparse
# (types are ordered by preference)
@dataclasses.dataclass(kw_only=True)
class TunerConfig:
    valon: ValonTunerParams = dataclasses.field(
        default_factory=lambda: ValonTunerParams(name="valon")
    )
    lmx2820: LMX2820TunerParams = dataclasses.field(
        default_factory=lambda: LMX2820TunerParams(name="lmx2820")
    )
    dummy: DummyTunerParams = dataclasses.field(
        default_factory=lambda: DummyTunerParams(name="dummy")
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


def create_tuner(name, tuner_config):
    try:
        tuner = tuner_config.create_tuner()
    except Exception:
        logger.info(f"Failed to init tuner: {name}")
        logger.debug("Tuner exception:", exc_info=True)
        return None
    else:
        logger.info(f"Initialized tuner: {name}")
        return tuner


def init_tuner(service, force_tuner=None):
    """Iterate through known tuners and take the first ready one"""
    if force_tuner is not None:
        logger.debug(f"Force initing tuner: {force_tuner}")
        try:
            tuner_config = getattr(service.cfg, force_tuner)
        except AttributeError:
            msg = f"No tuner config for {force_tuner} found"
            logger.info(msg)
            return msg
        tuner = create_tuner(force_tuner, tuner_config)
    else:
        for name, tuner_config in service.cfg.__dict__.items():
            # skip dummy tuner for auto-init
            if name == "dummy":
                continue
            logger.debug(f"Trying to init tuner: {name}")
            tuner = create_tuner(name, tuner_config)
            if tuner is not None:
                break
    if tuner is None:
        msg = "No tuners found"
        logger.info(msg)
        service.tuner = None
        return msg
    service.tuner = tuner


async def send_announce(client, service):
    # inspect known Tuner classes to get valid tuner commands
    tuner_commands = collections.defaultdict(dict)
    for name, tuner_config in service.cfg.__dict__.items():
        for method_name, fun in inspect.getmembers(
            tuner_config.tuner_class,
            lambda v: inspect.isfunction(v) and not v.__name__.startswith("_"),
        ):
            tuner_commands[name][method_name] = {
                "task_name": method_name,
                "arguments": get_annotations(fun, format=Format.STRING),
                "doc": fun.__doc__,
            }
    payload = {
        "title": "Tuner control",
        "description": "Control and monitor MEP tuner devices",
        "author": "Ryan Volz <rvolz@mit.edu>",
        "url": "ghcr.io/spectrumx/mep-tuner-control:latest",
        "source": "https://github.com/spectrumx/mep-tuner-control",
        "output": {
            "status": {
                "type": "mqtt",
                "value": f"{service.status_topic}",
            },
        },
        "version": "0.1",
        "type": "service",
        "time_started": time.time(),
        "command_topic": f"{service.command_topic}",
        "commands": {
            "status": {
                "task_name": "status",
                "arguments": {},
            },
            "init_tuner": {
                "task_name": "init_tuner",
                "arguments": {"force_tuner": "str"},
            },
            "restart_tuner": {
                "task_name": "restart_tuner",
                "arguments": {},
            },
        },
        "tuner_commands": tuner_commands,
    }
    json_payload = msgspec.json.encode(payload)
    logger.debug(
        f"Announcing {service.name} on {service.announce_topic}:\n{json_payload}"
    )
    await client.publish(service.announce_topic, json_payload, retain=True)


async def send_status(client, service):
    payload = {
        "state": "disabled",
        "timestamp": time.time(),
    }
    if service.tuner is not None:
        payload["state"] = "online"
        payload["tuner"] = dataclasses.asdict(service.tuner)
    json_payload = msgspec.json.encode(payload)
    logger.debug(
        f"Sending {service.name} status to {service.status_topic}:\n{json_payload}"
    )
    await client.publish(service.status_topic, json_payload, retain=True)


async def send_response(client, service, message, response_topic=None):
    if response_topic is None:
        response_topic = service.status_topic
    payload = {
        "message": message,
        "timestamp": time.time(),
    }
    json_payload = msgspec.json.encode(payload)
    logger.debug(
        f"Sending {service.name} command response to {response_topic}:\n{json_payload}"
    )
    await client.publish(response_topic, json_payload)


async def send_value(client, service, value, response_topic=None):
    if response_topic is None:
        response_topic = service.status_topic
    payload = {
        "value": value,
        "timestamp": time.time(),
    }
    json_payload = msgspec.json.encode(payload)
    logger.debug(f"Sending {service.name} value to {response_topic}:\n{json_payload}")
    await client.publish(response_topic, json_payload)


async def process_config_command(client, service, payload):
    cmd = payload["task_name"].removeprefix("config.")
    args = payload.get("arguments", {})
    response_topic = payload.get("response_topic", None)
    try:
        if cmd == "get":
            key = args.get("key", "")
            try:
                if not key:
                    value = service.cfg
                else:
                    # does service.cfg.{key} where key can have additional dot levels
                    value = operator.attrgetter(key)(service.cfg)
            except AttributeError:
                msg = f"ERROR config.get: key '{key}' not found."
                logger.warning(msg)
                await send_response(client, service, msg, response_topic)
            else:
                logger.debug(f"Got config key {key}: {value}")
                await send_value(client, service, value, response_topic)
        if cmd == "set":
            key = args.get("key", "")
            val = args["value"]
            # convert config to dict so we can deep update it and then use
            # msgspec.convert to go back to a dataclass with type checking
            cfg_dict = dataclasses.asdict(service.cfg)
            if not key:
                update_dict = val
            else:
                update_dict = functools.reduce(
                    lambda v, k: {k: v}, reversed(key.split(".")), val
                )
            updated_cfg_dict = deep_update(cfg_dict, update_dict)
            updated_cfg = msgspec.convert(updated_cfg_dict, type=type(service.cfg))
            service.cfg = updated_cfg
            logger.debug(f"Set config key {key}: {val}")
            await send_value(
                client, service, dataclasses.asdict(service.cfg), response_topic
            )
    except Exception:
        logger.exception(
            f"Error processing config payload:\n{msgspec.json.encode(payload)}"
        )
        msg = f"ERROR config:\n{traceback.format_exc()}"
        await send_response(client, service, msg, response_topic)


async def process_tuner_command(client, service, payload):
    args = payload.get("arguments", {})
    response_topic = payload.get("response_topic", None)
    try:
        cmd = payload["task_name"]
        logger.info(f"Processing {cmd} command")
        fun = service.tuner.getattr(cmd)
        result = fun(**args)
        msg = f"{service.tuner.name}.{cmd}: {result if result is not None else 'Done'}"
        await send_response(client, service, msg, response_topic)
        await send_status(client, service)
    except Exception:
        logger.exception(
            f"Error processing command payload:\n{msgspec.json.encode(payload)}"
        )
        msg = f"ERROR tuner command:\n{traceback.format_exc()}"
        await send_response(client, service, msg, response_topic)


async def process_commands(client, service):
    logger.info(f"Service {service.name} listening for commands")
    async for message in client.messages:
        payload = msgspec.json.decode(message.payload)
        logger.debug(f"Received message:\n{msgspec.json.encode(payload)}")
        if payload["task_name"] == "init_tuner":
            logger.info("Processing init_tuner command")
            msg = init_tuner(service, force_tuner=payload.get("force_tuner", None))
            if msg:
                response_topic = payload.get("response_topic", None)
                await send_response(client, service, msg, response_topic)
            await send_status(client, service)
        elif payload["task_name"] == "restart_tuner":
            logger.info("Processing restart_tuner command")
            response_topic = payload.get("response_topic", None)
            if service.tuner is not None:
                try:
                    service.tuner = dataclasses.replace(service.tuner)
                except Exception:
                    logger.exception("Error restarting tuner")
                    msg = f"ERROR restarting tuner:\n{traceback.format_exc()}"
                    await send_response(client, service, msg, response_topic)
                else:
                    msg = "Restarted tuner successfully"
                    await send_response(client, service, msg, response_topic)
            await send_status(client, service)
        elif payload["task_name"] == "status":
            logger.info("Processing status command")
            await send_status(client, service)
        elif payload["task_name"].startswith("config."):
            await process_config_command(client, service, payload)
        else:
            await process_tuner_command(client, service, payload)


async def main(service):
    will = aiomqtt.Will(
        service.status_topic,
        payload=msgspec.json.encode({"state": "offline"}),
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
                        Exception: lambda exc: logger.error("Exception", exc_info=exc),
                    }
                ):
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(process_commands, client, service)
        except aiomqtt.MqttError:
            msg = (
                "Connection to MQTT server lost;"
                f" Reconnecting in {interval} seconds ..."
            )
            logger.warning(msg)
            await anyio.sleep(interval)


if __name__ == "__main__":
    logger.info("Starting tuner_control_service")
    service = jsonargparse.auto_cli(
        TunerControlService, env_prefix="TUNER", default_env=True
    )
    anyio.run(main, service)
