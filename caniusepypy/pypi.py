# Copyright 2014 Google Inc. All rights reserved.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import unicode_literals

import concurrent.futures
import contextlib
import json
import logging
import multiprocessing
import pkgutil
import re
try:
    import urllib.request as urllib_request
except ImportError:  #pragma: no cover
    import urllib2 as urllib_request
import xml.parsers.expat
try:
    import xmlrpc.client as xmlrpc_client
except ImportError:  #pragma: no cover
    import xmlrpclib as xmlrpc_client


try:
    CPU_COUNT = max(2, multiprocessing.cpu_count())
except NotImplementedError:  #pragma: no cover
    CPU_COUNT = 2

PROJECT_NAME = re.compile(r'[\w.-]+')


def just_name(supposed_name):
    """Strip off any versioning or restrictions metadata from a project name."""
    return PROJECT_NAME.match(supposed_name).group(0).lower()

@contextlib.contextmanager
def pypi_client():
    client = xmlrpc_client.ServerProxy('https://pypi.python.org/pypi')
    try:
        yield client
    finally:
        try:
            client('close')()
        except (xml.parsers.expat.ExpatError, xmlrpc_client.Fault):  #pragma: no cover
            # The close hack is not in Python 2.6.
            pass


def overrides():
    """Load a set containing projects who are missing the proper Python 3 classifier.

    Project names are always lowercased.

    """
    raw_bytes = pkgutil.get_data(__name__, 'overrides.json')
    return json.loads(raw_bytes.decode('utf-8'))


def pypy_classifiers():
    """Fetch the Python 3-related trove classifiers."""
    url = 'https://pypi.python.org/pypi?%3Aaction=list_classifiers'
    response = urllib_request.urlopen(url)
    try:
        try:
            status = response.status
        except AttributeError:  #pragma: no cover
            status = response.code
        if status != 200:  #pragma: no cover
            msg = 'PyPI responded with status {0} for {1}'.format(status, url)
            raise ValueError(msg)
        data = response.read()
    finally:
        response.close()
    classifiers = data.decode('utf-8').splitlines()
    base_classifier = 'Programming Language :: Python :: Implementation :: PyPy'
    return (classifier for classifier in classifiers
            if classifier.startswith(base_classifier))


def projects_matching_classifier(classifier):
    """Find all projects matching the specified trove classifier."""
    log = logging.getLogger('ciu')
    with pypi_client() as client:
        log.info('Fetching project list for {0!r}'.format(classifier))
        try:
            return frozenset(result[0].lower()
                             for result in client.browse([classifier]))
        except xml.parsers.expat.ExpatError:  #pragma: no cover
            # Python 2.6 doesn't like empty results.
            logging.getLogger('ciu').info("PyPI didn't return any results")
            return []


def all_projects():
    """Get the set of all projects on PyPI."""
    log = logging.getLogger('ciu')
    with pypi_client() as client:
        log.info('Fetching all project names from PyPI')
        return frozenset(name.lower() for name in client.list_packages())


def all_pypy_projects(manual_overrides=None):
    """Return the set of names of all projects ported to PyPy, lowercased."""
    log = logging.getLogger('ciu')
    projects = set()
    thread_pool_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=CPU_COUNT)
    with thread_pool_executor as executor:
        for result in map(projects_matching_classifier, pypy_classifiers()):
            projects.update(result)
    if manual_overrides is None:
        manual_overrides = overrides()
    stale_overrides = projects.intersection(manual_overrides)
    log.info('Adding {0} overrides:'.format(len(manual_overrides)))
    for override in sorted(manual_overrides):
        msg = override
        try:
            msg += ' ({0})'.format(manual_overrides[override])
        except TypeError:
            # No reason a set can't be used.
            pass
        log.info('    ' + msg)
    if stale_overrides:  #pragma: no cover
        log.warning('Stale overrides: {0}'.format(stale_overrides))
    projects.update(manual_overrides)
    return projects


def is_pure_python(dependency):
    with pypi_client() as client:
        releases = client.package_releases(dependency)

        if not releases:
            return False

        latest = releases[-1]
        downloads = client.release_urls(dependency, latest)

        for download in downloads:
            if (
                download['packagetype'] == "bdist_wheel" and
                (
                    "py2.py3-none-any" in download['url'] or
                    "py2.none-any" in download['url']
                )
            ):
                return True
        return False
