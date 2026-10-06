"""kombu's Redis transport, told which protocol to speak.

kombu builds the broker connection from a fixed set of options and passes no ``protocol`` to redis-py, so against redis-py 8 the
worker and every ``.delay()`` open with ``HELLO 3`` - which a Redis older than 6.0 answers with ``unknown command``. See
``app/redis_compat.py`` for the whole story. This is the supported extension point (a transport named in ``broker_transport``),
not a patch of kombu: the one thing it changes is a ``protocol`` entry in the connection parameters.
"""
from __future__ import annotations

from kombu.transport import redis as kombu_redis

from .. import redis_compat


class Channel(kombu_redis.Channel):
    def _connparams(self, asynchronous: bool = False) -> dict:
        params = super()._connparams(asynchronous=asynchronous)
        params.setdefault("protocol", redis_compat.protocol())
        return params


class Transport(kombu_redis.Transport):
    Channel = Channel
