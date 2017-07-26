# -*- coding: utf-8 -*-
# Copyright (C) Duncan Macleod (2014)
#
# This file is part of GWpy.
#
# GWpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GWpy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GWpy.  If not, see <http://www.gnu.org/licenses/>.

"""Utilties for the GWpy test suite
"""

import os.path
import tempfile
from importlib import import_module

import numpy
from numpy.testing import (assert_array_equal, assert_allclose)

import pytest


# -- dependencies -------------------------------------------------------------

def has(module):
    """Test whether a module is available

    Returns `True` if `import module` succeeded, otherwise `False`
    """
    try:
        import_module(module)
    except ImportError:
        return False
    else:
        return True


def skip_missing_dependency(module):
    """Returns a mark generator to skip a test if the dependency is missing
    """
    return pytest.mark.skipif(not has(module),
                              reason='No module named %s' % module)

# -- assertions ---------------------------------------------------------------

def assert_quantity_equal(q1, q2):
    """Assert that two `~astropy.units.Quantity` objects are the same
    """
    _assert_quantity(q1, q2, array_assertion=assert_array_equal)


def assert_quantity_almost_equal(q1, q2):
    """Assert that two `~astropy.units.Quantity` objects are almost the same

    This method asserts that the units are the same and that the values are
    equal within precision.
    """
    _assert_quantity(q1, q2, array_assertion=assert_allclose)


def _assert_quantity(q1, q2, array_assertion=assert_array_equal):
    assert q1.unit == q2.unit, "%r != %r" % (q1.unit, q2.unit)
    array_assertion(q1.value, q2.value)


def assert_quantity_sub_equal(a, b, *attrs, **kwargs):
    """Assert that two `~gwpy.types.Array` objects are the same (or almost)

    Parameters
    ----------
    a, b : `~gwpy.types.Array`
        the arrays two be tested (can be subclasses)

    *attrs
        the list of attributes to test, defaults to all

    almost_equal : `bool`, optional
        allow the numpy array's to be 'almost' equal, default: `False`,
        i.e. require exact matches

    exclude : `list`, optional
        a list of attributes to exclude from the test
    """
    # get value test method
    if kwargs.pop('almost_equal', False):
        assert_array = assert_allclose
    else:
        assert_array = assert_array_equal
    # parse attributes to be tested
    if not attrs:
        attrs = a._metadata_slots
    exclude = kwargs.pop('exclude', [])
    attrs = [attr for attr in attrs if attr not in exclude]
    # test data
    assert_attributes(a, b, *attrs)
    assert_array(a.value, b.value)


def assert_attributes(a, b, *attrs):
    """Assert that the attributes for two objects match

    `attrs` should be `list` of attribute names that can be accessed
    with `getattr`
    """
    for attr in attrs:
        x = getattr(a, attr, None)
        y = getattr(b, attr, None)
        if isinstance(x, numpy.ndarray) and isinstance(b, numpy.ndarray):
            assert_array_equal(x, y)
        else:
            assert x == y


def assert_table_equal(a, b, is_copy=True, meta=False, check_types=True,
                       almost_equal=False):
    """Assert that two tables store the same information
    """
    # check column names are the same
    assert sorted(a.colnames) == sorted(b.colnames)

    # check that the metadata match
    if meta:
        assert a.meta == b.meta

    if almost_equal:
        check_types = False  # assert_allclose doesn't work for structured
        assert_array = assert_allclose
    else:
        assert_array = assert_array_equal

    # actually check the data
    if check_types:
        assert_array(a.as_array(), b.as_array())
    else:
        for col, col2 in zip(a.columns.values(), b.columns.values()):
            assert_array(col, col2.astype(col.dtype))

    # check that the tables are copied or the same data
    for col, col2 in zip(a.columns.values(), b.columns.values()):
        # check may_share_memory is True when copy is False and so on
        assert numpy.may_share_memory(col, col2) is not is_copy


# -- I/O helpers --------------------------------------------------------------


def test_read_write(data, format, extension=None, autoidentify=True,
                    exclude=[], read_args=[], read_kw={},
                    write_args=[], write_kw={}):
    # parse extension and add leading period
    if extension is None:
        extension = format
    extension = '.%s' % extension.lstrip()
    # try writing the data and reading it back
    try:
        fp = tempfile.mktemp(suffix=extension)
        data.write(fp, *write_args, format=format, **write_kw)
        if autoidentify:
            data.write(fp, *write_args, **write_kw)
        b = data.read(fp, *read_args, format=format, **read_kw)
        if autoidentify:
            data.read(fp, *read_args, **read_kw)
        assert_quantity_sub_equal(data, b, exclude=exclude)
    finally:
        # make sure and clean up after ourselves
        if os.path.exists(fp):
            os.remove(fp)
