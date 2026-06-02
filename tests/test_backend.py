import threading
from django.conf import global_settings
from nose.tools import eq_, raises
import sys
if sys.version < '3':
    from mock import patch, Mock
else:
    from unittest.mock import patch, Mock

from django_elasticache.memcached import ElastiCache


@raises(Exception)
@patch('django.conf.settings', global_settings)
def test_wrong_params():
    ElastiCache('qew', {})


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_split_servers(get_cluster_info):
    backend = ElastiCache('h:0', {})
    servers = ['h1:p', 'h2:p']
    get_cluster_info.return_value = {
        'nodes': servers
    }
    backend._lib.Client = Mock()
    assert backend._cache
    get_cluster_info.assert_called_once_with('h', '0', None, False)
    backend._lib.Client.assert_called_once_with(servers)


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_node_info_cache(get_cluster_info):
    servers = ['h1:p', 'h2:p']
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiCache('h:0', {})
    backend._lib.Client = Mock()
    backend.set('key1', 'val')
    backend.get('key1')
    backend.set('key2', 'val')
    backend.get('key2')
    backend._lib.Client.assert_called_once_with(servers)
    eq_(backend._cache.get.call_count, 2)
    eq_(backend._cache.set.call_count, 2)

    get_cluster_info.assert_called_once_with('h', '0', None, False)


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_invalidate_cache(get_cluster_info):
    servers = ['h1:p', 'h2:p']
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiCache('h:0', {})
    backend._lib.Client = Mock()
    assert backend._cache
    backend._cache.get = Mock()
    backend._cache.get.side_effect = Exception()
    try:
        backend.get('key1', 'val')
    except Exception:
        pass
    #  invalidate cached client
    backend._local.client = None
    try:
        backend.get('key1', 'val')
    except Exception:
        pass
    eq_(backend._cache.get.call_count, 2)
    eq_(get_cluster_info.call_count, 2)


@patch("django.conf.settings", global_settings)
@patch("django_elasticache.memcached.get_cluster_info")
def test_client_is_per_thread(get_cluster_info):
    get_cluster_info.return_value = {"nodes": ["h1:p", "h2:p"]}
    backend = ElastiCache("h:0", {})
    # Each call to the Client constructor returns a distinct mock so we can tell threads apart.
    backend._lib.Client = Mock(side_effect=lambda *a, **k: Mock())

    clients = {}

    def grab(name):
        clients[name] = backend._cache

    main_client = backend._cache
    t1 = threading.Thread(target=grab, args=("t1",))
    t2 = threading.Thread(target=grab, args=("t2",))
    t1.start()
    t1.join()
    t2.start()
    t2.join()

    # main, t1, t2 each built their own client.
    assert clients["t1"] is not clients["t2"]
    assert clients["t1"] is not main_client
    assert clients["t2"] is not main_client
    eq_(backend._lib.Client.call_count, 3)
