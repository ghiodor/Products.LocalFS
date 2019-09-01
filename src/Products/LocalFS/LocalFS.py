##############################################################################
# 
# Copyright (c) 1999 Jonothan Farr 
# All rights reserved. Written by Jonothan Farr <jfarr@speakeasy.org> 
# 
# Redistribution and use in source and binary forms, with or without 
# modification, are permitted provided that the following conditions 
# are met: 
#
# 1. Redistributions of source code must retain the above copyright 
#    notice, this list of conditions and the following disclaimer. 
# 2. Redistributions in binary form must reproduce the above copyright 
#    notice, this list of conditions and the following disclaimer in the 
#    documentation and/or other materials provided with the distribution. 
# 3. The name of the author may not be used to endorse or promote products 
#    derived from this software without specific prior written permission 
# 
# Disclaimer
#
#   THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR 
#   IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES 
#   OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. 
#   IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, 
#   INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT 
#   NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, 
#   DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY 
#   THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT 
#   (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF 
#   THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. 
#   
# In accordance with the license provided for by the software upon 
# which some of the source code has been derived or used, the following 
# acknowledgement is hereby provided: 
# 
#      "This product includes software developed by Digital Creations 
#      for use in the Z Object Publishing Environment 
#      (http://www.zope.org/)."
#
##############################################################################
"""Local File System product"""
__version__='2.0'
__doc__="""Local File System product"""

import sys, os, re, stat, glob as errno, time, tempfile
from urllib.parse import quote
import App, Acquisition, Persistence, OFS
import AccessControl
from App.Extensions import getObject
from App.FactoryDispatcher import ProductDispatcher
#TODO from webdav.NullResource import LockNullResource
from ZPublisher.HTTPResponse import HTTPResponse
from App.Dialogs import MessageDialog
from App.special_dtml import HTMLFile
from OFS.Image import Pdata
from TreeDisplay.TreeTag import encode_str
from OFS.CopySupport import _cb_encode, _cb_decode, CopyError
# The following MessageDialog are no longer available from OFS.CopySupport
eNoData=MessageDialog(
        title='No Data',
        message='No clipboard data found.',
        action ='manage_main',)

eInvalid=MessageDialog(
         title='Clipboard Error',
         message='The data in the clipboard could not be read, possibly due ' \
         'to cookie data being truncated by your web browser. Try copying ' \
         'fewer objects.',
         action ='manage_main',)

eNotFound=MessageDialog(
          title='Item Not Found',
          message='One or more items referred to in the clipboard data was ' \
          'not found. The item may have been moved or deleted after you ' \
          'copied it.',
          action ='manage_main',)

from ZODB.TimeStamp import TimeStamp
from DateTime import DateTime
import marshal  # sbk
from ZPublisher import xmlrpc
try: 
    from OFS.role import RoleManager 
except ImportError: 
    # Zope <=2.12 
    from AccessControl.Role import RoleManager
from zExceptions import BadRequest, Forbidden, Unauthorized, NotFound, MethodNotAllowed
from Products.PageTemplates.ZopePageTemplate import ZopePageTemplate
from Products.PythonScripts.PythonScript import PythonScript

class UploadError(Exception): pass
class RenameError(Exception): pass
class DeleteError(Exception): pass

_iswin32 = (sys.platform == 'win32')
if (_iswin32):
    try:
        import win32wnet
    except ImportError:
        pass
    unc_expr = re.compile(r'(\\\\[^\\]+\\[^\\]+)(.*)')

_test_read = 1024 * 8
_unknown = '(unknown)'

############################################################################
# The process of determining the content-type involves the following steps.
# This same process is used in LocalDirectory._getOb and LocalFile._getType.
#
# 1. Call _get_content_type which tries to look up the content-type 
#    in the type map based on the file extension.
# 2. If that fails then create a file object with the first _test_read
#    bytes of data and see what content-type Zope determines.
#    If we can't read the file then assign 'application/octet-stream'.
# 3. If we found a content-type then assign it to the object in
#    _set_content_type. This overrides the type assigned by Zope.
# 4. Try to see if Zope has assigned 'text/html' to a file that it
#    shouldn't have and change the type back to 'text/plain'. This
#    also happens in _set_content_type.
############################################################################

def _get_content_type(ext, _type_map):
    """_get_content_type"""
    try: 
        return _type_map[ext]
    except KeyError:
        return (None, None)

def _set_content_type(ob, content_type, data):
    """_set_content_type"""
    if content_type:
        ob.content_type = content_type
    if getattr(ob, 'content_type', None) == 'text/html':
        if content_type == 'text/html':
            return
        data = data.strip().lower()
        if data[:6] != '<html>' and data[:14] != '<!doctype html':
            ob.content_type = 'text/plain'

_types = {
    '.py': ('text/x-python', 'PythonScript'),
    '.html': ('text/html', 'DTMLDocument'),
    '.htm': ('text/html', 'DTMLDocument'),
    '.dtml': ('text/html', 'DTMLMethod'),
    '.gif': ('image/gif', 'Image'),
    '.jpg': ('image/jpeg', 'Image'),
    '.jpeg': ('image/jpeg', 'Image'),
    '.png': ('image/png', 'Image'),
    '.pt': ('text/html', 'PageTemplate'),
    '.zpt': ('text/html', 'PageTemplate'),
    '.ra': ('audio/vnd.rn-realaudio', ''),
    '.rv': ('video/vnd.rn-realvideo', ''),
    '.rm': ('application/vnd.rn-realmedia', ''),
    '.rp': ('image/vnd.rn-realpix', ''),
    '.rt': ('text/vnd.rn-realtext', ''),
    '.smi': ('application/smil', ''),
    '.swf': ('application/x-shockwave-flash', ''),
    '.stx': ('text/html', 
        'Products.StructuredDocument.StructuredDocument.StructuredDocument'),
    '.xml': ('text/xml', 'LocalFS.Factory.XMLDocumentFactory'),
}

_typemap_error = "Error parsing type map: '%s'"

def _list2typemap(l):
    """_list2typemap"""
    if not l:
        return
    m = {}
    for i in l:
        if i:
            try: 
                e, t = i.split()
                c = ''
            except ValueError:
                try: 
                    e, t, c = re.split('[ \t]+', i, 2)
                except ValueError:
                    raise ValueError(_typemap_error % i)
            m[e] = (t, c)
    return m

def _typemap2list(m):
    """_typemap2list"""
    l = []
    keys = sorted(m.keys())
    for k in keys:
        v = m[k]
        if type(v) is type(()): 
            l.append("".join((k, v[0], v[1])))
        else:
            l.append("".join((k, v)))
    return l
    
