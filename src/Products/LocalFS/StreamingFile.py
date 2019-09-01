"""Local File System product"""
__doc__="""Local File System product"""

from OFS.Image import File, Image, Pdata
from cStringIO import StringIO
from ZPublisher.HTTPRequest import FileUpload

BUFFER_SIZE = 1 << 16

def _read_data(self, file):
    # We do not want to load the whole file into memory, so just
    # get the file size and return a faked Pdata object.
    if isinstance(file, str):
        size = len(file)
        if size < BUFFER_SIZE: return file, size
        # Big string: cut it into smaller chunks
        file = StringIO(file)

    if isinstance(file, FileUpload) and not file:
        raise ValueError, 'File not specified'

    if hasattr(file, '__class__') and file.__class__ in (Pdata, Sdata):
        size = len(file)
        return file, size
    
    pos = file.tell()
    file.seek(0, 2)
    size = file.tell()
    file.seek(pos, 0)
    return Sdata(file, size), size


class StreamingFile(File):
    """ Wrapper around OFS.Image.File """
    _read_data = _read_data


class StreamingImage(Image):
    """ Wrapper around OFS.Image.Image """
    _read_data = _read_data


class Sdata(Pdata):
    """ Streaming wrapper for possibly large data """
    # Imitates OFS.Image.Pdata
    # Make it a subclass of Pdata to be conform with ExternalEditor

    _p_changed = 0

    def __init__(self, file, fsize, _offset=0):
        self.file = file
        self.fsize = fsize
        self.offset = _offset

    @property
    def data(self):
        return self[0:BUFFER_SIZE]

    @property
    def next(self):
        offset = self.offset + BUFFER_SIZE
        if offset < self.fsize:
            return Sdata(self.file, self.fsize, offset)

    def __getslice__(self, i, j):
        size = min(BUFFER_SIZE, len(self))
        if i < 0:
            i = max(i + size, 0)
        j = min(j, size)
        if j < 0:
            j = max(j + size, 0)
        if i >= j:
            return ''
        self.file.seek(self.offset+i, 0)
        return self.file.read(j-i)

    def __len__(self):
        return self.fsize - self.offset

    def __str__(self):
        self.file.seek(self.offset, 0)
        return self.file.read()

