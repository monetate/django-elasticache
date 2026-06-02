"""
Backend for django cache
"""
import socket
import threading
from functools import wraps
from django.core.cache import InvalidCacheBackendError
from django.core.cache.backends.memcached import PyLibMCCache
from .cluster_utils import get_cluster_info


def invalidate_cache_after_error(f):
    """
    catch any exception and invalidate internal cache with list of nodes
    """
    @wraps(f)
    def wrapper(self, *args, **kwds):
        try:
            return f(self, *args, **kwds)
        except Exception:
            self.clear_cluster_nodes_cache()
            raise
    return wrapper


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

        # pylibmc.Client is NOT thread-safe: pylibmc releases the GIL during socket I/O, so two threads using the same
        # Client can interleave reads and writes on the same connection and desync the protocol (MEMCACHED_END where
        # STORED was expected, EBADF on a socket another thread tore down, MEMCACHED_TIMEOUT "No active_fd"). Store the
        # Client in a threading.local so each thread gets its own.
        self._local = threading.local()

    def clear_cluster_nodes_cache(self):
        """clear internal cache with list of nodes in cluster"""
        if hasattr(self, '_cluster_nodes_cache'):
            del self._cluster_nodes_cache

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
        # pylibmc.Client is cached per-thread on self._local to avoid the cross-thread protocol-desync race. Cluster
        # node discovery results stay shared on the instance via get_cluster_nodes(), only the client connections become
        # per thread.
        # See django-pylibmc: https://github.com/django-pylibmc/django-pylibmc/blob/master/django_pylibmc/memcached.py
        client = getattr(self._local, 'client', None)
        if client:
            return client

        client = self._lib.Client(self.get_cluster_nodes())
        if self._options:
            # In Django 1.11, all behaviors are shifted into a behaviors dict
            # Attempt to get from there, and fall back to old behavior if the behaviors
            # key does not exist
            client.behaviors = self._options.get('behaviors', self._options)

        self._local.client = client

        return client

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
