import typing
import os
import random
from conjecture.testdata import TestData, Status
from conjecture.errors import StopTest


class StopShrinking(Exception):
    pass


DEBUG = os.getenv('CONJECTURE_DEBUG') == "true"


class Settings(object):
    def __init__(
        self, buffer_size=8 * 1024, mutations=50, generations=100,
        max_shrinks=2000,
    ):
        self.buffer_size = buffer_size
        self.mutations = mutations
        self.generations = generations
        self.max_shrinks = max_shrinks


class TestRunner(object):
    def __init__(
        self,
        test_function: typing.Callable[[TestData], type(None)],
        settings: typing.Optional[Settings]=None,
    ):
        self._test_function = test_function
        self.settings = settings or Settings()
        self.last_data = None
        self.changed = 0
        self.shrinks = 0
        self.fill_size = min(8, self.settings.buffer_size)

    def new_buffer(self):
        buffer = os.urandom(self.fill_size) + bytes(
            self.settings.buffer_size - self.fill_size)
        self.last_data = TestData(buffer)
        self.test_function(self.last_data)
        self.last_data.freeze()
        self.update_fill_size()

    def update_fill_size(self):
        self.fill_size = min(
            max(self.fill_size, self.last_data.index * 2),
            self.settings.buffer_size
        )

    def test_function(self, data):
        try:
            self._test_function(data)
        except StopTest:
            pass

    def consider_new_test_data(
        self, data: TestData
    ) -> bool:
        # Transition rules:
        #   1. Transition cannot decrease the status
        #   2. Any transition which increases the status is valid
        #   3. If the previous status was interesting, only shrinking
        #      transitions are allowed.
        if self.last_data.status < data.status:
            return True
        if self.last_data.status > data.status:
            return False
        if data.status == Status.INVALID:
            return data.index >= self.last_data.index
        if data.status == Status.OVERRUN:
            return data.index <= self.last_data.index
        if data.status == Status.INTERESTING:
            assert len(data.buffer) <= len(self.last_data.buffer)
            return interest_key(data) < interest_key(self.last_data)
        return True

    def incorporate_new_buffer(
        self, buffer: bytes
    ) -> bool:
        if buffer == self.last_data.buffer:
            return False
        data = TestData(buffer)
        self.test_function(data)
        data.freeze()
        if self.consider_new_test_data(data):
            if self.last_data.status == Status.INTERESTING:
                self.shrinks += 1
            self.last_data = data
            self.update_fill_size()
            self.changed += 1
            if self.shrinks >= self.settings.max_shrinks:
                raise StopShrinking()
            return True
        return False

    def run(self):
        try:
            self._run()
        except StopShrinking:
            pass

    def _run(self):
        self.new_buffer()
        mutations = 0
        generation = 0
        while self.last_data.status != Status.INTERESTING:
            if mutations >= self.settings.mutations:
                generation += 1
                if generation >= self.settings.generations:
                    return
                mutations = 0
                self.incorporate_new_buffer(
                    mutate_data_to_new_buffer(self.last_data)
                )
            else:
                self.new_buffer()
            mutations += 1

        initial_changes = self.changed
        change_counter = -1
        while (
            initial_changes + self.settings.max_shrinks >=
            self.changed > change_counter
        ):
            assert self.last_data.status == Status.INTERESTING
            change_counter = self.changed
            interval_change_counter = -1
            while self.changed > interval_change_counter:
                interval_change_counter = self.changed
                i = 0
                while i < len(self.last_data.intervals):
                    u, v = self.last_data.intervals[i]
                    if not self.incorporate_new_buffer(
                        self.last_data.buffer[:u] +
                        self.last_data.buffer[v:]
                    ):
                        i += 1
            i = 0
            while i < len(self.last_data.intervals):
                u, v = self.last_data.intervals[i]
                self.incorporate_new_buffer(
                    self.last_data.buffer[:u] +
                    bytes(sorted(self.last_data.buffer[u:v])) +
                    self.last_data.buffer[v:]
                )
                i += 1
            k = 8
            for i in range(len(self.last_data.buffer) - k):
                buf = self.last_data.buffer
                if i + k > len(buf):
                    break
                self.incorporate_new_buffer(
                    buf[:i] + bytes(k) + buf[i + k:]
                )
            i = 0
            while i < len(self.last_data.buffer):
                buf = self.last_data.buffer
                if not self.incorporate_new_buffer(
                    buf[:i] + buf[i+1:]
                ):
                    for c in range(buf[i]):
                        if self.incorporate_new_buffer(
                            buf[:i] + bytes([c]) + buf[i+1:]
                        ):
                            break
                        elif self.incorporate_new_buffer(
                            buf[:i] + bytes([c]) + os.urandom(
                                len(buf) - i - 1
                            )
                        ):
                            break
                i += 1
            i = 0
            while i + 1 < len(self.last_data.buffer):
                j = i + 1
                buf = self.last_data.buffer
                if buf[i] > buf[j]:
                    self.incorporate_new_buffer(
                        buf[:i] + bytes([buf[j], buf[i]]) + buf[j + 1:]
                    )
                i += 1
            if self.changed > change_counter:
                continue
            i = 0
            while i < len(self.last_data.buffer):
                buf = self.last_data.buffer
                if not self.incorporate_new_buffer(
                    buf[:i] + buf[i+1:]
                ):
                    if buf[i] == 0:
                        j = i
                        while j > 0:
                            if buf[j] > 0:
                                self.incorporate_new_buffer(
                                    buf[:j] + bytes([buf[j] - 1]) +
                                    bytes([255]) * (i - j) +
                                    buf[i+1:]
                                )
                                break
                            j -= 1
                i += 1
            if self.changed > change_counter:
                continue
            buckets = [[] for _ in range(256)]
            for i, c in enumerate(self.last_data.buffer):
                buckets[c].append(i)
            indices = []
            for bucket in buckets:
                if len(bucket) > 1:
                    indices.extend(
                        (j, k)
                        for j in bucket for k in bucket
                        if j < k
                    )
            for j, k in indices:
                buf = self.last_data.buffer
                if k >= len(buf):
                    continue
                if buf[j] == buf[k]:
                    c = buf[j]
                    if c == 0:
                        if j > 0 and buf[j - 1] > 0 and buf[k - 1] > 0:
                            self.incorporate_new_buffer(
                                buf[:j - 1] +
                                bytes([buf[j - 1] - 1, 255]) +
                                buf[j+1:k-1] +
                                bytes([buf[k - 1] - 1, 255]) +
                                buf[k+1:]
                            )
                    c = buf[j]
                    if c > 0:
                        bd = bytes([c - 1])
                        if self.incorporate_new_buffer(
                            buf[:j] + bd + buf[j+1:k] + bd +
                            buf[k+1:]
                        ):
                            for d in range(c - 1):
                                buf = self.last_data.buffer
                                bd = bytes([d])
                                self.incorporate_new_buffer(
                                    buf[:j] + bd + buf[j+1:k] + bd +
                                    buf[k+1:]
                                )
            if self.changed > change_counter:
                continue
            buf = self.last_data.buffer
            for j in range(len(buf)):
                buf = self.last_data.buffer
                if j >= len(buf):
                    break
                if buf[j] == 0:
                    continue
                for k in range(j + 1, len(buf)):
                    buf = self.last_data.buffer
                    if k >= len(buf):
                        break
                    if buf[j] > buf[k]:
                        self.incorporate_new_buffer(
                            buf[:j] + bytes([buf[k]]) + buf[j+1:k] +
                            bytes([buf[j]]) + buf[k+1:]
                        )
                    buf = self.last_data.buffer
                    if k >= len(buf):
                        break
                    if buf[j] > 0 and buf[k] > 0:
                        self.incorporate_new_buffer(
                            buf[:j] + bytes([buf[j] - 1]) + buf[j+1:k] +
                            bytes([buf[k] - 1]) + buf[k+1:]
                        )


