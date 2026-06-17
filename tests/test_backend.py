import threading
from django.conf import global_settings
from nose.tools import eq_, raises
import sys
if sys.version < '3':
    from mock import patch, Mock
    from Queue import Empty as QueueEmpty
else:
    from queue import Empty as QueueEmpty
    from unittest.mock import patch, Mock

import pylibmc
from django_elasticache.memcached import ElastiCache
from django.core.cache import InvalidCacheBackendError


@raises(Exception)
@patch('django.conf.settings', global_settings)
def test_wrong_params():
    ElastiCache('qew', {})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_size_string():
    ElastiCache('h:0', {'POOL_SIZE': 'not-a-number'})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_size_zero():
    ElastiCache('h:0', {'POOL_SIZE': 0})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_size_float():
    ElastiCache('h:0', {'POOL_SIZE': 3.7})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_size_bool():
    ElastiCache('h:0', {'POOL_SIZE': True})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_timeout_negative():
    ElastiCache('h:0', {'POOL_TIMEOUT_MS': -1})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_timeout_zero():
    ElastiCache('h:0', {'POOL_TIMEOUT_MS': 0})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_timeout_float():
    ElastiCache('h:0', {'POOL_TIMEOUT_MS': 1.5})


@raises(InvalidCacheBackendError)
@patch('django.conf.settings', global_settings)
def test_invalid_pool_timeout_bool():
    ElastiCache('h:0', {'POOL_TIMEOUT_MS': True})


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_pool_exhaustion_propagates_without_invalidating(get_cluster_info):
    # Pool exhaustion surfaces as QueueEmpty, which is not a pylibmc error, so it propagates without rediscovery.
    get_cluster_info.return_value = {'nodes': ['h:p']}
    backend = ElastiCache('h:0', {})
    backend._lib.Client = Mock()
    mock_pool = Mock()
    mock_pool.get.side_effect = QueueEmpty()
    backend._lib.ClientPool = Mock(return_value=mock_pool)

    raised = 0
    for _ in range(2):
        try:
            backend.get('k')
        except QueueEmpty:
            raised += 1
    # QueueEmpty reaches the caller untouched, and the node cache survives so the second op does not rediscover.
    eq_(raised, 2)
    eq_(get_cluster_info.call_count, 1)


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_pool_timeout_passed_to_get(get_cluster_info):
    # POOL_TIMEOUT_MS=500ms should reach pool.get() as timeout=0.5 seconds.
    get_cluster_info.return_value = {'nodes': ['h:p']}
    backend = ElastiCache('h:0', {'POOL_TIMEOUT_MS': 500})
    mock_client = Mock()
    backend._lib.Client = Mock()
    mock_pool = Mock()
    mock_pool.get.return_value = mock_client
    backend._lib.ClientPool = Mock(return_value=mock_pool)
    backend.get('k')
    mock_pool.get.assert_called_once_with(block=True, timeout=0.5)


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
    mock_client = Mock()
    backend._lib.Client = Mock()
    mock_pool = Mock()
    mock_pool.get.return_value = mock_client
    backend._lib.ClientPool = Mock(return_value=mock_pool)

    backend.set('key1', 'val')
    backend.get('key1')
    backend.set('key2', 'val')
    backend.get('key2')

    # The cluster is discovered once and the master client is built once.
    get_cluster_info.assert_called_once_with('h', '0', None, False)
    backend._lib.Client.assert_called_once_with(servers)

    # Every op checks out a client via pool.get() and returns it via pool.put().
    eq_(mock_pool.get.call_count, 4)
    eq_(mock_pool.put.call_count, 4)
    eq_(mock_client.get.call_count, 2)
    eq_(mock_client.set.call_count, 2)


# The errors invalidate_cache_after_error should treat as system/network-level failures worth rediscovering on.
INVALIDATING_ERRORS = [
    pylibmc.ConnectionError,
    pylibmc.HostLookupError,
    pylibmc.NoServers,
    pylibmc.ServerDead,
    pylibmc.ServerDown,
]


