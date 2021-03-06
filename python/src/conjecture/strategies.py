from functools import wraps
import inspect


class Strategy(object):
    def __init__(self, draw, repr=None):
        self._draw = draw
        self._repr = repr

    def __repr__(self):
        if self._repr is None:
            return "strategy(%r)" % (self.draw.__qualname__,)
        if inspect.isfunction(self._repr):
            self._repr = self._repr()
        return self._repr

    def draw(self, data):
        data.start_example()
        result = self._draw(data)
        data.stop_example()
        return result

    def __call__(self, data):
        return self.draw(data)

    def map(self, f):
        return mapped(self, f)

    def filter(self, f):
        return filtered(self, f)

    def flatmap(self, f):
        return flatmapped(self, f)

    def __or__(self, other):
        return union(self, other)


def strategy(f):
    @wraps(f)
    def accept(*args, **kwargs):
        def _r():
            bits = list(map(repr, args))
            for k in sorted(kwargs.keys()):
                bits.append("%s=%r" % (k, kwargs[k]))
            return "%s(%s)" % (f.__qualname__, ', '.join(bits))
        return Strategy(
            lambda draw: f(*([draw] + list(args)), **kwargs),
            _r
        )
    accept.base = f
    return accept


@strategy
def mapped(data, strategy, f):
    return f(strategy.draw(data))


@strategy
def filtered(data, strategy, f):
    i = 0
    while True:
        i += 1
        ix = data.index
        result = strategy.draw(data)
        if f(result):
            return result
        if data.index == ix:
            data.mark_invalid()
            assert False


@strategy
def flatmapped(data, strategy, f):
    return f(strategy.draw(data)).draw(data)


@strategy
def n_byte_unsigned(data, n):
    return int.from_bytes(data.draw_bytes(n), 'big')


@strategy
def n_byte_signed(data, n):
    return int.from_bytes(
        data.draw_bytes(n), 'big', signed=True)


def saturate(n):
    bits = n.bit_length()
    k = 1
    while k < bits:
        n |= (n >> k)
        k *= 2
    return n


@strategy
def byte(data):
    return n_byte_unsigned.base(data, 1)


@strategy
def booleans(data):
    return bool(byte.base(data) % 2)


class UnionStrategy(Strategy):
    def __init__(self, strategies):
        for s in strategies:
            assert isinstance(s, Strategy)
        if not strategies:
            raise ValueError("Union of empty list of strategies")
        self.strategies = strategies

    def __repr__(self):
        return ' | '.join(map(repr, self.strategies))

    def draw(self, data):
        data.start_example()
        i = integer_range.base(data, 0, len(self.strategies) - 1)
        result = self.strategies[i].draw(data)
        data.stop_example()
        return result


def union(*args):
    bits = []
    for a in args:
        if isinstance(a, UnionStrategy):
            bits.extend(a.strategies)
        else:
            assert isinstance(a, Strategy)
            bits.append(a)
    return UnionStrategy(bits)


integers = union(*[n_byte_signed(n) for n in range(1, 9)])


@strategy
def lists(data, draw_element):
    result = []
    stopping_value = 50
    data.start_example()
    while True:
        data.start_example()
        if byte.base(data) <= stopping_value:
            data.stop_example()
            break
        element = draw_element(data)
        data.stop_example()
        result.append(element)
    data.stop_example()
    return result


@strategy
def integer_range(data, lower, upper):
    assert lower <= upper
    if lower == upper:
        return lower
    gap = upper - lower
    bits = gap.bit_length()
    nbytes = bits // 8 + int(bits % 8 != 0)
    mask = saturate(gap)
    while True:
        probe = n_byte_unsigned.base(data, nbytes) & mask
        if probe <= gap:
            return lower + probe


@strategy
def just(data, value):
    return value


@strategy
def fractional_float(data):
    a = n_byte_unsigned.base(data, 8)
    if a == 0:
        return 0.0
    b = integer_range.base(data, 0, a)
    return b / a


NASTY_FLOATS = [
    0.0, 0.5, 1.0 / 3, 10e6, 10e-6, 1.175494351e-38, 2.2250738585072014e-308,
    1.7976931348623157e+308, 3.402823466e+38, 9007199254740992, 1 - 10e-6,
    1 + 10e-6, 1.192092896e-07, 2.2204460492503131e-016,
    float('inf'), float('nan'),
]
NASTY_FLOATS.extend([-x for x in NASTY_FLOATS])
assert len(NASTY_FLOATS) == 32


@strategy
def floats(data):
    # Note: We always draw both parts of the float. This is important because
    # it means we can consistently simplify nasty floats into nice floats.
    # Otherwise this might cause us to run out of data.
    b = byte.base(data)
    integral_part = integers(data)
    fractional_part = fractional_float.base(data)
    if b == 0:
        return 0.0
    if b == 1:
        return float(integral_part)
    branch = 255 - b
    if branch < 32:
        data.incur_cost(1)
        return NASTY_FLOATS[31 - branch & 31]
    else:
        return float(integral_part) + fractional_part


@strategy
def tuples(data, *args):
    return tuple(a(data) for a in args)