_icons = {
    'directory': 'dir.gif',
    'application': 'binary.gif',
    'application/mac-binhex40': 'compressed.gif',
    'application/octet-stream': 'binary.gif',
    'application/pdf': 'layout.gif',
    'application/postscript': 'ps.gif',
    'application/smil': 'layout.gif',
    'application/vnd.rn-realmedia': 'movie.gif',
    'application/x-dvi': 'dvi.gif',
    'application/x-gtar': 'tar.gif',
    'application/x-gzip': 'compressed.gif',
    'application/x-shockwave-flash': 'image2.gif',
    'application/x-tar': 'tar.gif',
    'application/x-tex': 'tex.gif',
    'application/zip': 'compressed.gif',
    'audio': 'sound1.gif',
    'audio/mpeg': 'sound1.gif',
    'audio/vnd.rn-realaudio': 'sound1.gif',
    'audio/x-wav': 'sound1.gif',
    'image': 'image2.gif',
    'image/gif': 'image2.gif',
    'image/jpeg': 'image2.gif',
    'image/png': 'image2.gif',
    'image/vnd.rn-realpix': 'image2.gif',
    'text': 'text.gif',
    'text/html': 'layout.gif',
    'text/plain': 'text.gif',
    'text/x-python': 'p.gif',
    'video': 'movie.gif',
    'video/mpeg': 'movie.gif',
    'video/quicktime': 'movie.gif',
    'video/vnd.rn-realvideo': 'movie.gif',
}

_icon_base = 'misc_/LocalFS/'
for k, v in _icons.items():
    _icons[k] = _icon_base + v

_iconmap_error = "Error parsing icon map: '%s'"

def _list2iconmap(l):
    """_list2iconmap"""
    if not l:
        return
    m = {}
    for i in l:
        if i:
            try:
                k, v = i.split()
            except ValueError:
                raise ValueError(_iconmap_error % i)
            m[k] = v
    return m

def _iconmap2list(m):
    """_iconmap2list"""
    l = []
    keys = sorted(m.keys())
    for k in keys:
        l.append("".join((k, m[k])))
    return l

def _create_ob(id, path, _type_map):
    """_create_ob"""
    ob = None
    ext = os.path.splitext(path)[-1]
    t, c = _get_content_type(ext.lower(), _type_map)
    if c is not None:
        ob = _create_builtin_ob(c, id, path)
        if ob is None:
            ob = _create_ob_from_function(c, id, path)
        if ob is None:
            ob = _create_ob_from_factory(c, id, path)
    if ob is None:
        ob = _wrap_ob(_create_File(id, path), path)
    # TODO: avoid this check here
    file = open(path, 'rb')
    ob.__doc__ = 'LocalFile'
    _set_content_type(ob, t, file.read(_test_read))
    return ob

def _create_DTMLMethod(id, path):
    """_create_DTMLMethod"""
    with open(path, 'r') as file:
        return OFS.DTMLMethod.DTMLMethod(file.read(), __name__=id)

def _create_DTMLDocument(id, path):
    """_create_DTMLDocument"""
    with open(path, 'r') as file:
        return OFS.DTMLDocument.DTMLDocument(file.read(), __name__=id)

def _create_Image(id, path):
    """_create_Image"""
    with open(path, 'rb') as file:
        ob = OFS.Image.Image(id, '', file)
    return ob

def _create_File(id, path):
    """_create_File"""
    with open(path, 'rb') as file: # TODO always mode=b ok?
        ob = OFS.Image.File(id, '', file)
    return ob

def _create_ZPT(id, path):
    """_create_ZPT"""
    with open(path, 'r') as file:
        ob = ZopePageTemplate(id, '', content_type='text/html')
        ob.pt_upload(None, file, encoding='utf-8')
    return ob

def _create_PythonScript(id, path):
    """_create_PythonScript"""
    with open(path, 'r') as file:
        ob = PythonScript(id)
        ob.write(file.read())
    return ob

_builtin_create = {
    'DTMLMethod': _create_DTMLMethod,
    'DTMLDocument': _create_DTMLDocument,
    'Image': _create_Image,
    'File': _create_File,
    'PageTemplate': _create_ZPT,
    'PythonScript': _create_PythonScript,
}

def _create_builtin_ob(c, id, path):
    try:
        f = _builtin_create[c]
        obj = f(id, path)
        return _wrap_ob(obj, path)
    except: pass
    
def _create_ob_from_function(c, id, path):
    try:
        i = c.rindex('.')
        m, c = c[:i], c[i+1:]
        m = __import__(m, globals(), locals(), (c,))
        c = getattr(m, c)
        f = getattr(c, 'createSelf').__func__ # TODO: does it have __func__?
        if f.__code__.co_varnames == ('id', 'file'):
            file = open(path, 'rb') # TODO mode=b?
            obj = f(id, file)
            file.close()
            return _wrap_ob(obj, path)
    except: pass
    
def _create_ob_from_factory(c, id, path):
    try:
        i = c.rindex('.')
        m, c = c[:i], c[i+1:]
        c = getObject(m, c)
        f = c()
        file = open(path, 'rb') # TODO mode=b?
        obj = f(id, file)
        file.close()
        ob = _wrap_ob(obj, path)
        ob.__factory = f
        return ob
    except: pass


class Wrapper:
    """Mix-in class used to save object changes."""
    _local_path = None
    # Create a global and persistent lock table
    _dav_writelocks = {}
    # The object itself is not persistent and cannot be stored
    _p_changed = 0

    # TODO: Zope 4 does not use bobobase* anymore
    def bobobase_modification_time(self):
        """ bobobase_modification_time """
        t = os.stat(self._local_path)[stat.ST_MTIME]
        return DateTime(t)
    
    def __repr__(self):
        """ __repr__ """
        c = self.__class__.__bases__[-1].__name__
        return '<%s ObjectWrapper instance at %8X>' % (c, id(self))
    
    def wl_lockmapping(self, killinvalids=0, create=0):
        """ Overwrite the default method of LockableItem """
        locks = self._dav_writelocks.get(self._local_path)
        if locks is None:
            locks = {}
            if create:
                # Store it in persistent lock table
                self._dav_writelocks[self._local_path] = locks
        elif killinvalids:
            # Delete invalid locks
            for token, lock in locks.items():
                if not lock.isValid():
                    del locks[token]
            if not locks and not create:
                # Remove empty lock table
                del self._dav_writelocks[self._local_path]
        return locks


_wrapper_method = '''def %(name)s %(arglist)s:
    """Wrapper for the %(name)s method."""
    r = self.__class__.__bases__[-1].%(name)s(*%(baseargs)s)
    try:
        _save_ob(self, self._local_path)
    except ValueError: pass
    return r
'''

_wrappers = {}