def test_invalidate_cache():
    # nose generator: one case per caught error. @patch lives on the check, not here, so it is active per case.
    for error in INVALIDATING_ERRORS:
        yield check_invalidate_cache_on_error, error


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def check_invalidate_cache_on_error(error, get_cluster_info):
    get_cluster_info.return_value = {'nodes': ['h1:p', 'h2:p']}

    backend = ElastiCache('h:0', {})
    mock_client = Mock()
    mock_client.get.side_effect = error()
    backend._lib.Client = Mock()
    mock_pool = Mock()
    mock_pool.get.return_value = mock_client
    # return_value so both pool builds (before and after the error-triggered clear) get the same mock_pool.
    backend._lib.ClientPool = Mock(return_value=mock_pool)

    # Each call raises the caught error to the caller and drops the node cache, so the next op rediscovers.
    raised = 0
    for _ in range(2):
        try:
            backend.get('key1', 'val')
        except error:
            raised += 1
    eq_(raised, 2)
    eq_(mock_client.get.call_count, 2)
    eq_(get_cluster_info.call_count, 2)


@patch('django.conf.settings', global_settings)
@patch('django_elasticache.memcached.get_cluster_info')
def test_transient_error_does_not_invalidate_cache(get_cluster_info):
    # A transient error (e.g. a timeout surfacing as the base pylibmc.Error) must not trigger rediscovery.
    get_cluster_info.return_value = {'nodes': ['h1:p', 'h2:p']}

    backend = ElastiCache('h:0', {})
    mock_client = Mock()
    mock_client.get.side_effect = pylibmc.Error()
    backend._lib.Client = Mock()
    mock_pool = Mock()
    mock_pool.get.return_value = mock_client
    backend._lib.ClientPool = Mock(return_value=mock_pool)

    # The transient error reaches the caller but the node cache and pool are preserved, so the next op reuses them.
    raised = 0
    for _ in range(2):
        try:
            backend.get('key1', 'val')
        except pylibmc.Error:
            raised += 1
    eq_(raised, 2)
    eq_(mock_client.get.call_count, 2)
    eq_(get_cluster_info.call_count, 1)


@patch("django.conf.settings", global_settings)
@patch("django_elasticache.memcached.get_cluster_info")
def test_pool_is_bounded(get_cluster_info):
    get_cluster_info.return_value = {"nodes": ["h1:p", "h2:p"]}
    backend = ElastiCache("h:0", {"POOL_SIZE": 3})
    backend._lib.Client = Mock()

    # Mock ClientPool so the test stays isolated from pylibmc internals (e.g. how/when fill() clones clients).
    mock_pool = Mock()
    mock_pool.get.return_value = backend._lib.Client.return_value
    backend._lib.ClientPool = Mock(return_value=mock_pool)

    for _ in range(40):
        backend.get("k")

    # Pool is constructed with the master client and the configured POOL_SIZE.
    backend._lib.ClientPool.assert_called_once_with(backend._lib.Client.return_value, 3)
    # Every op checks out via pool.get() and returns via pool.put().
    eq_(mock_pool.get.call_count, 40)
    eq_(mock_pool.put.call_count, 40)


@patch("django.conf.settings", global_settings)
@patch("django_elasticache.memcached.get_cluster_info")
def test_pool_built_once_under_concurrent_first_use(get_cluster_info):
    # Verify that two threads racing on the first _cache access both pass the outer
    # `if self._pool_proxy is not None` check, but only one builds the pool.
    get_cluster_info.return_value = {"nodes": ["h1:p", "h2:p"]}
    backend = ElastiCache("h:0", {})
    backend._lib.Client = Mock()

    # Count how many times ClientPools are constructed using a thread-safe counter.
    pool_build_count = [0]  # Using list for nonlocal mutability in Python 2.
    lock = threading.Lock()

    def make_pool(*args, **kwargs):
        with lock:
            pool_build_count[0] += 1
        pool = Mock()
        pool.get.return_value = Mock()
        return pool

    backend._lib.ClientPool = Mock(side_effect=make_pool)

    # Use an Event to hold both threads at the starting line so they hit _cache simultaneously.
    start = threading.Event()

    def access_cache():
        start.wait()
        backend._cache  # noqa: B018

    threads = [threading.Thread(target=access_cache) for _ in range(8)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    # Despite 8 threads racing, the pool must be built exactly once.
    eq_(pool_build_count[0], 1)
