# -*- coding: utf-8 -*-
# Copyright (C) Duncan Macleod (2014-2020)
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

"""Basic utilities for reading/writing LIGO_LW-format XML files.

All specific unified input/output for class objects should be placed in
an 'io' subdirectory of the containing directory for that class.
"""

import builtins
import importlib
import os
import os.path
import re
from contextlib import contextmanager
from functools import wraps
from importlib import import_module
from numbers import Integral

import numpy

try:
    from ligo.lw.ligolw import (
        ElementError as LigolwElementError,
        LIGOLWContentHandler,
    )
except ImportError:  # no ligo.lw
    LigolwElementError = None
    LIGOLWContentHandler = None

from .utils import (file_list, FILE_LIKE)
from ..utils.decorators import deprecated_function

__author__ = 'Duncan Macleod <duncan.macleod@ligo.org>'

# record actual import function
_real_import = builtins.__import__

# XML elements
XML_SIGNATURE = b'<?xml'
LIGOLW_SIGNATURE = b'<!doctype ligo_lw'
LIGOLW_ELEMENT = b'<ligo_lw>'

LIGO_LW_MODULE_REGEX = re.compile(r"\Aligo.lw")

# regex to catch all errors associated with reading an old-format file
# with the new library
_compat_errors = {
    r"invalid Column \'process_id\'",
    r"invalid Column \'coinc_event_id'",
    r"invalid Column \'coinc_def_id'",
    r"invalid Column \'event_id'",
    r"invalid type \'ilwd:char\'"
}
LIGO_LW_COMPAT_ERROR = re.compile("({})".format("|".join(_compat_errors)))


# -- import hackery to support ligo.lw and glue.ligolw concurrently -----------

def _ligolw_compat_import(name, *args, **kwargs):
    name = LIGO_LW_MODULE_REGEX.sub("glue.ligolw", name)
    return _real_import(name, *args, **kwargs)


def _is_glue_ligolw_object(obj):
    if not isinstance(obj, type):
        obj = type(obj)
    return str(obj.__module__).startswith("glue.ligolw")


