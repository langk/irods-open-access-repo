import hashlib
import io
import tarfile
import zipfile
import logging
from collections import OrderedDict

from tqdm import tqdm
from io import RawIOBase
from requests_toolbelt.multipart.encoder import CustomBytesIO, encode_with
from requests.utils import super_len

logger = logging.getLogger('iRODS to Dataverse')
BLOCK_SIZE = 1024 * io.DEFAULT_BUFFER_SIZE


class MultiPurposeReader:
    """
    Custom multi-part reader.
    Update the chksums while reading the buffer by chunk.
    """

    def __init__(self, buffer, length, md5, sha):
        self.len = None if length is None else int(length)
        self._raw = buffer
        self.md5 = md5
        self.sha = sha
        self.bar = tqdm(total=length, unit="bytes", smoothing=0.1, unit_scale=True)

    def read(self, chunk_size):
        force_size = 1024 * io.DEFAULT_BUFFER_SIZE

        if chunk_size == -1 or chunk_size == 0 or chunk_size > force_size:
            chunk = self._raw.read(chunk_size) or b''
        else:
            chunk = self._raw.read(force_size) or b''

        self.len -= len(chunk)

        if not chunk:
            self.len = 0

        self.md5.update(chunk)
        self.sha.update(chunk)
        self.bar.update(len(chunk))

        return chunk


class IteratorAsBinaryFile(object):
    """
    Custom bundle iterator for streaming.
    <requests_toolbelt.streaming_iterator>
    """

    def __init__(self, size, iterator, md5, encoding='utf-8'):
        #: The expected size of the upload
        self.size = int(size)
        self.md5 = md5
        if self.size < 0:
            raise ValueError(
                'The size of the upload must be a positive integer'
            )

        #: Attribute that requests will check to determine the length of the
        #: body. See bug #80 for more details
        self.len = self.size
        #: The iterator used to generate the upload data
        self.iterator = iterator

        #: Encoding the iterator is using
        self.encoding = encoding

        # The buffer we use to provide the correct number of bytes requested
        # during a read
        self._buffer = CustomBytesIO()

    def _get_bytes(self):
        try:
            return encode_with(next(self.iterator), self.encoding)
        except StopIteration:
            return b''

    def _load_bytes(self, size):
        self._buffer.smart_truncate()
        amount_to_load = size - super_len(self._buffer)
        bytes_to_append = True

        while amount_to_load > 0 and bytes_to_append:
            bytes_to_append = self._get_bytes()
            amount_to_load -= self._buffer.append(bytes_to_append)

    def read(self, size=-1):
        size = int(size)
        if size == -1:
            return b''.join(self.iterator)

        self._load_bytes(size)
        s = self._buffer.read(size)
        if not s:
            self.len = 0

        if size < 0:
            self.len = 0

        self.md5.update(encode_with(s, self.encoding))
        return encode_with(s, self.encoding)


class UnseekableStream(RawIOBase):
    """
    Custom raw buffer for streaming.
    """

    def __init__(self):
        self._buffer = b''

    def writable(self):
        return True

    def write(self, b):
        if self.closed:
            raise ValueError('Stream was closed!')
        self._buffer += b
        return len(b)

    def get(self):
        chunk = self._buffer
        self._buffer = b''
        return chunk


def archive_generator(func, stream, bar):
    """
    Yield the raw buffer for streaming.

    :param func: archive buffer iterator
    :param stream: raw buffer stream
    :param bar: progress monitor
    :return:
    """

    try:
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            with open("debug_archive", 'wb') as out_fp:
                for i in func:
                    s = stream.get()
                    if len(s) > 0:
                        bar.update(len(s))
                        out_fp.write(s)
                        out_fp.flush()
                        yield s
        else:
            for i in func:
                s = stream.get()
                if len(s) > 0:
                    bar.update(len(s))
                    yield s
    except StopIteration:
        return b''


def sort_path_by_size(collection):
    sort = {}
    for coll, sub, files in collection.walk():
        for file in files:
            sort.update({file.size: file})

    return OrderedDict(sorted(sort.items(), reverse=True, key=lambda t: t[0]))


def calc_zip_block(size, file_path, zip64):
    """
    Calculate the zip member block size.

    :param int size: file size
    :param str file_path: file path
    :param boolean zip64: Flag for zip64 format, if True add extra header
    :return: int - zip member block size
    """

    # File size
    zip_size = size
    if size >= zipfile.ZIP64_LIMIT:
        # Local file header  30 + Length filename + Data descriptor 24 + ZIP64 extra_data 20
        zip_size += 30 + len(file_path) + 24 + 20
        # Central directory file header 46 + Length filename  + ZIP64 extra_data 20
        zip_size += 46 + len(file_path)
        if zip64:
            # Central directory ZIP64 28
            zip_size += 28
        else:
            # First Central directory ZIP64  20
            zip_size += 20

    else:
        # Local file header  30 + Length filename + Data descriptor ZIP64 - 24 + ZIP64 extra_data - 20
        zip_size += 30 + len(file_path) + 24 + 20
        # Central directory file header 46 + Length filename
        zip_size += 46 + len(file_path)
        if zip64:
            # Central directory ZIP64 - 12
            zip_size += 12
        # else:
        #     # Data descriptor - 16
        #     zip_size += 16

    return zip_size


