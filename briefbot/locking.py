"""An OS lock prevents two processes from consuming the same local queue."""
import os


def acquire_lock(path):
    handle = open(path, 'a+b')
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('SiteBrief уже запущен с этой папкой данных. Остановите первый процесс.') from None
    return handle
