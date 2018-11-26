import struct
from collections import namedtuple
import gzip

import logging
import warnings
import io

from .file_types import is_gzip, is_eventio
from .exceptions import WrongTypeException
from .tools import read_from

log = logging.getLogger(__name__)

known_objects = {}


class EventIOFile:

    def __init__(self, path):
        log.info('Opening new file {}'.format(path))
        self.path = path
        self.__file = open(path, 'rb')

        if not is_eventio(path):
            raise ValueError('File {} is not an eventio file'.format(path))

        if is_gzip(path):
            log.info('Found gzipped file')
            self.__compfile = gzip.GzipFile(mode='r', fileobj=self.__file)
            self.__filehandle = io.BufferedReader(self.__compfile)
        else:
            log.info('Found uncompressed file')
            self.__filehandle = self.__file

        self.objects = read_all_headers(self, toplevel=True)
        log.info(
            'File contains {} top level objects'.format(len(self.objects))
        )

    def __len__(self):
        return len(self.objects)

    def seek(self, position, whence=0):
        return self.__filehandle.seek(position, whence)

    def tell(self):
        return self.__filehandle.tell()

    def read(self, size=-1):
        return self.__filehandle.read(size)

    def read_from_position(self, first_byte, size):
        pos = self.__filehandle.tell()
        self.seek(first_byte)
        data = self.read(size)
        self.seek(pos)
        return data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.__file.close()

    def __getitem__(self, idx):
        return self.objects[idx]

    def __iter__(self):
        return iter(self.objects)

    def __repr__(self):
        repr_ = '{}(path={}, objects=[\n'.format(
            self.__class__.__name__,
            self.path
        )

        if len(self.objects) <= 8:
            for object_ in self.objects:
                repr_ += '  {}\n'.format(object_)
        else:
            for object_ in self.objects[:4]:
                repr_ += '  {}\n'.format(object_)
            repr_ += '\t...\n'
            for object_ in self.objects[-4:]:
                repr_ += '  {}\n'.format(object_)
        repr_ += '])'
        return repr_


class EventIOObject:
    '''Base Classe for classes representing an eventio object

    EventIO objects can basically play two roles:
        - a binary or ascii data blob
        - a hierarchy level, i.e. an object *is* a "list" of other objects

    But also combinations are allowed, so an object can be a "list" of other
    objects and contain some data itself.

    If an object contains only sub objects and therefore can be thought
    of as a simple list or not is marked by the boolean field
    `header.only_sub_objects`.

    FIXME:
    If an object only contains sub objects the constructor of this
    base class does already something, which might not be wanted:
        It will start parsing all sub objects.
    We might want to review this design decision.

    FIXME 2:
    This object is a kind of hybrid of an object and a file, since it
    exhibits a typical file like interface of "seek, read, tell"
    AND it exhibits EventIo objects features like: header, data, sub-objects.
    I think now, that this hybridization or mixing of features is not good.
    It makes it very difficult to understand what is going on.
    We should most certainly review this design decision.
    '''
    eventio_type = None

    def __init__(self, eventio_file, header, first_byte):
        if header.type != self.eventio_type:
            raise WrongTypeException(self.eventio_type, header.type)

        self.eventio_file = eventio_file
        self.first_byte = first_byte
        self.header = header
        self.position = 0

        self.objects = []

        if self.header.only_sub_objects:
            self.objects = read_all_headers(self, toplevel=False)

    def __getitem__(self, idx):
        '''In case an object contains only subobjects it can be thought
        of as a simple list. One can get sub objects by their integer indices.
        '''
        return self.objects[idx]

    def parse_data_field(self):
        ''' Read the data in this field

        should return nice python objects, e.g. structured numpy arrays

        If an object does not only contain sub objects, the binary data field
        needs to be somehow parsed into understandable data, e.g. a numpy array
        or so.
        We think concrete implementations inheriting from EventIOObject should
        take care of this parsing.
        '''
        raise NotImplementedError

    def __repr__(self):
        if len(self.objects) > 0:
            subitems = ', subitems={}'.format(len(self.objects))
        else:
            subitems = ''

        return '{}(first={}, length={}{})'.format(
            self.__class__.__name__,
            self.first_byte,
            self.header.length,
            subitems,
        )

    def read(self, size=-1):
        '''returns the binary data bolb of this object

        FIXME: I do not understand this first `if` anymore.
        I assume it has something to do with sub objects.
        '''
        if size == -1 or size > self.header.length - self.position:
            size = self.header.length - self.position

        data = self.eventio_file.read_from_position(
            first_byte=self.header.data_field_first_byte + self.position,
            size=size,
        )

        self.position += size

        return data

    def read_from_position(self, first_byte, size):
        '''read `size` bytes from address `first_byte`
        from `self.eventio_file`.

        NOTE: Seeks back to current position.
        '''
        pos = self.tell()
        self.seek(first_byte)
        data = self.read(size)
        self.seek(pos)
        return data

    def seek(self, offset, whence=0):
        '''manipulate `self.position` without really histting the disk'''
        if whence == 0:
            assert offset >= 0
            self.position = offset
        elif whence == 1:
            self.position += offset
        elif whence == 2:
            self.position = self.header.length + offset
        else:
            raise ValueError(
                'invalid whence ({}, should be 0, 1 or 2)'.format(whence)
            )
        return self.position

    def tell(self):
        return self.position