def _get_wrapper(c):
    try:
        return _wrappers[c]
    except KeyError:
        class ObjectWrapper(Wrapper, c): pass
        _wrap_method(ObjectWrapper, 'manage_edit')
        _wrap_method(ObjectWrapper, 'manage_upload')
        _wrap_method(ObjectWrapper, 'pt_edit')
        _wrap_method(ObjectWrapper, 'write') # PythonScript
        _wrap_method(ObjectWrapper, 'ZBindingsHTML_editAction') # PythonScript
        _wrap_method(ObjectWrapper, 'PUT')
        # Remove management options that cannot be used 
        manage_options = []
        for opt in ObjectWrapper.manage_options:
            if opt['label'] not in ('Properties', 'Security', 'Undo',
                    'Ownership', 'Interfaces', 'Proxy', 'History'):
                manage_options.append(opt)
        ObjectWrapper.manage_options = tuple(manage_options)
        _wrappers[c] = ObjectWrapper
        return ObjectWrapper

def _wrap_method(c, name):
    try: f = getattr(c.__bases__[-1], name)
    except AttributeError: return
    a = list(f.__code__.co_varnames)[:f.__code__.co_argcount]
    d = f.__defaults__ or () # to avoid len(None)
    arglist = []
    baseargs = []
    while (len(a) > len(d)):
        arglist.append(a[0])
        baseargs.append(a[0])
        del a[0]
    for i in range(len(a)):
        arglist.append('%s=%s' % (a[i], repr(d[i])))
        baseargs.append(a[i])
    arglist = '(' + ','.join(arglist) + ')'
    baseargs = '(' + ','.join(baseargs) + ')'
    d = {}
    exec(_wrapper_method % vars(), globals(), d)
    setattr(c, name, d[name])

def _wrap_ob(ob, path):
    c = ob.__class__
    n = ob.__name__
    if hasattr(c, '__basicnew__'):
        c = _get_wrapper(c)
        d = ob.__dict__
        ob = c.__basicnew__()
        ob.__dict__.update(d)
    else:
        c = _get_wrapper(c)
        ob.__class__ = c
    ob._local_path = path
    ob.__name__ = n
    ob._p_oid = path
    return ob

def _save_DTML(ob, path):
    f = open(path, 'w')
    try:
       f.write(ob.read())
    finally:
       f.close()

def _save_File(ob, path):
    if isinstance(ob.data, Pdata):
        f = open(path, 'wb')
        f.write(str(ob.data))
        f.close()
    else:
        f = open(path, 'wb')
        f.write(ob.data)
        f.close()

def _save_Folder(ob, path):
    os.mkdir(path)
    
_builtin_save = {
    'Script (Python)': _save_DTML,
    'DTML Method': _save_DTML,
    'DTML Document': _save_DTML,
    'Image': _save_File,
    'File': _save_File,
    'Folder': _save_Folder,
    'Page Template': _save_DTML,
}

def _save_builtin_ob(ob, path):
    try: 
        f = _builtin_save[ob.meta_type]
        f(ob, path)
        return 1
    except KeyError: pass

def _save_ob_with_function(ob, path):
    try:
        ob.saveSelf(path)
        return 1
    except: pass

def _save_ob_with_factory(ob, path):
    try:
        ob.__factory.save(ob, path)
        return 1
    except: pass
    
def _save_ob(ob, path):
    s = _save_builtin_ob(ob, path)
    if not s:
        s = _save_ob_with_function(ob, path)
    if not s:
        s = _save_ob_with_factory(ob, path)
    if not s:
        raise TypeError("Cannot save files of type '%s'." % ob.meta_type)

def _set_timestamp(ob, path):
    t = os.stat(path)[stat.ST_MTIME]
    t = TimeStamp(*time.gmtime(t)[:6])
    ob._p_serial = t.raw()
    
_marker = []

def valid_id(id):
    if id == os.curdir or id == os.pardir or id[0] == '_':
        return 0
    return 1
    
bad_id = re.compile('[^a-zA-Z0-9-_~,. ]').search #TS

def absattr(attr):
    if callable(attr):
        return attr()
    return attr

def sanity_check(c, ob):
    # This is called on cut/paste operations to make sure that
    # an object is not cut and pasted into itself or one of its
    # subobjects, which is an undefined situation.
    dest = c.basepath
    src = ob._local_path
    if dest[:len(src)] != src:
        return 1
    return 0

