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
"""Valon 5015/5019 RF Synthesizer

Alisa Yurevich (Alisa.Yurevich@tufts.edu) 06/2025
Ryan Volz (rvolz@mit.edu) 01/2026
"""

import dataclasses
import logging
import os
import time
import typing

import serial

from .tuner_base import MEPTuner

logger = logging.getLogger(__name__)
logger.setLevel(
    os.environ.get(f"{__name__.replace('.', '_').upper()}_LOG_LEVEL", "NOTSET")
)


@dataclasses.dataclass(kw_only=True)
class ValonTuner(MEPTuner):
    """Valon 5015/5019 RF Synthesizer over a serial connection.

    This tool configures the synthesizer via USB serial connection.
    NOTE: Internal error from serial is thrown sometimes when the VALON is run for too
    long (hours). Possible solutions include:
        - a reset function (although this will introduce time delay in a sweep)
        - higher/lower baud rate
        - shorter sleep after send
    At a power input of 0, the relative output is equal to that of the LMX2820.

    """

    name: str = dataclasses.field(default="valon", init=False)
    port: str = "/dev/valon5015"  # default assumes persistent udev symlink
    baudrate: int = 9600
    timeout: float = 1.0
    pwr_dBm: typing.Optional[float] = dataclasses.field(default=None, init=False)

    def __post_init__(self):
        self.reset_connection()

    def reset_connection(self):
        try:
            self._reset_serial_connection()
        except Exception:
            self.ready = False
            logger.info("Could not connect to Valon synthesizer", exc_info=True)
        else:
            self.ready = True
            logger.debug("Getting VALON device status string:")
            self.info = self.send_cmd("STAT")
            logger.debug(self.info)

    def _reset_serial_connection(self):
        if self._ser and self._ser.is_open:
            self._ser.close()

        self._ser = serial.Serial(
            port=self.port, baudrate=self.baudrate, timeout=self.timeout
        )

        # one way to clear -> can also turn dtr on and off
        self._ser.reset_input_buffer()

    def __del__(self):
        """Close serial connection."""
        self._ser.reset_input_buffer()
        del self._ser

    def _send_cmd(self, command):
        """Send a command string to the Valon over serial.

        Appends carriage return. Returns any response.

        """
        self._ser.write((command + "\r").encode())
        time.sleep(0.1)
        response = b""

        while self._ser.in_waiting:
            response += self._ser.read(self._ser.in_waiting)

        return response.decode(errors="ignore")

    def send_cmd(self, command, retries=3):
        """Send a command string to the Valon over serial.

        Appends carriage return. Returns any response.

        """
        for n in range(retries):
            try:
                response = self._send_cmd(command)
            except Exception as e:
                if n == (retries - 1):
                    raise e
                else:
                    logger.warning(f"Failed to send command: {command}", exc_info=True)
                    self._reset_serial_connection()
            else:
                break
        return response

    def set_freq(self, freq_mhz):
        """Set the output frequency of the synthesizer."""
        logger.info(f"Setting local oscillator frequency to {freq_mhz} MHz")
        cmd = f"F{freq_mhz}MHz"
        logger.debug(f"Sending frequency command: {cmd}")
        result = self.send_cmd(cmd)
        logger.debug(result)
        self.freq_mhz = freq_mhz
        return result

    def set_power(self, pwr_dBm):
        """Set output power level.

        Valid Range -50 - 20. Can be brought lower configuring extra settings in the
        Valon.

        """
        logger.info(f"Setting output power level to {pwr_dBm} dBm")
        cmd = f"PWR {pwr_dBm}"
        logger.debug(f"Sending power command: {cmd}")
        result = self.send_cmd(cmd)
        logger.debug(result)
        self.pwr_dBm = pwr_dBm
        return result

    def get_lock_status(self):
        """Return the status of the PLL lock condition from Main and Sub PLLs"""
        cmd = "LK"
        logger.debug(f"Sending lock status command: {cmd}")
        result = self.send_cmd(cmd)
        logger.debug(result)
        return result
