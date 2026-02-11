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

from .tuner_base import TunerBase, TunerParamsBase

logger = logging.getLogger(__name__)
logger.setLevel(
    os.environ.get(f"{__name__.replace('.', '_').upper()}_LOG_LEVEL", "NOTSET")
)


@dataclasses.dataclass(kw_only=True)
class ValonTuner(TunerBase):
    """Valon 5015/5019 RF Synthesizer over a serial connection.

    This tool configures the synthesizer via USB serial connection.
    NOTE: Internal error from serial is thrown sometimes when the VALON is run for too
    long (hours). Possible solutions include:
        - a reset function (although this will introduce time delay in a sweep)
        - higher/lower baud rate
        - shorter sleep after send
    At a power input of 0, the relative output is equal to that of the LMX2820.

    """

    name: str = "valon"
    """Name of tuner instance"""
    port: str = "/dev/valon5015"  # default assumes persistent udev symlink
    """Serial device name"""
    baudrate: int = 9600
    """Serial device baud rate"""
    timeout: float = 1.0
    """Serial device read timeout in seconds"""
    pwr_dbm: typing.Optional[float] = None
    """Output power in dBm"""
    info: typing.Optional[str] = None
    """Status string (set upon initialization)"""

    def __post_init__(self):
        self.ser = serial.Serial(
            port=self.port, baudrate=self.baudrate, timeout=self.timeout
        )
        # one way to clear -> can also turn dtr on and off
        self.ser.reset_input_buffer()

        logger.info("Getting VALON device status string:")
        self.info = self.send_cmd("STAT")
        logger.info(self.info)

        super().__post_init__()

        if self.pwr_dbm is not None:
            self.set_power(self.pwr_dbm)

    def _send_cmd(self, command: str):
        """Send a command string to the Valon over serial.

        Appends carriage return. Returns any response.

        """
        self.ser.write((command + "\r").encode())
        time.sleep(0.1)
        response = b""

        while self.ser.in_waiting:
            response += self.ser.read(self.ser.in_waiting)

        return response.decode(errors="ignore")

    def send_cmd(self, command: str, retries: int = 3):
        """Send a command string to the Valon over serial.

        Appends carriage return. Returns any response.

        """
        for n in range(retries):
            try:
                logger.debug(f"Sending tuner command: {command}")
                response = self._send_cmd(command)
                logger.debug(response)
            except Exception as e:
                if n == (retries - 1):
                    raise e
                else:
                    logger.warning(f"Failed to send command: {command}", exc_info=True)
                    self._reset_serial_connection()
            else:
                break
        return response

    def set_freq(self, freq_mhz: float):
        """Set the output frequency of the synthesizer."""
        logger.info(f"Setting local oscillator frequency to {freq_mhz} MHz")
        cmd = f"F{freq_mhz}MHz"
        result = self.send_cmd(cmd)
        self.freq_mhz = freq_mhz
        return result

    def set_power(self, pwr_dbm: float):
        """Set output power level.

        Valid Range -50 - 20. Can be brought lower configuring extra settings in the
        Valon.

        """
        logger.info(f"Setting output power level to {pwr_dbm} dBm")
        cmd = f"PWR {pwr_dbm}"
        result = self.send_cmd(cmd)
        self.pwr_dbm = pwr_dbm
        return result

    def get_lock_status(self):
        """Return the status of the PLL lock condition from Main and Sub PLLs"""
        logger.info("Getting PLL lock condition from Main and Sub PLLs")
        cmd = "LK"
        result = self.send_cmd(cmd)
        return result


ValonTunerParams = dataclasses.make_dataclass(
    "ValonTunerParams",
    [(f.name, f.type, f) for f in dataclasses.fields(ValonTuner)]
    + [
        (
            "tuner_class",
            typing.ClassVar[TunerBase],
            dataclasses.field(default=ValonTuner),
        )
    ],
    bases=(TunerParamsBase,),
    kw_only=True,
)