def collection_zip_preparation(collection, rulemanager, upload_success):
    """
    Walk through the collection. Request iRODS file chksums.
    Return a list of all the file's path and the estimated zip size.

    :param <irods.manager.collection_manager.CollectionManager> collection: iRODS collection to evaluate
    :param <irodsManager.irodsRuleManager.RuleManager> rulemanager: RuleManager to call chksums rule
    :param dict upload_success: {file_path: hash_key}
    :return: (list, int)
    """

    data = []
    size = 0
    sorted_path = sort_path_by_size(collection)
    zip64 = False
    for file_size, file in sorted_path.items():
        data.append(file)
        size += calc_zip_block(file_size, file.path, zip64)

        if size >= zipfile.ZIP64_LIMIT:
            zip64 = True

        irods_hash_decode = rulemanager.rule_checksum(file.path)
        logger.info(f"{'--':<30}iRODS {file.name} SHA-256: {irods_hash_decode}")
        upload_success.update({file.path: irods_hash_decode})

    # End of central directory record (EOCD) 22
    size += 22
    if zip64:
        # Zip64 end of central directory record 56 & locator 20
        size += 56 + 20
    return data, size


def zip_collection(data, stream, session, upload_success):
    """
    Create a generator to zip the collection.
    Also request the iRODS sha256 chksums and compare it to the buffer.

    :param list data: list of files path
    :param UnseekableStream stream: raw buffer stream
    :param <irods.session.iRODSSession> session: Open iRODS session to the server
    :param dict upload_success: {file_path: hash_key}
    """

    zip_buffer = zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED)
    yield
    for f in data:
        buff = session.data_objects.open(f.path, 'r')

        zip_info = zipfile.ZipInfo(f.path)
        zip_info.file_size = f.size
        irods_sha = hashlib.sha256()
        with zip_buffer.open(zip_info, mode='w', force_zip64=True) as dest:
            for chunk in iter(lambda: buff.read(BLOCK_SIZE), b''):
                dest.write(chunk)
                irods_sha.update(chunk)
                yield
        buff.close()

        sha_hexdigest = irods_sha.hexdigest()
        logger.info(f"{'--':<30}buffer {f.name} SHA: {sha_hexdigest}")
        if upload_success.get(f.path) == sha_hexdigest:
            logger.info(f"{'--':<30}SHA-256 {f.name}  match: True")
            upload_success.update({f.path: True})

    zip_buffer.close()
    yield


def get_zip_generator(collection, session, upload_success, rulemanager, irods_md5) -> IteratorAsBinaryFile:
    """
    Bundle an iRODS collection into an uncompressed zip buffer.
    Return the zip buffer iterator.

    :param <irods.manager.collection_manager.CollectionManager> collection: iRODS collection to zip
    :param <irods.session.iRODSSession> session:  Open iRODS session to the server
    :param dict upload_success: {file_path: hash_key}
    :param <irodsManager.irodsRuleManager.RuleManager> rulemanager: RuleManager to call chksums rule
    :param irods_md5: hashlib.md5()
    :return: zip buffer iterator
    """

    data, size = collection_zip_preparation(collection, rulemanager, upload_success)
    logger.info(f"{'--':<10} bundle predicted size: {size}")
    stream = UnseekableStream()
    zip_iterator = zip_collection(data, stream, session, upload_success)
    bar = tqdm(total=size, unit="bytes", smoothing=0.1, unit_scale=True)
    iterator = IteratorAsBinaryFile(size, archive_generator(zip_iterator, stream, bar), irods_md5)

    return iterator


def calc_tar_block(nb):
    if nb < 512:
        return 512
    remainder = divmod(nb, 512)[1]
    if remainder == 0:
        return nb
    elif remainder > 0:
        return nb + 512


# https://gist.github.com/chipx86/9598b1e4a9a1a7831054
def stream_build_tar(tar_name, collection, data, stream, session, upload_success):
    tar = tarfile.TarFile.open(tar_name, 'w|', stream)
    yield

    for f in data:
        filepath = f.path.replace(collection.path, '')
        tar_info = tarfile.TarInfo(filepath)

        tar_info.size = f.size
        tar.addfile(tar_info)

        buff = session.data_objects.open(f.path, 'r')

        irods_sha = hashlib.sha256()

        while True:
            s = buff.read(BLOCK_SIZE)
            if len(s) > 0:
                tar.fileobj.write(s)
                irods_sha.update(s)
                yield

            if len(s) < BLOCK_SIZE:
                blocks, remainder = divmod(tar_info.size, tarfile.BLOCKSIZE)

                if remainder > 0:
                    tar.fileobj.write(tarfile.NUL * (tarfile.BLOCKSIZE - remainder))
                    yield
                    blocks += 1

                tar.offset += blocks * tarfile.BLOCKSIZE
                break
        buff.close()

        sha_hexdigest = irods_sha.hexdigest()
        # logger.info(f"{'--':<30}buffer {f.name} SHA: {sha_hexdigest}")
        if upload_success.get(f.path) == sha_hexdigest:
            # logger.info(f"{'--':<30}SHA-256 {f.name}  match: True")
            upload_success.update({f.path: True})

    tar.close()
    yield