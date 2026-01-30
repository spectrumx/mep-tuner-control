# SPDX-FileCopyrightText: Copyright (c) 2026 University of Colorado
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
"""
tuner_base.py

Abstract base class for MEP tuner devices defining the standard tuner interface.

Author: nicholas.rainville@colorado.edu
"""

import dataclasses
import typing
from abc import ABC, abstractmethod


@dataclasses.dataclass(kw_only=True)
class MEPTuner(ABC):
    name: str = dataclasses.field(default="mep_tuner", init=False)
    ready: bool = dataclasses.field(default=True, init=False)
    freq_mhz: typing.Optional[float] = dataclasses.field(default=None, init=False)

    def __del__(self):
        """Destructor - can be overridden by child classes"""
        self.ready = False

    def __bool__(self):
        """Test if tuner is ready or available to be used"""
        return self.ready

    def reset_connection(self):
        """Reset tuner connection - may be implemented by child classes"""
        pass

    @abstractmethod
    def set_freq(self, freq_mhz):
        """Abstract method to set frequency - must be implemented by child classes"""
        self.freq_mhz = freq_mhz
