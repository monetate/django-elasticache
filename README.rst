Amazon ElastiCache backend for Django
=====================================

Simple Django cache backend for Amazon ElastiCache (memcached based). It uses
`pylibmc <http://github.com/lericson/pylibmc>`_ and sets up a connection to each
node in the cluster using
`auto discovery <http://docs.aws.amazon.com/AmazonElastiCache/latest/UserGuide/AutoDiscovery.html>`_.


Requirements
------------

* pylibmc
* Django 1.5+.

It was written and tested on Python 2.7 and 3.7.

Installation
------------

Get it from `pypi <http://pypi.python.org/pypi/django-elasticache>`_::

    pip install django-elasticache

or `github <http://github.com/gusdan/django-elasticache>`_::

    pip install -e git://github.com/gusdan/django-elasticache.git#egg=django-elasticache


Usage
-----

Your cache backend should look something like this::

    CACHES = {
        'default': {
            'BACKEND': 'django_elasticache.memcached.ElastiCache',
            'LOCATION': 'cache-c.draaaf.cfg.use1.cache.amazonaws.com:11211',
            'OPTIONS': {
                'IGNORE_CLUSTER_ERRORS': [True,False],
                'behaviors': {  # pylibmc behaviors, passed to underlying client
                    'ketama': True,
                    'receive_timeout': 50,  # milliseconds, socket timeout for memcached reads
                    'send_timeout': 50,  # milliseconds, socket timeout for memcached writes
                },
            },
            'DISCOVERY_TIMEOUT': 0.1,  # seconds, Elasticache discovery connection timeout
            'POOL_SIZE': 8,  # pylibmc clients pooled per process; see Thread safety
            'TIMEOUT': 600,  # seconds, default memcached key expiration time if not specified in set()
        }
    }

By the first call to cache it connects to cluster (using ``LOCATION`` param),
gets list of all nodes and setup pylibmc client using full
list of nodes. As result your cache will work with all nodes in cluster and
automatically detect new nodes in cluster. List of nodes are stored in class-level
cached, so any changes in cluster take affect only after restart of working process.
But if you're using gunicorn or mod_wsgi you usually have max_request settings which
restart process after some count of processed requests, so auto discovery will work
fine.

The ``IGNORE_CLUSTER_ERRORS`` option is useful when ``LOCATION`` doesn't have support
for ``config get cluster``. When set to ``True``, and ``config get cluster`` fails,
it returns a list of a single node with the same endpoint supplied to ``LOCATION``.

DISCOVERY_TIMEOUT controls how long to wait for response to any command during the
discovery sequence, including initial connection and any subsequent commands. This is
passed to the underlying socket used in the Telnet connection for communicating with
the ElastiCache cluster. Measured in seconds.

Django-elasticache does not change default pylibmc params. The user should set
performance-related params in the cache configuration.

Thread safety
-------------

``pylibmc`` releases the GIL during socket I/O, so a single ``pylibmc.Client``
shared across threads can have its reads and writes interleave on the same
connection, causing intermittent libmemcached errors (protocol desync /
``MEMCACHED_END``, ``EBADF``, "No active_fd" timeouts). A ``pylibmc.Client`` is
not safe to share between threads.

``ElastiCache`` keeps a bounded ``pylibmc.ClientPool`` per process and reserves
a client from it for each cache operation, so no client is used by two threads
at once. Open connections per cluster node are therefore ``POOL_SIZE *
processes-per-box * boxes``, bounded by the pool size rather than the worker
thread count. Cluster-node discovery results stay shared on the instance; only
the client connections are pooled.

``POOL_SIZE`` and ``POOL_TIMEOUT_MS`` are top-level cache options (siblings of
``OPTIONS``, like ``DISCOVERY_TIMEOUT``). ``POOL_SIZE`` defaults to 8.
``POOL_TIMEOUT_MS`` (milliseconds) defaults to 1000 and controls how long an
operation waits for a free client before raising ``Queue.Empty``
(``queue.Empty`` in python 3). The wait is NOT bounded by the pylibmc
``connect_timeout`` / ``send_timeout`` / ``receive_timeout`` behaviors as those
only apply during socket I/O after a client is obtained. Both must be plain
positive integers.

Another solutions
-----------------

ElastiCache provides memcached interface so there are three solution of using it:

1. Memcached configured with location = Configuration Endpoint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In this case your application
will randomly connect to nodes in cluster and cache will be used with not optimal
way. At some moment you will be connected to first node and set item. Minute later
you will be connected to another node and will not able to get this item.

 ::

    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.memcached.PyLibMCCache',
            'LOCATION': 'cache.gasdbp.cfg.use1.cache.amazonaws.com:11211',
        }
    }


2. Memcached configured with all nodes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It will work fine, memcache client will
separate items between all nodes and will balance loading on client side. You will
have problems only after adding new nodes or delete old nodes. In this case you should
add new nodes manually and don't forget update your app after all changes on AWS.

 ::

    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.memcached.PyLibMCCache',
            'LOCATION': [
                'cache.gqasdbp.0001.use1.cache.amazonaws.com:11211',
                'cache.gqasdbp.0002.use1.cache.amazonaws.com:11211',
            ]
        }
    }


3. Use django-elasticache
~~~~~~~~~~~~~~~~~~~~~~~~~

It will connect to cluster and retrieve ip addresses
of all nodes and configure memcached to use all nodes.

 ::

    CACHES = {
        'default': {
            'BACKEND': 'django_elasticache.memcached.ElastiCache',
            'LOCATION': 'cache-c.draaaf.cfg.use1.cache.amazonaws.com:11211',
        }
    }


Difference between setup with nodes list (django-elasticache) and
connection to only one configuration Endpoint (using dns routing) you can see on
this graph:

.. image:: https://raw.github.com/gusdan/django-elasticache/master/docs/images/get%20operation%20in%20cluster.png

Testing
-------

Install the test dependencies and run the tests like this::

    pip install -e '.[test]'
    pytest

To run the suite across the supported Python versions (2.7 and 3.7), use tox::

    tox

These steps are also wrapped in the Makefile as ``make install``, ``make test``, and ``make tox``.
