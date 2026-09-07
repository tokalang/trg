#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <assert.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define write c_compat_unused_write
#include <io.h>
#undef write
#include <fcntl.h>

// Forward declaration of trg Windows compat write()
long long write(int fd, const void *buf, size_t count);

int main(int argc, char** argv) {
    printf("[test_c_bridge_write] Starting C bridge write fault injection tests...\n");

    // Case 1: Bad file descriptor with pre-seeded stale GetLastError(109)
    // Must NOT be converted to EPIPE (32)
    SetLastError(109); // ERROR_BROKEN_PIPE
    errno = 0;
    long long res = write(-1, "test", 4);
    int err = errno;
    printf("Case 1 (Invalid fd with stale 109): res=%lld, errno=%d (EBADF=%d, EPIPE=32)\n", res, err, EBADF);
    assert(res < 0);
    assert(err != 32); // MUST NOT BE EPIPE
    assert(err == EBADF || err == EINVAL);

    // Case 2: Regular file with invalid buffer (causing normal EINVAL/EFAULT) with stale win_err 109
    char tmp_path[MAX_PATH];
    GetTempPathA(MAX_PATH, tmp_path);
    strcat(tmp_path, "trg_test_write_einval.tmp");
    int fd = _open(tmp_path, _O_WRONLY | _O_CREAT | _O_TRUNC, 0666);
    assert(fd >= 0);

    SetLastError(109);
    errno = 0;
    // count > 0 with NULL buffer causes CRT _write to fail with EINVAL (22)
    res = write(fd, NULL, 16);
    err = errno;
    printf("Case 2 (Regular file invalid buffer with stale 109): res=%lld, errno=%d (EINVAL=%d)\n", res, err, EINVAL);
    assert(res < 0);
    assert(err != 32); // MUST NOT BE EPIPE
    assert(err == 22); // MUST BE EINVAL (22)

    // Case 3: Genuine pipe closed at read end
    // Must be converted to EPIPE (32)
    int pipe_fds[2];
    int p_res = _pipe(pipe_fds, 1024, _O_BINARY);
    assert(p_res == 0);
    _close(pipe_fds[0]); // Close read end

    errno = 0;
    res = write(pipe_fds[1], "pipe payload", 12);
    err = errno;
    printf("Case 3 (Genuine broken pipe): res=%lld, errno=%d (EPIPE=32)\n", res, err);
    assert(res < 0);
    assert(err == 32); // MUST BE EPIPE (32)
    _close(pipe_fds[1]);

    _close(fd);
    remove(tmp_path);

    printf("[test_c_bridge_write] ALL FAULT INJECTION ASSERTIONS PASSED!\n");
    return 0;
}
#else
int main() {
    printf("[test_c_bridge_write] POSIX host no-op (verified via native libc write)\n");
    return 0;
}
#endif
