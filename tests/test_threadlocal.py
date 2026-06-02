import threading
from django.conf import global_settings
from nose.tools import eq_, ok_
import sys
if sys.version < '3':
    from mock import patch, Mock
else:
    from unittest.mock import patch, Mock

from django_elasticache.threadlocal import ThreadLocalElastiCache


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_has_thread_local(get_cluster_info):
    get_cluster_info.return_value = {'nodes': ['h1:p', 'h2:p']}
    backend = ThreadLocalElastiCache('h:0', {})
    # The thread-local container is what activates the per-thread code path in ElastiCache._cache; without it the lookup
    # falls back to the shared self.
    ok_(isinstance(backend._local, threading.local))
    container = getattr(backend, '_local', backend)
    ok_(container is backend._local)


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_client_is_per_thread(get_cluster_info):
    get_cluster_info.return_value = {'nodes': ['h1:p', 'h2:p']}
    backend = ThreadLocalElastiCache('h:0', {})
    # Each call returns a distinct client so we can tell threads apart.
    backend._lib.Client = Mock(side_effect=lambda *a, **k: Mock())

    clients = {}

    def grab(name):
        clients[name] = backend._cache

    main_client = backend._cache
    t1 = threading.Thread(target=grab, args=('t1',))
    t2 = threading.Thread(target=grab, args=('t2',))
    t1.start(); t1.join()
    t2.start(); t2.join()

    # Three different threads (main, t1, t2) each built their own client.
    ok_(clients['t1'] is not clients['t2'])
    ok_(clients['t1'] is not main_client)
    ok_(clients['t2'] is not main_client)
    eq_(backend._lib.Client.call_count, 3)


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_client_reused_within_thread(get_cluster_info):
    get_cluster_info.return_value = {'nodes': ['h1:p', 'h2:p']}
    backend = ThreadLocalElastiCache('h:0', {})
    backend._lib.Client = Mock(side_effect=lambda *a, **k: Mock())
    # Same thread reuses its cached client across calls.
    ok_(backend._cache is backend._cache)
    eq_(backend._lib.Client.call_count, 1)