class LocalDirectory(
    OFS.CopySupport.CopyContainer,
    App.Management.Navigation,
    OFS.SimpleItem.Item, 
    Acquisition.Implicit
    ):
    
    """Object representing a directory in the local file system."""

    meta_type = 'Local Directory'
    
    isPrincipiaFolderish = 0   # 0 to avoid slow down of ZMI
    manage_addProduct = ProductDispatcher()

    manage_main = HTMLFile('dtml/main', globals())
    index_html = HTMLFile('dtml/methodBrowse', globals())
    manage_uploadForm = HTMLFile('dtml/methodUpload', globals())
    
    manage_options = (
        {'label': 'Contents', 'action': 'manage_main'},
        {'label': 'View', 'action': 'index_html'},
        {'label': 'Upload', 'action': 'manage_uploadForm'},
        )

    icon = 'misc_/OFSP/Folder_icon.gif'
    
    security = AccessControl.ClassSecurityInfo()

    security.declareProtected(
        'FTP access',
        'manage_FTPstat',
        'manage_FTPget',
        'manage_FTPlist')

    
    def __init__(self, id, basepath, root, tree_view, catalog, _type_map,
                 _icon_map, file_filter=None):
        """LocalDirectory __init__"""
        self.id = id
        self.basepath = self._local_path = basepath
        self.root = root
        self.tree_view = self.isPrincipiaFolderish = tree_view
        self.catalog = catalog
        self._type_map = _type_map
        self._icon_map = _icon_map
        self.file_filter = file_filter

    def __bobo_traverse__(self, REQUEST, name):
        """ bobo_traverse """
        # import pdb; pdb.set_trace()
        method = REQUEST.get('REQUEST_METHOD', 'GET').upper()
        try:
            # FTP - PUT
            if not method in ('GET', 'POST', 'HEAD'):
               return None # LockNullResource(self, name, REQUEST).__of__(self)
        except:
            pass
        try:
            return self._safe_getOb(name)
        except:
            pass
        try:
            return getattr(self, name)
        except AttributeError: 
            pass
        # ***Andreas did not apply this change from SmilyChris because I do 
        # *** not need to handle errors if self._save_getOb(name) fails
        # It fails on Zope2.7b3+ so LocalFS is broken 
        
        # ***SmileyChris (PTs don't have a RESPONSE?)
        #try:  # ***
        #    REQUEST.RESPONSE.notFoundError(name)
        #except:
        #    HTTPResponse().notFoundError(name)  # ***
        
    def __getitem__(self, i):
        if isinstance(i, str):
            return self._safe_getOb(i)
        else:
            raise TypeError('index must be a string')
        
    def __getattr__(self, attr):
        try:
            return self._safe_getOb(attr)
        except NotFound:
            raise AttributeError(attr)
    
    def _getpath(self, id):
        return os.path.join(self.basepath, id)

    def _getfileob(self, id, spec=None):
        if spec is None:
            spec=self.file_filter
        path = self._getpath(id)
        return LocalFile(self, id, path, spec)
    
    def _ids(self, spec=None):
        if spec is None:
            spec=self.file_filter
        try:
            ids = os.listdir(self.basepath)
        except (OSError, IOError) as err:
            if err[0] == errno.EACCES:
                raise Forbidden(HTTPResponse()._error_html(
                    'Forbidden',
                    'Sorry, you do not have permission to read '
                    'the requested directory.<p>'))
            else: raise
        if (spec is not None):
            try:
                if (type(spec) is type('')):
                    spec = spec.split(' ')
                curdir = os.getcwd()
                os.chdir(self.basepath)
                l = []
                for id in ids:
                    if os.path.isdir(id) and '*/' in spec or '*\\' in spec:
                        l.append(id)
                for patt in spec:
                    names = glob.glob(patt)
                    for id in names:
                        if id[-1] == os.sep: id = id[:-1]
                        if (id not in l):
                            l.append(id)
                ids = l
            finally:
                os.chdir(curdir)
        ids = sorted(filter(valid_id, ids))
        return ids
        
    def _safe_getOb(self, name, default=_marker):
        return self._getOb(name, default)

    def _getOb(self, id, default=_marker):
        if id in (os.curdir, os.pardir):
            raise ValueError(id)
        ob = None
        path = self._getpath(id)
        if os.path.isdir(path):
            ob = LocalDirectory(id, path, self.root or self, self.tree_view,
                self.catalog, self._type_map, self._icon_map)
        elif os.path.isfile(path):
            ob = _create_ob(id, path, self._type_map)
        if ob is None:
            if default is _marker:
                raise AttributeError(id)
            return default
        _set_timestamp(ob, path)
        ob._p_jar = self._p_jar
        return ob.__of__(self) # TODO what's this?
                    
    def _setObject(self, id, object, roles=None, user=None):
        if getattr(object, '__locknull_resource__', 0):
            self._checkId(id, 1)
            return id
        else:
            self._checkId(id)
        self._safe_setOb(id, object)
        return id

    def _delObject(self, id, dp=1):
        self._delOb(id)

    def _checkId(self, id, allow_dup=0):
        # If allow_dup is false, an error will be raised if an object
        # with the given id already exists. If allow_dup is true,
        # only check that the id string contains no illegal chars.
        if not id:
            raise BadRequest('No id was specified')
        if bad_id(id):
            raise BadRequest(
                'The id %s contains characters illegal in filenames.' % id)
        if id[0]=='_':
            raise BadRequest(
                'The id %s  is invalid - it begins with an underscore.'  % id)
        if not allow_dup:
            path = self._getpath(id)
            if os.path.exists(path):
                raise BadRequest(
                    'The id %s is invalid - it is already in use.' % id)

    def _safe_setOb(self, id, ob):
        try: self._setOb(id, ob)
        except EnvironmentError as err: 
            if (err[0] == errno.EACCES):
                raise Forbidden(HTTPResponse()._error_html(
                    'Forbidden',
                    "Sorry, you do not have permission to write "
                    "to this directory.<p>"))
            else: raise
        
    def _setOb(self, id, ob):
        if not hasattr(ob, 'meta_type'):
            raise BadRequest('Unknown object type.')
        path = self._getpath(id)
        try: _save_ob(ob, path)
        except TypeError:
            raise BadRequest(
                "Cannot add objects of type '%s' to local directories."
                % ob.meta_type)

    def _delOb(self, id):
        path = self._getpath(id)
        try:
            if os.path.isdir(path):
                t = 'directory'
                os.rmdir(path)
            else:
                t = 'file'
                os.unlink(path)
        except EnvironmentError as err:
            if (err[0] == errno.EACCES):
                if t == 'directory' and os.listdir(path):
                    raise DeleteError(HTTPResponse()._error_html(
                        'DeleteError',
                        "The directory '%s' is not empty." % id))
                else:
                    raise Forbidden(HTTPResponse()._error_html(
                        'Forbidden',
                        "Sorry, you do not have permission to delete " \
                        "the requested %s ('%s')." % (t, id)))
            else: raise

    def _copyOb(self, id, ob):
        self._setObject(id, ob)

    def _moveOb(self, id, ob):
        src = ob._local_path
        dest = self._getpath(id)
        try: 
            os.rename(src, dest)
        except EnvironmentError as err: 
            if (err[0] == errno.EACCES):
                raise Forbidden(HTTPResponse()._error_html(
                    'Forbidden',
                    "Sorry, you do not have permission to write "
                    "to this directory.<p>"))
            else: raise
        
    def _verifyObjectPaste(self, ob, REQUEST):
        pass
            
    def _write_file(self, pfile, path):
        try:
            if isinstance(pfile, str):
                outfile=open(path,'wb')
                outfile.write(pfile)
                outfile.close()
            else:
                blocksize=8*1024
                outfile=open(path,'wb')
                data=pfile.read(blocksize)
                while data:
                    outfile.write(data)
                    data=pfile.read(blocksize)
                outfile.close()
        except EnvironmentError as err: 
            if (err[0] == errno.EACCES):
                raise Forbidden(HTTPResponse()._error_html(
                    'Forbidden',
                    "Sorry, you do not have permission to write "
                    "to this directory.<p>"))
            else: raise

    def manage_createDirectory(self, path, action='manage_workspace', REQUEST=None):
        """Create a new directory relative to this directory."""
        parts = os.path.split(path)
        parts = filter(lambda p: p not in ('.','..'),parts)
        path = os.path.join(*parts)
        fullpath = os.path.join(self.basepath,path)
        if os.path.exists(fullpath):
            if REQUEST:
                return MessageDialog(
                        title='OK',
                        message='The directory already exists.',
                        action=action)
        else:
            try:
                os.makedirs(fullpath)
            except EnvironmentError as err: 
                if (err[0] == errno.EACCES):
                    raise Forbidden(HTTPResponse()._error_html(
                        'Forbidden',
                        "Sorry, you do not have permission to write "
                        "to this directory.<p>"))
                else: raise
            if REQUEST:
                return MessageDialog(
                        title='Success!',
                        message='The directory has been created.',
                        action=action)

    def manage_upload(self, file, id='', action='manage_workspace', REQUEST=None):
        """Upload a file to the local file system. The 'file' parameter
        is a FileUpload instance representing the uploaded file."""
        if hasattr(file,'filename'):
            filename = file.filename
        else:
            filename = file.name
        if not id:
            # Try to determine the filename without any path.
            # First check for a UNIX full path. There will be no UNIX path
            # separators in a Windows path.
            if '/' in filename:
                id = filename[filename.rfind('/')+1:]
            # Next check for Window separators. If there are no UNIX path
            # separators then it's probably a Windows path and not a random
            # relative UNIX path which happens to have backslashes in it.
            # Lets hope this never happens, anyway. ;)
            elif '\\' in filename:
                id = filename[filename.rfind('\\')+1:]
            # Not sure if we'll ever get a Mac path, but here goes...
            elif ':' in filename:
                id = filename[filename.rfind(':')+1:]
            # Else we have a filename with no path components so let's use 
            # that for the id.
            else:
                id = filename

        try:
            self._checkId(id,1)
        except:
            raise UploadError(MessageDialog(
                title='Invalid Id',
                message=sys.exc_value,
                action='manage_main'))
        path = self._getpath(id)
        if os.path.exists(path):
            self.manage_overwrite(file, path, REQUEST)
        else:
            self._write_file(file, path)
        if REQUEST: 
            if action == 'index_fs':
                # do not show a MessageDialog if called from PloneLocalFS
                REQUEST['RESPONSE'].redirect(self.absolute_url() + '/index_fs?portal_status_message=Your%20file%20has%20been%20uploaded')
            else:
                return MessageDialog(
                    title = 'Success!',
                    message = 'Your file has been uploaded.',
                    action = action)
    
    def manage_overwrite(self, file, path, REQUEST=None):
        """Overwrite a local file."""
        if REQUEST is None and hasattr(self, 'aq_acquire'):
            try: 
                REQUEST = self.aq_acquire('REQUEST')
            except: pass
        try: 
             user = REQUEST['AUTHENTICATED_USER']
        except:
             user = None
        if user is None or not user.has_permission('Overwrite local files', self):
            raise Unauthorized(HTTPResponse()._error_html(
                    'Unauthorized',
                    "Sorry, you are not authorized to overwrite files.<p>"))
        self._write_file(file, path)
    
    def manage_renameObject(self, id, new_id, REQUEST=None):
        """Rename a file or subdirectory."""
        try:
            self._checkId(new_id)
        except:
            raise RenameError(MessageDialog(
                      title='Invalid Id',
                      message=sys.exc_value,
                      action ='manage_main'))
        f = self._getpath(id)
        t = self._getpath(new_id)
        try:
            os.rename(f, t)
        except EnvironmentError as err:
            if (err[0] == errno.EACCES):
                if os.path.isdir(f):
                    t = 'directory'
                else:
                    t = 'file'
                raise RenameError(HTTPResponse()._error_html(
                    'RenameError'
                    "Sorry, you do not have permission to rename " \
                    "the requested %s ('%s')." % (t, id)))
            else: raise
        if REQUEST is not None:
            return self.manage_main(self, REQUEST, update_menu=1)

    def manage_cutObjects(self, ids, REQUEST=None):
        """Put a reference to the objects named in ids in the clipboard,
        marked for a cut operation."""
        if type(ids) is type(''):
            ids = [ids]
        oblist = []
        for id in ids:
            ob = self._safe_getOb(id)
            m = FileMoniker(ob)
            oblist.append(m.ids)
        cp = (1, oblist)
        cp = _cb_encode(cp)
        if REQUEST is not None:
            resp = REQUEST['RESPONSE']
            resp.setCookie('__lcp', cp, path='%s' % REQUEST['SCRIPT_NAME'])
            return self.manage_main(self, REQUEST, cb_dataValid=1)
        return cp
    
    def manage_copyObjects(self, ids, REQUEST=None, RESPONSE=None):
        """Put a reference to the objects named in ids in the clipboard,
        marked for a copy operation."""
        if type(ids) is type(''):
            ids = [ids]
        oblist = []
        for id in ids:
            ob = self._safe_getOb(id)
            m = FileMoniker(ob)
            oblist.append(m.ids)
        cp = (0, oblist)
        cp = _cb_encode(cp)
        if REQUEST is not None:
            resp = REQUEST['RESPONSE']
            resp.setCookie('__lcp', cp, path='%s' % REQUEST['SCRIPT_NAME'])
            return self.manage_main(self, REQUEST, cb_dataValid=1)
        return cp

    def manage_pasteObjects(self, cb_copy_data=None, REQUEST=None):
        """Paste objects from the clipboard into the current directory.
        The cb_copy_data parameter, if specified, should be the result 
        of a previous call to manage_cutObjects or manage_copyObjects."""
        cp = None
        if cb_copy_data is not None:
            cp = cb_copy_data
        else:
            if REQUEST and REQUEST.has_key('__lcp'):
                cp = REQUEST['__lcp']
        if cp is None:
            raise CopyError(eNoData)
        
        try: cp = _cb_decode(cp)
        except: raise CopyError(eInvalid)

        oblist = []
        m = FileMoniker()
        op = cp[0]
        for ids in cp[1]:
            m.ids = ids
            try:
                ob = m.bind(self.root or self)
            except:
                raise CopyError(eNotFound)
            self._verifyObjectPaste(ob, REQUEST)
            oblist.append(ob)

        if op == 0:
            # Copy operation
            for ob in oblist:
                id = self._get_id(absattr(ob.id))
                self._copyOb(id, ob)

            if REQUEST is not None:
                return self.manage_main(self, REQUEST, update_menu=1,
                                        cb_dataValid=1)

        if op == 1:
            # Move operation
            for ob in oblist:
                id = absattr(ob.id)
                if not sanity_check(self, ob):
                    raise CopyError('This object cannot be pasted into itself')
                id = self._get_id(id)
                self._moveOb(id, ob)

            if REQUEST is not None:
                REQUEST['RESPONSE'].setCookie('cp_', 'deleted',
                                    path='%s' % REQUEST['SCRIPT_NAME'],
                                    expires='Wed, 31-Dec-97 23:59:59 GMT')
                return self.manage_main(self, REQUEST, update_menu=1,
                                        cb_dataValid=0)
        return ''

    def cb_dataValid(self):
        """Return true if clipboard data seems valid."""
        try:
            cp = _cb_decode(self.REQUEST['__lcp'])
        except:
            return 0
        return 1
        
    def manage_delObjects(self, ids=[], REQUEST=None):
        """Delete files or subdirectories."""
        if type(ids) is type(''):
            ids=[ids]
        if not ids:
            return MessageDialog(title='No items specified',
                   message='No items were specified!',
                   action ='manage_main',)
        while ids:
            id = ids[-1]
            path = self._getpath(id)
            if not os.path.exists(path):
                raise BadRequest('%s does not exist' % ids[-1])
            self._delObject(id)
            del ids[-1]
        if REQUEST is not None:
                return self.manage_main(self, REQUEST, update_menu=1)

    def fileIds(self, spec=None):
        """Return a list of subobject ids.
        If 'spec' is specified, return only objects whose filename 
        matches 'spec'."""
        if spec is None:
            spec = self.file_filter
        return self._ids(spec)
    
    def fileValues(self, spec=None, propagate=1):
        """Return a list of Local File objects.
        If 'spec' is specified, return only objects whose filename 
        matches 'spec'."""
        if spec is None:
            spec = self.file_filter	
        r = []
        a = r.append
        g = self._getfileob
        if propagate:
            for id in self._ids(spec): a(g(id, spec))
        else:
            for id in self._ids(spec): a(g(id))
        #sort that directories come first
        res = []
        for v in r:
            s = '%s%s' %((v.type!='directory'),v.id)
            res.append((s,v))
        res.sort()    
        
        return [ x[1] for x in res ]

    def fileItems(self, spec=None, propagate=1):
        """Return a list of (id, fileobject) tuples.
        If 'spec' is specified, return only objects whose filename 
        matches 'spec'
        """
        if spec is None:
            spec=self.file_filter
        r = []
        a = r.append
        g = self._getfileob
        if propagate:
            for id in self._ids(spec): a((id, g(id, spec)))
        else:
            for id in self._ids(spec): a((id, g(id)))
        return r

    def objectIds(self, spec=None):
        """Return a list of subobject ids.

        Returns a list of subobject ids of the current object.  If 'spec' is
        specified, returns objects whose meta_type matches 'spec'.
        """
        if self.catalog:
            return self._objectIds(spec)
        return ()
    
    def objectValues(self, spec=None):
        """Return a list of the actual subobjects.

        Returns a list of actual subobjects of the current object.  If
        'spec' is specified, returns only objects whose meta_type match 'spec'
        """
        if self.catalog:
            return self._objectValues(spec)
        return ()
            
    def objectItems(self, spec=None):
        """Return a list of (id, subobject) tuples.

        Returns a list of (id, subobject) tuples of the current object.
        If 'spec' is specified, returns only objects whose meta_type match
        'spec'
        """
        if self.catalog:
            return self._objectItems(spec)
        return ()
    
    def _objectIds(self, spec=None):
        g = self._safe_getOb
        ids = self._ids()
        if spec is not None:
            if type(spec) == type('s'):
                spec = [spec]
            r = []
            a = r.append
            for id in ids:
                ob = g(id)
                if ob.meta_type in spec:
                    r.append(id)
            return r
        return ids
        
    def _objectValues(self, spec=None):
        r = []
        a = r.append
        g = self._safe_getOb
        if spec is not None:
            if type(spec) == type('s'):
                spec = [spec]
            for id in self._ids(): 
                ob = g(id)
                if ob.meta_type in spec:
                    a(g(id))
        else:
            for id in self._ids(): a(g(id))
        return r

    def _objectItems(self, spec=None):
        r = []
        a = r.append
        g = self._safe_getOb
        if spec is not None:
            if type(spec) == type('s'):
                spec = [spec]
            for id in self._ids(): 
                ob = g(id)
                if ob.meta_type in spec:
                    a((id, g(id)))
        else:
            for id in self._ids(): a((id, g(id)))
        return r
    
    def tpValues(self):
        """Returns a list of the folder's sub-folders, used by tree tag."""
        r = []
        try:
            for id in self._ids():
                o = self._safe_getOb(id)
                try: 
                    if o.isPrincipiaFolderish: r.append(o)
                except: pass
        except: pass
        return r

    def tpId(self): return encode_str(self.serverPath())
    
    def serverPath(self):
        """Return the full path of the directory object relative to the
        root of the server."""
        ids = []
        ob = self
        while 1:
            if not hasattr(ob, 'id'): break
            ids.append(absattr(ob.id))
            if not hasattr(ob, 'aq_parent'): break
            ob = ob.aq_parent
        ids.reverse()
        return '/'.join(ids)

    def parentURL(self):
        """Return the URL of the parent directory."""
        url = self.REQUEST['URL2']
        spec = self.REQUEST.get('spec', None)
        if (spec is not None):
            if (type(spec) is type('')):
                url = '%s?spec=%s' % (url, quote(spec))
            else:
                query = []
                for patt in spec:
                    query.append('spec:list=%s' % quote(patt))
                url = url + '?' + '&'.join(query)
        return url

    def defaultDocument(self):
        """Return the first default document found in this folder 
        as a Zope object or None."""
        #***Andreas Don#t know why but self.default_document is sometimes empty
        try:
            files = self.default_document.split()
            for file in files:
                path = self._getpath(file)
                if os.path.isfile(path):
                    try:
                        return self._safe_getOb(file)
                    except Forbidden: pass
        except:
            pass
        return None
                
    def bobobase_modification_time(self):
        t = os.stat(self._local_path)[stat.ST_MTIME]
        return DateTime(t)

    #
    #  FTP Support - sbk
    #
    
    security.declarePrivate('PUT_factory')
    def PUT_factory( self, name, typ, body ):
        """
        Dispatcher for PUT requests to non-existent IDs.
        """
        if name and (name.endswith('.pt') or name.endswith('.zpt')):
           from Products.PageTemplates.ZopePageTemplate import ZopePageTemplate
           ob = ZopePageTemplate(name, body, content_type=typ)
        elif typ in ('text/html', 'text/xml', 'text/plain'):
           from OFS.DTMLDocument import DTMLDocument
           if type(body) is not type(''):
               body=body.read()
           ob = DTMLDocument( body, __name__=name )
        elif typ[:6]=='image/':
           from OFS.Image import Image
           ob = Image(name, '', body, content_type=typ)
        else:
           from OFS.Image import File
           ob = File(name, '', body, content_type=typ)
        return ob

    def manage_FTPlist(self,REQUEST):
        """Directory listing for FTP"""
        out = ()
        files = list(self.fileItems())
        try:
            if not (hasattr(self,'isTopLevelPrincipiaApplicationObject')\
               and self.isTopLevelPrincipiaApplicationObject):
                files.insert(0,('..',self.aq_parent))
        except:
            pass
        for k,v in files:
            try:
                stat = marshal.loads(v.manage_FTPstat(REQUEST))
            except:
                stat = None
            if stat is not None:
                out = out+((k,stat),)
        return marshal.dumps(out)

    def manage_FTPstat(self,REQUEST):
        """Psuedo stat used for FTP listings"""
        mode = 0o040000 | 0o770
        mtime = self.bobobase_modification_time().timeTime()
        owner = group = 'Zope'
        return marshal.dumps((mode, 0, 0, 1, owner, group, 0, mtime, mtime, mtime))