def find_interesting_buffer(test_function, settings=None):
    runner = TestRunner(test_function, settings)
    runner.run()
    if runner.last_data.status == Status.INTERESTING:
        return runner.last_data.buffer


def interest_key(data):
    buf = data.buffer
    return (
        data.cost, len(data.intervals), len(buf), buf
    )


def mutate_data_to_new_buffer(data):
    n = min(len(data.buffer), data.index)
    if not n:
        return b''
    if n == 1:
        return os.urandom(1)

    if data.status == Status.OVERRUN:
        result = bytearray(data.buffer)
        for i, c in enumerate(data.buffer):
            t = random.randint(0, 2)
            if t == 0:
                result[i] = 0
            elif t == 1:
                result[i] = random.randint(0, c)
            else:
                result[i] = c

    probe = os.urandom(1)[0]
    if probe <= 100 or len(data.intervals) <= 1:
        if random.randint(0, 1) or len(data.intervals) <= 1:
            u = random.randint(0, len(data.buffer) - 2)
            v = random.randint(u + 1, len(data.buffer) - 1)
        else:
            u, v = random.choice(data.intervals)
        c = random.randint(0, 2)
        if c == 0:
            replace = b'\0' * (v - u)
        elif c == 1:
            replace = bytes([255]) * (v - u)
        else:
            replace = os.urandom(v - u)
        return data.buffer[:u] + replace + data.buffer[v:]
    else:
        int1 = None
        int2 = None
        while int1 == int2:
            i = random.randint(0, len(data.intervals) - 2)
            int1 = data.intervals[i]
            int2 = data.intervals[
                random.randint(i + 1, len(data.intervals) - 1)]
        return data.buffer[:int1[0]] + data.buffer[int2[0]:int2[1]] + \
            data.buffer[int1[1]:]