def ilwdchar_compat(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        use_compat = kwargs.pop(
            "ilwdchar_compat",
            any(map(
                _is_glue_ligolw_object,
                list(args) + list(kwargs.values()),
            )),
        )
        try:
            if use_compat:
                builtins.__import__ = _ligolw_compat_import
            return func(*args, **kwargs)
        finally:
            builtins.__import__ = _real_import
    return wrapped


def _get_ligolw_types(typename):
    """Utility function to return a tuple of type objects
    covering all LIGO_LW libraries

    This is designed purely for use with `isinstance`, and has
    not been tested for any other purpose.

    Examples
    --------
    With both python-ligo-lw and lscsoft-glue installed

    >>> _get_ligolw_types("Document")
    (<class 'glue.ligolw.ligolw.Document'>, <class 'ligo.lw.ligolw.Document'>)

    With only lscsoft-glue installed:

    >>> _get_ligolw_types("Document")
    (<class 'glue.ligolw.ligolw.Document'>)

    And with neither installed:

    >>> _get_ligolw_types("Document")
    ()

    (`isinstance` always returns false when given an empty type tuple)
    """
    types = []
    for pkg in ("glue.ligolw", "ligo.lw"):
        try:
            ligolw = importlib.import_module(".ligolw", package=pkg)
        except ImportError:
            continue
        types.append(getattr(ligolw, typename))
    return tuple(types)


# -- hack around around TypeError from LIGOTimeGPS(numpy.int32(...)) ----------

def _ligotimegps(s, ns=0):
    """Catch TypeError and cast `s` and `ns` to `int`
    """
    from lal import LIGOTimeGPS
    try:
        return LIGOTimeGPS(s, ns)
    except TypeError:
        return LIGOTimeGPS(int(s), int(ns))


@contextmanager
def patch_ligotimegps(module="ligo.lw.lsctables"):
    """Context manager to on-the-fly patch LIGOTimeGPS to accept all int types
    """
    module = import_module(module)
    orig = module.LIGOTimeGPS
    module.LIGOTimeGPS = _ligotimegps
    try:
        yield
    finally:
        module.LIGOTimeGPS = orig


# -- content handling ---------------------------------------------------------

@ilwdchar_compat
def get_partial_contenthandler(element):
    """Build a `PartialLIGOLWContentHandler` to read only this element

    Parameters
    ----------
    element : `type`, subclass of :class:`~ligo.lw.ligolw.Element`
        the element class to be read,

    Returns
    -------
    contenthandler : `type`
        a subclass of :class:`~ligo.lw.ligolw.PartialLIGOLWContentHandler`
        to read only the given `element`
    """
    from ligo.lw.ligolw import PartialLIGOLWContentHandler
    from ligo.lw.table import Table

    if issubclass(element, Table):
        def _element_filter(name, attrs):
            return element.CheckProperties(name, attrs)
    else:
        def _element_filter(name, _):
            return name == element.tagName

    return build_content_handler(PartialLIGOLWContentHandler, _element_filter)


@ilwdchar_compat
def get_filtering_contenthandler(element):
    """Build a `FilteringLIGOLWContentHandler` to exclude this element

    Parameters
    ----------
    element : `type`, subclass of :class:`~ligo.lw.ligolw.Element`
        the element to exclude (and its children)

    Returns
    -------
    contenthandler : `type`
        a subclass of
        :class:`~ligo.lw.ligolw.FilteringLIGOLWContentHandler` to
        exclude an element and its children
    """
    from ligo.lw.ligolw import FilteringLIGOLWContentHandler
    from ligo.lw.table import Table

    if issubclass(element, Table):
        def _element_filter(name, attrs):
            return ~element.CheckProperties(name, attrs)
    else:
        def _element_filter(name, _):
            # pylint: disable=unused-argument
            return name != element.tagName

    return build_content_handler(FilteringLIGOLWContentHandler,
                                 _element_filter)


@ilwdchar_compat
def build_content_handler(parent, filter_func):
    """Build a `~xml.sax.handler.ContentHandler` with a given filter
    """
    from ligo.lw.lsctables import use_in

    class _ContentHandler(parent):
        # pylint: disable=too-few-public-methods
        def __init__(self, document):
            super().__init__(document, filter_func)

    return use_in(_ContentHandler)


# -- reading ------------------------------------------------------------------

@ilwdchar_compat
def read_ligolw(source, contenthandler=LIGOLWContentHandler, **kwargs):
    """Read one or more LIGO_LW format files

    Parameters
    ----------
    source : `str`, `file`
        the open file or file path to read

    contenthandler : `~xml.sax.handler.ContentHandler`, optional
        content handler used to parse document

    verbose : `bool`, optional
        be verbose when reading files, default: `False`

    Returns
    -------
    xmldoc : :class:`~ligo.lw.ligolw.Document`
        the document object as parsed from the file(s)
    """
    from ligo.lw.ligolw import Document
    from ligo.lw import types
    from ligo.lw.lsctables import use_in
    from ligo.lw.utils import (load_url, ligolw_add)

    # mock ToPyType to link to numpy dtypes
    topytype = types.ToPyType.copy()
    for key in types.ToPyType:
        if key in types.ToNumPyType:
            types.ToPyType[key] = numpy.dtype(types.ToNumPyType[key]).type

    contenthandler = use_in(contenthandler)

    # read one or more files into a single Document
    source = file_list(source)
    try:
        if len(source) == 1:
            return load_url(
                source[0],
                contenthandler=contenthandler,
                **kwargs
            )
        return ligolw_add.ligolw_add(
            Document(),
            source,
            contenthandler=contenthandler,
            **kwargs
        )
    except LigolwElementError as exc:
        # failed to read with ligo.lw,
        # try again with glue.ligolw (ilwdchar_compat)
        if LIGO_LW_COMPAT_ERROR.search(str(exc)):
            try:
                return read_ligolw(
                    source,
                    contenthandler=contenthandler,
                    ilwdchar_compat=True,
                    **kwargs
                )
            except Exception:  # if fails for any reason, use original error
                pass
        raise
    finally:  # replace ToPyType
        types.ToPyType = topytype


@deprecated_function
def with_read_ligolw(func=None, contenthandler=None):  # pragma: no cover
    """Decorate a LIGO_LW-reading function to open a filepath if needed

    ``func`` should be written to presume a
    :class:`~ligo.lw.ligolw.Document` as the first positional argument
    """
    def decorator(func_):
        # pylint: disable=missing-docstring
        @wraps(func_)
        def decorated_func(source, *args, **kwargs):
            # pylint: disable=missing-docstring
            from ligo.lw.ligolw import Document
            from glue.ligolw.ligolw import Document as GlueDocument
            if not isinstance(source, (Document, GlueDocument)):
                read_kw = {
                    'contenthandler': kwargs.pop('contenthandler',
                                                 contenthandler),
                    'verbose': kwargs.pop('verbose', False),
                }
                return func_(read_ligolw(source, **read_kw), *args, **kwargs)
            return func_(source, *args, **kwargs)

        return decorated_func

    if func is not None:
        return decorator(func)
    return decorator


# -- reading ------------------------------------------------------------------

@ilwdchar_compat
def read_table(source, tablename=None, columns=None, contenthandler=None,
               **kwargs):
    """Read a :class:`~ligo.lw.table.Table` from one or more LIGO_LW files

    Parameters
    ----------
    source : `Document`, `file`, `str`, `CacheEntry`, `list`
        object representing one or more files. One of

        - a LIGO_LW :class:`~ligo.lw.ligolw.Document`
        - an open `file`
        - a `str` pointing to a file path on disk
        - a formatted :class:`~lal.utils.CacheEntry` representing one file
        - a `list` of `str` file paths or :class:`~lal.utils.CacheEntry`

    tablename : `str`
        name of the table to read.

    columns : `list`, optional
        list of column name strings to read, default all.

    contenthandler : `~xml.sax.handler.ContentHandler`, optional
        SAX content handler for parsing LIGO_LW documents.

    **kwargs
        other keyword arguments are passed to `~gwpy.io.ligolw.read_ligolw`

    Returns
    -------
    table : :class:`~ligo.lw.table.Table`
        `Table` of data
    """
    from ligo.lw.ligolw import Document
    from ligo.lw import (table, lsctables)

    # get ilwdchar_compat to pass to read_ligolw()
    if Document.__module__.startswith("glue"):
        kwargs["ilwdchar_compat"] = True

    # get content handler to read only this table (if given)
    if tablename is not None:
        tableclass = lsctables.TableByName[
            table.Table.TableName(tablename)
        ]
        if contenthandler is None:
            contenthandler = get_partial_contenthandler(tableclass)

        # overwrite loading column names to get just what was asked for
        _oldcols = tableclass.loadcolumns
        if columns is not None:
            tableclass.loadcolumns = columns

    # read document
    if isinstance(source, Document):
        xmldoc = source
    else:
        if contenthandler is None:
            contenthandler = lsctables.use_in(LIGOLWContentHandler)
        try:
            xmldoc = read_ligolw(source, contenthandler=contenthandler,
                                 **kwargs)
        finally:  # reinstate original set of loading column names
            if tablename is not None:
                tableclass.loadcolumns = _oldcols

    # now find the right table
    if tablename is None:
        tables = list_tables(xmldoc)
        if not tables:
            raise ValueError("No tables found in LIGO_LW document(s)")
        if len(tables) > 1:
            tlist = "'{}'".format("', '".join(tables))
            raise ValueError("Multiple tables found in LIGO_LW document(s), "
                             "please specify the table to read via the "
                             "``tablename=`` keyword argument. The following "
                             "tables were found: {}".format(tlist))
        tableclass = lsctables.TableByName[table.Table.TableName(tables[0])]

    # extract table
    return tableclass.get_table(xmldoc)


# -- writing ------------------------------------------------------------------

@ilwdchar_compat
def open_xmldoc(fobj, **kwargs):
    """Try and open an existing LIGO_LW-format file, or create a new Document

    Parameters
    ----------
    fobj : `str`, `file`
        file path or open file object to read

    **kwargs
        other keyword arguments to pass to
        :func:`~ligo.lw.utils.load_fileobj` as appropriate

    Returns
    --------
    xmldoc : :class:`~ligo.lw.ligolw.Document`
        either the `Document` as parsed from an existing file, or a new, empty
        `Document`
    """
    from ligo.lw.ligolw import (Document, LIGOLWContentHandler)
    from ligo.lw.lsctables import use_in
    from ligo.lw.utils import load_fileobj

    use_in(kwargs.setdefault('contenthandler', LIGOLWContentHandler))

    # read from an existing Path/filename
    if not isinstance(fobj, FILE_LIKE):
        try:
            with open(fobj, "rb") as fobj2:
                return open_xmldoc(fobj2, **kwargs)
        except (OSError, IOError):
            # or just create a new Document
            return Document()

    try:
        out = load_fileobj(fobj, **kwargs)
    except LigolwElementError as exc:
        if LIGO_LW_COMPAT_ERROR.search(str(exc)):
            # handle ligo.lw/glue.ligolw compatibility issue
            try:
                # load_fileobj will have closed the file, so we need to
                # go back to the origin and reopen it
                return open_xmldoc(fobj.name, ilwdchar_compat=True, **kwargs)
            except Exception:  # for any reason, raise original
                pass
        raise

    if isinstance(out, tuple):  # glue.ligolw.utils.load_fileobj returns tuple
        out = out[0]
    return out


@ilwdchar_compat
def get_ligolw_element(xmldoc):
    """Find an existing <LIGO_LW> element in this XML Document
    """
    from ligo.lw.ligolw import WalkChildren
    ligolw_types = _get_ligolw_types("LIGO_LW")

    if isinstance(xmldoc, ligolw_types):
        return xmldoc
    for elem in WalkChildren(xmldoc):
        if isinstance(elem, ligolw_types):
            return elem
    raise ValueError("Cannot find LIGO_LW element in XML Document")


@ilwdchar_compat
def write_tables_to_document(xmldoc, tables, overwrite=False):
    """Write the given LIGO_LW table into a :class:`Document`

    Parameters
    ----------
    xmldoc : :class:`~ligo.lw.ligolw.Document`
        the document to write into

    tables : `list` of :class:`~ligo.lw.table.Table`
        the set of tables to write

    overwrite : `bool`, optional, default: `False`
        if `True`, delete an existing instance of the table type, otherwise
        append new rows
    """
    from ligo.lw.ligolw import LIGO_LW
    from ligo.lw import lsctables

    # find or create LIGO_LW tag
    try:
        llw = get_ligolw_element(xmldoc)
    except ValueError:
        llw = LIGO_LW()
        xmldoc.appendChild(llw)

    for table in tables:
        try:  # append new data to existing table
            old = lsctables.TableByName[
                table.TableName(table.Name)].get_table(xmldoc)
        except ValueError:  # or create a new table
            llw.appendChild(table)
        else:
            if overwrite:
                llw.removeChild(old)
                old.unlink()
                llw.appendChild(table)
            else:
                old.extend(table)

    return xmldoc


@ilwdchar_compat
def write_tables(target, tables, append=False, overwrite=False, **kwargs):
    """Write an LIGO_LW table to file

    Parameters
    ----------
    target : `str`, `file`, :class:`~ligo.lw.ligolw.Document`
        the file or document to write into

    tables : `list`, `tuple` of :class:`~ligo.lw.table.Table`
        the tables to write

    append : `bool`, optional, default: `False`
        if `True`, append to an existing file/table, otherwise `overwrite`

    overwrite : `bool`, optional, default: `False`
        if `True`, delete an existing instance of the table type, otherwise
        append new rows

    **kwargs
        other keyword arguments to pass to
        :func:`~ligo.lw.utils.load_fileobj` as appropriate
    """
    from ligo.lw.ligolw import (Document, LIGO_LW, LIGOLWContentHandler)
    from ligo.lw import utils as ligolw_utils

    # allow writing directly to XML
    if isinstance(target, (Document, LIGO_LW)):
        xmldoc = target
    # open existing document, if possible
    elif append:
        xmldoc = open_xmldoc(
            target,
            contenthandler=kwargs.pop('contenthandler', LIGOLWContentHandler),
        )
    # fail on existing document and not overwriting
    elif (
        not overwrite
        and isinstance(target, (str, os.PathLike))
        and os.path.exists(target)
    ):
        raise IOError("File exists: {}".format(target))
    else:  # or create a new document
        xmldoc = Document()

    # convert table to format
    write_tables_to_document(xmldoc, tables, overwrite=overwrite)

    # find writer function and target filename
    if isinstance(target, FILE_LIKE):
        writer = ligolw_utils.write_fileobj
        name = target.name
    else:
        writer = ligolw_utils.write_filename
        name = target = str(target)

    # handle gzip compression kwargs
    if name.endswith('.gz') and writer.__module__.startswith('glue'):
        kwargs.setdefault('gz', True)
    elif name.endswith('.gz'):
        kwargs.setdefault('compress', 'gz')

    # write XML
    writer(xmldoc, target, **kwargs)


# -- utilities ----------------------------------------------------------------

@ilwdchar_compat
def iter_tables(source):
    """Iterate over all tables in the given document(s)

    Parameters
    ----------
    source : `file`, `str`, :class:`~ligo.lw.ligolw.Document`, `list`
        one or more open files, file paths, or LIGO_LW `Document`s

    Yields
    ------
    ligo.lw.table.Table
        a table structure from the document(s)
    """
    from ligo.lw.ligolw import (Stream, WalkChildren)

    # get LIGO_LW object
    if not isinstance(source, _get_ligolw_types("Element")):
        filt = get_filtering_contenthandler(Stream)
        source = read_ligolw(source, contenthandler=filt)
    llw = get_ligolw_element(source)

    # yield tables
    for elem in WalkChildren(llw):
        if elem.tagName == "Table":
            yield elem


@ilwdchar_compat
def list_tables(source):
    """List the names of all tables in this file(s)

    Parameters
    ----------
    source : `file`, `str`, :class:`~ligo.lw.ligolw.Document`, `list`
        one or more open files, file paths, or LIGO_LW `Document`s

    Examples
    --------
    >>> from gwpy.io.ligolw import list_tables
    >>> print(list_tables('H1-LDAS_STRAIN-968654552-10.xml.gz'))
    ['process', 'process_params', 'sngl_burst', 'search_summary', 'segment_definer', 'segment_summary', 'segment']
    """  # noqa: E501
    return [tbl.TableName(tbl.Name) for tbl in iter_tables(source)]


@ilwdchar_compat
def to_table_type(val, cls, colname):
    """Cast a value to the correct type for inclusion in a LIGO_LW table

    This method returns the input unmodified if a type mapping for ``colname``
    isn't found.

    Parameters
    ----------
    val : `object`
        The input object to convert, of any type

    cls : `type`, subclass of :class:`~ligo.lw.table.Table`
        the table class to map against

    colname : `str`
        The name of the mapping column

    Returns
    -------
    obj : `object`
        The input ``val`` cast to the correct type

    Examples
    --------
    >>> from gwpy.io.ligolw import to_table_type as to_ligolw_type
    >>> from ligo.lw.lsctables import SnglBurstTable
    >>> print(to_ligolw_type(1.0, SnglBurstTable, 'central_freq')))
    1.0

    ID integers are converted to fancy ILWD objects

    >>> print(to_ligolw_type(1, SnglBurstTable, 'process_id')))
    sngl_burst:process_id:1

    Formatted fancy ILWD objects are left untouched:

    >>> from ligo.lw.ilwd import ilwdchar
    >>> pid = ilwdchar('process:process_id:0')
    >>> print(to_ligolw_type(pid, SnglBurstTable, 'process_id')))
    process:process_id:1
    """
    from ligo.lw.types import (
        ToNumPyType as numpytypes,
        ToPyType as pytypes,
    )

    # if nothing to do...
    if val is None or colname not in cls.validcolumns:
        return val

    llwtype = cls.validcolumns[colname]

    # don't mess with formatted IlwdChar
    if llwtype == 'ilwd:char':
        return _to_ilwd(val, cls.tableName, colname,
                        ilwdchar_compat=_is_glue_ligolw_object(cls))
    # otherwise map to numpy or python types
    try:
        return numpy.sctypeDict[numpytypes[llwtype]](val)
    except KeyError:
        return pytypes[llwtype](val)


@ilwdchar_compat
def _to_ilwd(value, tablename, colname):
    from ligo.lw.ilwd import (ilwdchar, get_ilwdchar_class)
    from ligo.lw._ilwd import ilwdchar as IlwdChar

    if isinstance(value, IlwdChar) and value.column_name != colname:
        raise ValueError("ilwdchar '{0!s}' doesn't match column "
                         "{1!r}".format(value, colname))
    if isinstance(value, IlwdChar):
        return value
    if isinstance(value, Integral):
        return get_ilwdchar_class(tablename, colname)(value)
    return ilwdchar(value)


# -- identify -----------------------------------------------------------------

def is_ligolw(origin, filepath, fileobj, *args, **kwargs):
    """Identify a file object as LIGO_LW-format XML
    """
    # pylint: disable=unused-argument
    if fileobj is not None:
        loc = fileobj.tell()
        fileobj.seek(0)
        try:
            line1 = fileobj.readline().lower()
            line2 = fileobj.readline().lower()
            try:
                return (
                    line1.startswith(XML_SIGNATURE)
                    and line2.startswith((LIGOLW_SIGNATURE, LIGOLW_ELEMENT))
                )
            except TypeError:  # bytes vs str
                return (
                    line1.startswith(XML_SIGNATURE.decode('utf-8'))
                    and line2.startswith((
                        LIGOLW_SIGNATURE.decode('utf-8'),
                        LIGOLW_ELEMENT.decode('utf-8'),
                    ))
                )
        finally:
            fileobj.seek(loc)

    element_types = _get_ligolw_types("Element")
    return len(args) > 0 and isinstance(args[0], element_types)


@deprecated_function
def is_xml(origin, filepath, fileobj, *args, **kwargs):  # pragma: no cover
    """Identify a file object as XML (any format)
    """
    # pylint: disable=unused-argument
    if fileobj is not None:
        loc = fileobj.tell()
        fileobj.seek(0)
        try:
            sig = fileobj.read(5).lower()
            return sig == XML_SIGNATURE
        finally:
            fileobj.seek(loc)
    elif filepath is not None:
        return filepath.endswith(('.xml', '.xml.gz'))