class LocalFile(OFS.SimpleItem.Item, Acquisition.Implicit):

    """Object representing a file in the local file system."""

    meta_type='Local File'

    security = AccessControl.ClassSecurityInfo()

    security.declareProtected('FTP access', 'manage_FTPstat', 'manage_FTPget',
        'manage_FTPlist', 'PUT', 'manage_FTPput')  # sbk
    
    def __init__(self, parent, id, path, spec):
        """LocalFile __init__"""
        self.parent = parent
        self.id = id
        self.path = path
        self.type = self._getType()
        self.url = self._getURL(spec)
        self.plain_url = self._getPlainURL()
        self.icon = self._getIcon()
        self.size = self._getSize()
        self.mtime = self._getTime()
        self.display_size = self._getDisplaySize()
        self.display_mtime = self._getDisplayTime()

    def getDefaultDocumentPath(self, target, default=''):
        """Return true if is Directory and has default doc"""
        if self.type != 'directory':
            return target
        targetpath = os.path.join(target, default)
        default_documents = self.parent.default_document
        if type(default_documents) == type(' '):
            default_documents = default_documents.split(' ')
        for file in default_documents:
            path = os.path.join(self.path, file)
            if (os.path.isfile(path)):
                return os.path.join(target, file)
        return os.path.join(target, default)

    def getObject(self):
        """Return a Zope object representing this local file."""
        return self.parent._safe_getOb(self.id)

    def get_size(self):
        """Return the size of the file."""
        return self.size
                
    def bobobase_modification_time(self):
        """bobobase_modification_time"""
        return self.mtime

    def _getURL(self, spec):
        """_getURL"""
        url = quote(self.id)
        if (self.type == 'directory') and (spec is not None):
            if (type(spec) is type('')):
                return url + '?spec=' + quote(spec)
            else:
                query = []
                for patt in spec:
                    query.append('spec:list=%s' % quote(patt))
                return url + '?' + '&'.join(query)
        return url

    def _getPlainURL(self):
        """_getPlainURL"""
        return quote(self.id)
        
    def _getType(self):
        """Return the content type of a file."""
        name = self.id
        path = self.path
        if (os.path.isdir(path)): return 'directory'
        ext = os.path.splitext(name)[-1]
        t, c = _get_content_type(ext, self.parent._type_map)
        if t: return t
        try:
            f = open(path, 'rb')
            data = f.read(_test_read)
            f.close()
            ob = OFS.Image.File(name, name, data)
            _set_content_type(ob, t, data)
            return ob.content_type
        except EnvironmentError:
            return 'application/octet-stream'

    def _getIcon(self):
        """Return the path of the icon associated with this file type."""
        content_type = self.type.lower()
        _icon_map = self.parent._icon_map
        try:
            return _icon_map[content_type]
        except KeyError:
            pass
        content_type = content_type[:content_type.find('/')]
        try:
            return _icon_map[content_type]
        except KeyError:
            pass
        return _icon_base + 'generic.gif'

    def _getSize(self):
        """Return the size of the specified file or -1 if an error occurs.
        Return None if the path refers to a directory."""
        path = self.path
        if (os.path.isdir(path)):
            return None
        try:
            return os.stat(path)[stat.ST_SIZE]
        except:
            return -1

    def _getDisplaySize(self):
        """Return the size of a file or directory formatted for display."""
        size = self.size
        if size is None:
            return '-' * 5
        if size == -1:
            return _unknown
        k = 1024.0
        if (size > k):
            size = size / k
            if (size > k):
                size = size / k
                return '%.1f MB' % size
            else:
                return '%.1f kB' % size
        else:
            return '%d bytes' % size

    def _getTime(self):
        """Return the last modified time of a file or directory
        or None if an error occurs."""
        try:
            return DateTime(os.stat(self.path)[stat.ST_MTIME])
        except:
            pass

    def _getDisplayTime(self):
        """Return the last modified time of a file or directory formatted 
        for display."""
        t = self.mtime
        if t is None:
            return _unknown
        return '%s %s' % (t.Time(), t.Date())

    #
    # FTP Support - sbk
    #
    
    def PUT(self, REQUEST, RESPONSE):
        """ Handle HTTP PUT requests """
        self.dav__init(REQUEST, RESPONSE)
        self.dav__simpleifhandler(REQUEST, RESPONSE, refresh=1)
        self.write(REQUEST.get('BODY', ''))
        self.ZCacheable_invalidate()
        RESPONSE.setStatus(204)
        return RESPONSE

    manage_FTPput = PUT

    def manage_FTPget(self):
        """Get source for FTP download"""
        self.REQUEST.RESPONSE.setHeader('Content-Type', self.content_type)
        return self.read()
    
    getSize = get_size

    def manage_FTPstat(self,REQUEST):
        """Pseudo stat used for FTP listings"""
        size = self._getSize()
        mode = 0o100000 | 0o660
        if self.type == 'directory':
            size = 0
            mode = 0o040000 | 0o770
        mtime = self.bobobase_modification_time().timeTime()
        owner = group = 'Zope'
        return marshal.dumps((mode, 0, 0, 1, owner, group, size, mtime, mtime, mtime))


