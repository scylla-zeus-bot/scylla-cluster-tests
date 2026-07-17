# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright (c) 2026 ScyllaDB

"""Unit tests for UpgradeBaseVersion.filter_rc_only_version's handling of unreachable
repository files (SCT-664): an S3 object archived to cold storage must be tolerated,
while any other fetch failure must still fail loudly."""

from unittest.mock import patch
from concurrent.futures import TimeoutError as FuturesTimeoutError

import pytest

from sdcm.utils.parallel_object import ParallelObjectResult, ParallelObjectException
from sdcm.utils.version_utils import RepositoryURLError
from utils.get_supported_scylla_base_versions import UpgradeBaseVersion

BASE_VERSION_LIST = ["6.0", "2024.1", "2024.2"]


def _make_version_detector():
    detector = UpgradeBaseVersion.__new__(UpgradeBaseVersion)
    detector.scylla_version = "2024.2"
    detector.repo_maps = {"6.0": "mock-url-6.0", "2024.1": "mock-url-2024.1", "2024.2": "mock-url-2024.2"}
    detector.base_version_all_sts_versions = False
    return detector


def test_invalid_object_state_on_parallel_fetch_is_tolerated():
    """A per-arch Packages fetch failing with S3's InvalidObjectState (object archived to
    cold storage) keeps the version in the base-version list instead of crashing."""
    exc = ParallelObjectException(
        results=[
            ParallelObjectResult(
                obj="binary-amd64",
                exc=RepositoryURLError("archived", status_code=403, s3_error_code="InvalidObjectState"),
            )
        ]
    )
    with patch("utils.get_supported_scylla_base_versions.get_all_versions", side_effect=exc):
        result = _make_version_detector().filter_rc_only_version(list(BASE_VERSION_LIST))

    assert set(result) == set(BASE_VERSION_LIST)


def test_invalid_object_state_on_bare_fetch_is_tolerated():
    """A bare RepositoryURLError (eg. the top-level .list/.repo file itself returns
    InvalidObjectState) is tolerated the same way as a wrapped per-arch failure."""
    exc = RepositoryURLError("archived", status_code=403, s3_error_code="InvalidObjectState")
    with patch("utils.get_supported_scylla_base_versions.get_all_versions", side_effect=exc):
        result = _make_version_detector().filter_rc_only_version(list(BASE_VERSION_LIST))

    assert set(result) == set(BASE_VERSION_LIST)


def test_no_such_key_re_raises():
    """A genuinely missing object (S3 NoSuchKey, HTTP 404) must not be silently treated as
    a confirmed release — it re-raises."""
    exc = RepositoryURLError("missing", status_code=404, s3_error_code="NoSuchKey")
    with patch("utils.get_supported_scylla_base_versions.get_all_versions", side_effect=exc):
        with pytest.raises(RepositoryURLError):
            _make_version_detector().filter_rc_only_version(list(BASE_VERSION_LIST))


def test_bare_403_without_invalid_object_state_re_raises():
    """A bare HTTP 403 without the InvalidObjectState error code (eg. AccessDenied from a
    broken bucket ACL/policy) must not be conflated with an archived object — it re-raises."""
    exc = RepositoryURLError("forbidden", status_code=403, s3_error_code="AccessDenied")
    with patch("utils.get_supported_scylla_base_versions.get_all_versions", side_effect=exc):
        with pytest.raises(RepositoryURLError):
            _make_version_detector().filter_rc_only_version(list(BASE_VERSION_LIST))


def test_mixed_parallel_failures_re_raise():
    """If the parallel fetch has both a tolerable InvalidObjectState failure and an
    unrelated timeout, the unexpected failure still re-raises rather than being masked."""
    exc = ParallelObjectException(
        results=[
            ParallelObjectResult(
                obj="binary-amd64",
                exc=RepositoryURLError("archived", status_code=403, s3_error_code="InvalidObjectState"),
            ),
            ParallelObjectResult(obj="binary-arm64", exc=FuturesTimeoutError()),
        ]
    )
    with patch("utils.get_supported_scylla_base_versions.get_all_versions", side_effect=exc):
        with pytest.raises(ParallelObjectException):
            _make_version_detector().filter_rc_only_version(list(BASE_VERSION_LIST))