class UnknownObject(EventIOObject):
    def __init__(self, eventio_file, header, first_byte):
        self.eventio_type = header.type
        super().__init__(eventio_file, header, first_byte)

    def __repr__(self):
        _, *last = super().__repr__().split('(first')

        return '{}[{}](first'.format(
            self.__class__.__name__, self.eventio_type
        ) + ''.join(last)

SYNC_MARKER_INT_VALUE = -736130505

def parse_sync_bytes(sync):
    ''' returns the endianness as given by the sync byte '''

    int_value, = struct.unpack('<i', sync)
    if int_value == SYNC_MARKER_INT_VALUE:
        log.debug('Found Little Endian byte order')
        return '<'

    int_value, = struct.unpack('>i', sync)
    if int_value == SYNC_MARKER_INT_VALUE:
        log.debug('Found Big Endian byte order')
        return '>'

    raise ValueError(
        'Sync must be 0xD41F8A37 or 0x378A1FD4. Got: {}'.format(sync)
    )





# Base class for ObjectHeader below
# together they implement a namedtuple with a constructor so an
# ObjectHeader can be created from an `f` and a `parent`
# using `reader_header()`
HeaderBase = namedtuple(
    'HeaderBase',
    [
        'endianness',
        'type',
        'version',
        'user',
        'extended',
        'only_sub_objects',
        'length',
        'id',
        'data_field_first_byte',
        'level'
    ]
)


class ObjectHeader(HeaderBase):
    '''Simple namedtuple with the same fields as HeaderBase above
    can be created from an `file` and a `parent` using `read_header()`
    '''
    def __new__(cls, file, parent=None):
        self = super().__new__(
            cls,
            *read_header(file, parent)
        )
        return self


def read_header(file, toplevel):
    '''parse header fields from file-like `file`.

    Paramters:
    ----------

    file : filelike
        The file to read from

    toplevel: boolean
        flag telling us if the object this header belogs to
        is a so called toplevel object or not.
        Depending on this flag the header needs to be parsed differently

    Returns:
        header fields are returned as a tuple
    '''
    if toplevel is True:
        sync = file.read(4)
        endianness = parse_sync_bytes(sync)
        level = 0
    else:
        endianness = file.header.endianness
        level = file.header.level + 1

    if endianness == '>':
        raise NotImplementedError(
            'Big endian byte order is not supported by this reader'
        )

    type_version_field = read_type_field(file)
    id_field = read_from('<I', file)[0]
    only_sub_objects, length = read_length_field(file)

    if type_version_field.extended:
        length += read_extension(file)

    data_field_first_byte = file.tell()
    return_value = (
        endianness,
        type_version_field.type,
        type_version_field.version,
        type_version_field.user,
        type_version_field.extended,
        only_sub_objects,
        length,
        id_field,
        data_field_first_byte,
        level,
    )

    return return_value


def read_all_headers(eventio_file_or_object, toplevel=True):
    '''parses list of header objects from an eventio file (or object).

    Iterates over the entire file (or internal objects) and populates a list
    of `objects` which is returned.

    FIXME:
    This function is named "read all headers" but we return a list of "objects"
    So we clearly use "header" and "object" somehow interchangably here.
    This is possibly not good.
    However, the "objects" we return here contain all information:
     - start address & length
     - object type

    In order to be converted into more useful objects later on. In that sense
    the "objects" we represent here, do really represent the objects stored in
    the file.

    FIXME 2:
    The whole idea here is to parse headers in this file and store "jump"
    addresses, so that some later code can directly jump to certain objects
    and thus allow for random even access.
    This feature is most certainly not needed in any real application, but it
    does not come at zero cost. Quite the opposite. This feature costs a lot
    of time at the start.
    Especially if one wants to read only the Runheader or so, it is absolutely
    not necessary to parse all headers.

    However for fellow Python developers who want to explore this low level
    structure and implement or debug object parsers, this thing might be
    useful.

    FIXME 3:

    '''
    eventio_file_or_object.seek(0)
    objects = []
    while True:
        position = eventio_file_or_object.tell()
        try:
            header = ObjectHeader(
                eventio_file_or_object,
                toplevel,
            )
            log.debug(
                'Found header of type {} at byte {}'.format(
                    header.type,
                    position
                )
            )
            eventio_object = known_objects.get(header.type, UnknownObject)(
                eventio_file=eventio_file_or_object,
                header=header,
                first_byte=position,
            )
            objects.append(eventio_object)
            eventio_file_or_object.seek(header.length, 1)
        except ValueError:
            warnings.warn('File seems to be truncated')
            break
        except struct.error:
            break

    return objects


