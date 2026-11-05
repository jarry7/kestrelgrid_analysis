import struct

import numpy as np
import pytest
from kestrelgrid.raw_reader.types import MAGIC_NUM


@pytest.fixture
def raw_bytes():
    def generate(start=1_700_000_000.0, count=8, wmu=4021):
        n, rate = 4000, 4
        samples = np.rint(1000 * np.sin(2 * np.pi * 60 * np.arange(n) / 4000)).astype("<i2")
        return b"".join(
            struct.pack("<hhhhddhhhhh54x", 88 + 2 * n, wmu, n, rate,
                        start + index, 0.1, 4, MAGIC_NUM, 0, 1, 1) + samples.tobytes()
            for index in range(count)
        )
    return generate
