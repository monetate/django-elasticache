"""
Backend for django cache.

``pylibmc.Client`` is not thread-safe: pylibmc releases the GIL during socket I/O, so two threads sharing one
client interleave reads and writes on the same connection and desync the protocol. ``ElastiCache`` addresses this
by keeping a bounded ``pylibmc.ClientPool`` per process and handing each operation its own client from the pool,
so connection count is ``POOL_SIZE * processes-per-box * boxes`` rather than one-per-thread.

Top-level configuration params (siblings of ``OPTIONS``, like ``DISCOVERY_TIMEOUT``):

- ``POOL_SIZE`` (int, default 8): number of pooled clients per process.
- ``POOL_TIMEOUT_MS`` (int, milliseconds, default 1000): how long to wait for a free client before raising
  ``QueueEmpty``. Callers such as ``KeyCheckingMemcache`` catch and handle this like any other cache error.

The pool is built lazily on first use (cluster discovery must happen first) under a lock so concurrent first
calls do not each build their own pool.
"""
import socket
import threading
from functools import wraps

import pylibmc
from django.core.cache import InvalidCacheBackendError
from django.core.cache.backends.memcached import PyLibMCCache
from .cluster_utils import get_cluster_info

# Number of pooled pylibmc.Client objects per backend instance per process.
DEFAULT_POOL_SIZE = 8

# On exhaustion raises QueueEmpty.
DEFAULT_POOL_TIMEOUT_MS = 1000


def _validate_positive_int(name, value):
    # Explicitly check bool since it is a subclass of int in Python.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidCacheBackendError("{0} must be a positive integer, got {1!r}".format(name, value))
    return int(value)


def invalidate_cache_after_error(f):
    """Catch system/network-level errors and invalidate the cluster node cache and client pool so both are rebuilt on
    the next call. Only errors that indicate a node is unreachable trigger invalidation. Transient errors (timeouts,
    dropped connections, protocol errors) and pool exhaustion propagate without clearing the node cache.
    """
    @wraps(f)
    def wrapper(self, *args, **kwds):
        try:
            return f(self, *args, **kwds)
        except (
            pylibmc.ConnectionError,
            pylibmc.HostLookupError,
            pylibmc.NoServers,
            pylibmc.ServerDead,
            pylibmc.ServerDown,
        ):
            self.clear_cluster_nodes_cache()
            raise
    return wrapper


class PooledClient(object):
    """Proxy that routes each cache operation through a pylibmc.ClientPool reservation.

    Django's BaseMemcachedCache calls operations directly on whatever ``_cache`` returns (e.g.
    ``self._cache.get(key)``). Returning this proxy makes every such call check a client out of the pool, run
    the operation, and return the client, so the number of live pylibmc.Client objects (open connections) is bounded by
    the pool size rather than by the worker thread count.
    """

    def __init__(self, pool, timeout=None):
        self.pool = pool
        self.timeout = timeout  # Seconds to wait for a free client before raising QueueEmpty.

    def __getattr__(self, name):
        def call(*args, **kwargs):
            # ClientPool is a Queue subclass. Call get()/put() directly rather than reserve() because it does not expose
            # a timeout parameter. Raises QueueEmpty on pool exhaustion; callers (e.g. KeyCheckingMemcache) handle it.
            mc = self.pool.get(block=True, timeout=self.timeout)
            try:
                return getattr(mc, name)(*args, **kwargs)
            finally:
                self.pool.put(mc)
        return call


class ElastiCache(PyLibMCCache):
    """
    backend for Amazon ElastiCache (memcached) with auto discovery mode
    it used pylibmc in binary mode
    """
    def __init__(self, server, params):
        super(ElastiCache, self).__init__(server, params)
        if len(self._servers) > 1:
            raise InvalidCacheBackendError(
                'ElastiCache should be configured with only one server '
                '(Configuration Endpoint)')
        if len(self._servers[0].split(':')) != 2:
            raise InvalidCacheBackendError(
                'Server configuration should be in format IP:port')

        # Django's pylibmc backend merges the 'behaviors' dict into the upper
        # level 'OPTIONS' dict but is rather indiscriminate in doing so - even
        # if you have other top-level keys in 'OPTIONS' they end up on the same
        # level as the keys you had inside params['OPTIONS']['behaviors'] so
        # now you can't tell whether the key came from 'behaviors' or was a top
        # level key in 'OPTIONS'. Work around all of this by just putting the
        # DISCOVERY_TIMEOUT key at the same level as 'OPTIONS'.
        self._discovery_timeout = params.get('DISCOVERY_TIMEOUT', None)
        self._ignore_cluster_errors = self._options.get(
            'IGNORE_CLUSTER_ERRORS', False)

        self.pool_size = _validate_positive_int("POOL_SIZE", params.get("POOL_SIZE", DEFAULT_POOL_SIZE))
        self.pool_timeout = _validate_positive_int(
            "POOL_TIMEOUT_MS", params.get("POOL_TIMEOUT_MS", DEFAULT_POOL_TIMEOUT_MS)
        ) / 1000.0  # Python's Queue.get(timeout) takes seconds.

        self._pool_lock = threading.Lock()
        self._pool_proxy = None

    def clear_cluster_nodes_cache(self):
        """Clear the cached cluster node list and drop the client pool so both are rebuilt on next use.

        Acquires _pool_lock so a concurrent _cache build that already fetched stale nodes cannot overwrite
        the cleared _pool_proxy after this method returns.
        """
        with self._pool_lock:
            if hasattr(self, '_cluster_nodes_cache'):
                del self._cluster_nodes_cache
            self._pool_proxy = None

    def get_cluster_nodes(self):
        """
        return list with all nodes in cluster
        """
        if not hasattr(self, '_cluster_nodes_cache'):
            server, port = self._servers[0].split(':')
            try:
                self._cluster_nodes_cache = (
                    get_cluster_info(server, port, self._discovery_timeout,
                                     self._ignore_cluster_errors)['nodes'])
            except (socket.gaierror, socket.timeout) as err:
                raise Exception('Cannot connect to cluster {0} ({1})'.format(
                    self._servers[0], err
                ))
        return self._cluster_nodes_cache

    @property
    def _cache(self):
        # _pool_proxy is safe to read without the lock, only written under _pool_lock during initialization.
        if self._pool_proxy is not None:
            return self._pool_proxy

        with self._pool_lock:
            # Re-check, another thread may have built the pool while waiting for the lock.
            if self._pool_proxy is not None:
                return self._pool_proxy

            master = self._lib.Client(self.get_cluster_nodes())
            if self._options:
                # In Django 1.11, all behaviors are shifted into a behaviors dict. Attempt to get from there, and fall
                # back to old behavior if the behaviors key does not exist.
                master.behaviors = self._options.get("behaviors", self._options)

            pool = self._lib.ClientPool(master, self.pool_size)
            self._pool_proxy = PooledClient(pool, self.pool_timeout)
            return self._pool_proxy

    @invalidate_cache_after_error
    def get(self, *args, **kwargs):
        return super(ElastiCache, self).get(*args, **kwargs)

    @invalidate_cache_after_error
    def get_many(self, *args, **kwargs):
        return super(ElastiCache, self).get_many(*args, **kwargs)

    @invalidate_cache_after_error
    def set(self, *args, **kwargs):
        return super(ElastiCache, self).set(*args, **kwargs)

    @invalidate_cache_after_error
    def set_many(self, *args, **kwargs):
        return super(ElastiCache, self).set_many(*args, **kwargs)

    @invalidate_cache_after_error
    def delete(self, *args, **kwargs):
        return super(ElastiCache, self).delete(*args, **kwargs)