# The following functions perform bit magic.
# they extract some N-bit words and 1-bit 'flags' from 32bit words
# So we need '(LEN)GTH' and '(POS)ITION' to find and extract them.
# both LEN and POS are measured in bits.
# POS starts at zero of course.

TYPE_LEN = 16
TYPE_POS = 0

USER_LEN = 1
USER_POS = 16

EXTENDED_LEN = 1
EXTENDED_POS = 17

VERSION_LEN = 12
VERSION_POS = 20

ONLYSUBOBJECTS_LEN = 1
ONLYSUBOBJECTS_POS = 30

LENGTH_LEN = 30
LENGTH_POS = 0

EXTENSION_LEN = 12
EXTENSION_POS = 0


def bool_bit_from_pos(uint32_word, pos):
    '''parse a Python Boolean from a bit a position `pos` in an
    unsigned 32bit integer.
    '''
    return bool(uint32_word & (1 << pos))


def len_bits_from_pos(uint32_word, len, pos):
    '''return a range of bits from the input word
    which bits to return are defined by a position `pos` and a length `len`

    assume the input word was:
        MSB                                    LSB
        0000_0000__0000_0000__1010_1100__0000_0000

    and pos=10 and len=4

    the return value would be: 1011 (with leading zeros)
    '''
    return (uint32_word >> pos) & ((1 << len) - 1)


TypeInfo = namedtuple('TypeInfo', 'type version user extended')


def read_type_field(file):
    '''parse TypeInfo from `file`

    TypeInfo is encoded in a 32bit word.

    NOTE: this advances the file pointer by 4bytes
    '''
    uint32_word = read_from('<I', file)[0]
    _type = len_bits_from_pos(uint32_word, TYPE_LEN, TYPE_POS)
    user_bit = bool_bit_from_pos(uint32_word, USER_POS)
    extended = bool_bit_from_pos(uint32_word, EXTENDED_POS)
    version = len_bits_from_pos(uint32_word, VERSION_LEN, VERSION_POS)
    return TypeInfo(_type, version, user_bit, extended)


def read_length_field(file):
    '''parse the "length field" from `file`.

    The length field contains:

     - only_sub_objects: boolean
        This field tells us if the current object only consists of subobjects
        and does not contain any data on its own.
     - length: unsigend integer (32bit?)
        The length of this object in bytes(?). I currently do not remember
        if the length is counted from *before* then "length field" or from
        *just after* the "length field".
    '''
    uint32_word = read_from('<I', file)[0]
    only_sub_objects = bool_bit_from_pos(uint32_word, ONLYSUBOBJECTS_POS)
    length = len_bits_from_pos(uint32_word, LENGTH_LEN, LENGTH_POS)
    return only_sub_objects, length


def read_extension(file):
    '''parse the so called "extension" field from `file`

    FIXME:
    This one is difficult to describe... which might be a hint for bad design.

    The length of an object can be so large, that it cannot be hold by the
    original `length` field which is 30bits long.
    In that case the most significant part of the length is stored in the
    so called "extension" field. The extension is 12bits long.

    So the total length of the object is:
        real_length = extension * 2^30 + original_lenth

    This function returns the extension *already multiplied with 2^30*
    so that the original length can simply be added to the result of this
    function in order to get the real length of the object.
    '''
    uint32_word = read_from('<I', file)[0]
    extension = len_bits_from_pos(uint32_word, EXTENSION_LEN, EXTENSION_POS)
    # we push the length-extension so many bits to the left,
    # i.e. we multiply with such a high number, that we can simply
    # use the += operator further up in `ObjectHeader_from_file` to
    # combine the normal (small) length and this extension.
    extension <<= LENGTH_LEN
    return extension
