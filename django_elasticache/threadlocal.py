"""
Thread-local variant of the ElastiCache backend.

Why this exists
---------------
``pylibmc.Client`` (the libmemcached handle) is NOT safe to share between threads. pylibmc releases the GIL while it
does socket I/O, so two Python threads can be inside libmemcached on the *same* client/connection at the same time.
When that happens their reads and writes interleave on the same socket and the connection desynchronizes, producing
intermittent libmemcached errors such as:

* MEMCACHED_END (21)     - a reply terminator is read where a STORED was expected (one thread reads another thread's
                           response)
* MEMCACHED_ERRNO/EBADF  - an op runs against a socket another thread already closed (e.g. after a tight timeout tore
                           it down)
* MEMCACHED_TIMEOUT (31) - "No active_fd", the connection was reset out from under the op by a sibling thread

The base ``ElastiCache._cache`` already reads/writes the cached client through
``container = getattr(self, '_local', self)``, so if a ``self._local`` thread-local object is present the client is
stored per thread instead of once on the shared backend instance. That ``_local`` attribute used to be supplied by
Django's memcached base class, but Django stopped providing it in 1.7+, so on every supported Django version here the
lookup silently falls back to ``self`` and all worker threads share one client. This race condition occurs under the
threaded track server (CherryPy with a 50-thread pool) when aggressive socket timeouts are present.

This subclass reinstates ``self._local`` so the existing thread-local code path in ``ElastiCache._cache`` activates and
each thread lazily builds and reuses its own ``pylibmc.Client``. Cluster-node discovery results stay shared
on the instance (``_cluster_nodes_cache``), only the client connections become per thread. In a bounded thread pool the
number of clients is bounded by the pool size.
"""
import threading

from django_elasticache.memcached import ElastiCache


class ThreadLocalElastiCache(ElastiCache):
    """ElastiCache backend that keeps its `pylibmc.Client` in thread-local storage instead of sharing a single client
    across all threads.
    """

    def __init__(self, server, params):
        super(ThreadLocalElastiCache, self).__init__(server, params)
        # Presence of self._local is what activates the thread-local code path in ElastiCache._cache; each thread then
        # gets its own _client.
        self._local = threading.local()