class FileMoniker:

    """A file moniker is a reference to an object in the file system."""
    
    def __init__(self, ob=None):
        """FileMoniker __init__"""
        if ob is None:
            return
        self.ids = []
        while 1:
            if not hasattr(ob, 'id'):
                break
            if ob.meta_type == 'Local File System':
                break
            self.ids.append(absattr(ob.id))
            ob = ob.aq_parent
        self.ids.reverse()

    def bind(self, root):
        """Return the file object named by this moniker"""
        ob = root
        for id in self.ids:
            ob = ob._safe_getOb(id)
        return ob


class LocalFS(
    LocalDirectory,
    OFS.PropertyManager.PropertyManager,
    Persistence.Persistent,
    RoleManager
    ):

    """Object that creates Zope objects from files in the local file system."""

    meta_type = 'Local File System'
    
    manage_options = (
        (
        {'label': 'Contents', 'action': 'manage_main',
         'help': ('LocalFS', 'FileSystem_Contents.stx')},
        {'label': 'View', 'action': '',
         'help': ('LocalFS', 'FileSystem_View.stx')},
        {'label': 'Properties', 'action': 'manage_propertiesForm',
         'help': ('LocalFS', 'FileSystem_Properties.stx')},
        {'label': 'Security', 'action': 'manage_access',
         'help': ('LocalFS', 'FileSystem_Security.stx')},
        {'label': 'Upload', 'action': 'manage_uploadForm',
         'help': ('LocalFS', 'FileSystem_Upload.stx')},
        )
    )

    __ac_permissions__ = (
        ('View', ('',)),
        ('View Directory Index', ('index_html',)),
        ('View management screens', 
            ('manage', 'manage_main')),
        ('Change Local File System properties', 
            ('manage_propertiesForm', 'manage_changeProperties')),
        ('Access contents information', 
            ('fileIds', 'fileValues', 'fileItems')),
        ('Upload local files',
            ('manage_uploadForm', 'manage_upload')), # ***SmileyChris no WAY should anonymous be allowed to upload by default!
        ('Overwrite local files', ('manage_overwrite',)),
        ('Manage local files', 
            ('manage_cutObjects', 'manage_copyObjects', 'manage_pasteObjects',
            'manage_renameForm', 'manage_renameObject', 
            'manage_createDirectory', )),
        ('Delete local files', ('manage_delObjects',)),
        )
    
    _properties=(
        {'id': 'title', 'type': 'string', 'mode': 'w'},
        {'id': 'basepath', 'type': 'string', 'mode': 'w'},
    )
    if (_iswin32): _properties = _properties + (
        {'id': 'username', 'type': 'string', 'mode': 'w'},
        {'id': 'password', 'type': 'string', 'mode': 'w'},
    )
    _properties = _properties + (
        {'id': 'default_document', 'type': 'string', 'mode': 'w'},
        {'id': 'type_map', 'type': 'lines', 'mode': 'w'},
        {'id': 'icon_map', 'type': 'lines', 'mode': 'w'},
        {'id': 'catalog', 'type': 'boolean', 'mode': 'w'},
        {'id': 'tree_view', 'type': 'boolean', 'mode': 'w'},
        {'id': 'file_filter', 'type': 'string', 'mode': 'w'},	
    )

    default_document = 'index.html default.html'
    username = _share = ''
    _connected = 0
    tree_view = 0 # ***SmileyChris was 1 - changed because it can slow ZMI down quite a bit
    isPrincipiaFolderish = 1  #SmileyChris *** leaving this at one though
    catalog = 0
    root = None
    password = ''
    
    _type_map = _types
    _icon_map = _icons
    type_map = _typemap2list(_types)
    icon_map = _iconmap2list(_icons)
    file_filter = None
    
    def __init__(self, id, title, basepath, username, password):
        """LocalFS __init__"""
        LocalDirectory.__init__(self, id, basepath, self, self.tree_view, 
            self.catalog, self._type_map, self._icon_map, self.file_filter)
        self.title = title
        self.basepath = self._local_path = basepath
        if (_iswin32):
            self.username = username
            self._password = password
            m = unc_expr.match(self.basepath)
            if (m is not None) and (self.username):
                self._share = m.group(1)
                self._connect()
            else:
                self._share = ''

    def manage_editProperties(self, REQUEST):
        """Edit object properties via the web.
        The purpose of this method is to change all property values,
        even those not listed in REQUEST; otherwise checkboxes that
        get turned off will be ignored. Use manage_changeProperties()
        instead for most situations.
        """
        type_map = self.type_map
        icon_map = self.icon_map
        file_filter = self.file_filter
        if (_iswin32):
            username = self.username
            password = self._password

        OFS.PropertyManager.PropertyManager.manage_editProperties(self, REQUEST)

        if self.file_filter.strip() == '':
            self.file_filter = None
        if self.file_filter == 'None':
            self.file_filter = None
        if self.type_map != type_map:
            self._type_map = _list2typemap(self.type_map)
        if self.icon_map != type_map:
            self._icon_map = _list2iconmap(self.icon_map)
        if (_iswin32):
            if self.username != username or self.password != password:
                self._password = password
                if (self._connected):
                    self._disconnect()
                m = unc_expr.match(self.basepath)
                if (m is not None) and (self.username):
                    self._share = m.group(1)
                    self._connect()
                else:
                    self._share = ''
            self.password = ''
        self.isPrincipiaFolderish = 1
        message = "Saved changes."
        return self.manage_propertiesForm(self, REQUEST,
           manage_tabs_message=message, update_menu=1)

    def manage_changeProperties(self, REQUEST=None, **kw):
        """Change existing object properties.

        Change object properties by passing either a mapping object
        of name:value pairs {'foo':6} or passing name=value parameters
        """
        type_map = self.type_map
        icon_map = self.icon_map
        if (_iswin32):
            username = self.username
            password = self._password
        OFS.PropertyManager.PropertyManager.manage_changeProperties(self, REQUEST, **kw)
        if self.type_map != type_map:
            self._type_map = _list2typemap(self.type_map)
        if self.icon_map != type_map:
            self._icon_map = _list2iconmap(self.icon_map)
        if (_iswin32):
            if self.username != username or self._password != password:
                if (self._connected):
                    self._disconnect()
                m = unc_expr.match(self.basepath)
                if (m is not None) and (self.username):
                    self._share = m.group(1)
                    self._connect()
                else:
                    self._share = ''
        self.isPrincipiaFolderish = self.tree_view
            
    def _connect(self):
        """_connect"""
        win32wnet.WNetAddConnection2(1, None, self._share, None, 
            self.username or None, self._password or None)
        self._connected = 1

    def _disconnect(self):
        """_disconnect"""
        win32wnet.WNetCancelConnection2(self._share, 0, 0)
        self._connected = 0

    def _check_connected(self):
        """_check_connected"""
        if (self._share and not self._connected):
            self._connect()

    def _ids(self, spec=None):
        """_ids"""
        self._check_connected()
        return LocalDirectory._ids(self, spec)

    def _getfileob(self, id, spec=None):
        """_getfileob"""
        self._check_connected()
        return LocalDirectory._getfileob(self, id, spec)

    def _getOb(self, id, default=_marker):
        """_getOb"""
        self._check_connected()
        return LocalDirectory._getOb(self, id, default)

    def bobobase_modification_time(self):
        """bobobase_modification_time"""
        return Persistence.Persistent.bobobase_modification_time(self)

    def hasDefaultDocument(self):
        """Return true if is Directory and has default doc"""
        # self.default_document is sometimes empty
        try:
            files = self.default_document.split()
            for file in files:
                path = self._getpath(file)
                if (os.path.isfile(path)):
                    try:
                        return self._safe_getOb(file)
                    except Forbidden:
                        pass
        except:
            pass
        return None


def manage_addLocalFS(self, id, title, basepath, 
    username=None, password=None, REQUEST=None):
    """Add a local file system object to a folder
  
    In addition to the standard Zope object-creation arguments,
    'id' and 'title', the following arguments are defined:

        basepath -- The base path of the local files.
        username -- Username for a network share (win32 only).
        password -- Password for a network share (win32 only).
    """
    
    ob = LocalFS(id, title, basepath, username, password)
    self._setObject(id, ob)
    if REQUEST is not None:
        return self.manage_main(self, REQUEST)

manage_addLocalFSForm = HTMLFile('dtml/methodAdd', globals())

