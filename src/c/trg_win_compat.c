// Windows compatibility layer for trg
#ifdef _WIN32

#include <windows.h>
#define read c_compat_unused_read
#define write c_compat_unused_write
#define close c_compat_unused_close
#include <io.h>
#undef read
#undef write
#undef close
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#ifndef FSCTL_GET_REPARSE_DATA_BUFFER
#define FSCTL_GET_REPARSE_DATA_BUFFER 0x000900a8
#endif

#ifndef IO_REPARSE_TAG_MOUNT_POINT
#define IO_REPARSE_TAG_MOUNT_POINT (0xA0000003L)
#endif

#ifndef IO_REPARSE_TAG_SYMLINK
#define IO_REPARSE_TAG_SYMLINK (0xA000000CL)
#endif

#ifndef MAXIMUM_REPARSE_DATA_BUFFER_SIZE
#define MAXIMUM_REPARSE_DATA_BUFFER_SIZE (16 * 1024)
#endif

#ifndef FILE_NAME_NORMALIZED
#define FILE_NAME_NORMALIZED 0x0
#endif

typedef struct _TRG_REPARSE_DATA_BUFFER {
    ULONG  ReparseTag;
    USHORT ReparseDataLength;
    USHORT Reserved;
    union {
        struct {
            USHORT SubstituteNameOffset;
            USHORT SubstituteNameLength;
            USHORT PrintNameOffset;
            USHORT PrintNameLength;
            ULONG  Flags;
            WCHAR  PathBuffer[1];
        } SymbolicLinkReparseBuffer;
        struct {
            USHORT SubstituteNameOffset;
            USHORT SubstituteNameLength;
            USHORT PrintNameOffset;
            USHORT PrintNameLength;
            WCHAR  PathBuffer[1];
        } MountPointReparseBuffer;
        struct {
            UCHAR  DataBuffer[1];
        } GenericReparseBuffer;
    };
} TRG_REPARSE_DATA_BUFFER;

// Initialize console codepage to UTF-8 and standard streams to binary mode
void trg_win_init_platform(void) {
    SetConsoleOutputCP(65001);
    SetConsoleCP(65001);
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    _setmode(_fileno(stderr), _O_BINARY);
}

static wchar_t* utf8_to_utf16(const char* utf8) {
    if (!utf8) return NULL;
    int len = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, NULL, 0);
    if (len <= 0) return NULL;
    wchar_t* wstr = (wchar_t*)malloc(len * sizeof(wchar_t));
    if (!wstr) return NULL;
    MultiByteToWideChar(CP_UTF8, 0, utf8, -1, wstr, len);
    return wstr;
}

static void utf16_to_utf8(const wchar_t* wstr, char* out_buf, int max_len) {
    if (!wstr || !out_buf || max_len <= 0) return;
    out_buf[0] = '\0';
    WideCharToMultiByte(CP_UTF8, 0, wstr, -1, out_buf, max_len, NULL, NULL);
    out_buf[max_len - 1] = '\0';
}

void trg_win_clear_last_error(void) {
    SetLastError(0);
}

int trg_win_get_last_error(void) {
    return (int)GetLastError();
}

// Resolves path using Win32 GetFullPathNameW.
// Preserves process CWD and handles drive-relative (D:bar), root-relative (\foo), UNC, etc.
int trg_win_resolve_path(const char* rel_path, char* out_buf, size_t out_size) {
    if (!rel_path || !out_buf || out_size == 0) {
        return -1;
    }
    wchar_t* wpath = utf8_to_utf16(rel_path);
    if (!wpath) {
        return -1;
    }
    wchar_t wout[4096];
    DWORD len = GetFullPathNameW(wpath, 4096, wout, NULL);
    free(wpath);
    if (len == 0 || len >= 4096) {
        return -1; // Resolution failed, do NOT guess
    }

    int utf8_len = WideCharToMultiByte(CP_UTF8, 0, wout, -1, out_buf, (int)out_size, NULL, NULL);
    if (utf8_len <= 0) {
        return -1;
    }

    for (char* p = out_buf; *p; p++) {
        if (*p == '\\') *p = '/';
    }

    if (out_buf[0] >= 'a' && out_buf[0] <= 'z' && out_buf[1] == ':') {
        out_buf[0] = (char)(out_buf[0] - ('a' - 'A'));
    }

    return (int)strlen(out_buf);
}

// Classifies an object for reparse / link handling:
//  0: Normal object (regular file or directory)
//  1: Link to skip (symlink, junction/mount point, or uninspectable directory reparse point)
// -1: Detection failure (permission denied, I/O error)
// -2: File / path not found
int trg_win_classify_reparse(const char* path) {
    if (!path) return -1;
    wchar_t* wpath = utf8_to_utf16(path);
    if (!wpath) return -1;

    DWORD attrs = GetFileAttributesW(wpath);
    if (attrs == INVALID_FILE_ATTRIBUTES) {
        DWORD err = GetLastError();
        free(wpath);
        if (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND) {
            return -2; // Not found
        }
        return -1; // Detection failure (permission denied, I/O device error, etc.)
    }

    if (!(attrs & FILE_ATTRIBUTE_REPARSE_POINT)) {
        free(wpath);
        return 0; // Normal object
    }

    // It IS a reparse point!
    HANDLE h = CreateFileW(
        wpath,
        0,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        NULL
    );
    free(wpath);

    if (h == INVALID_HANDLE_VALUE) {
        // Cannot open handle (e.g. locked or permission).
        // If it's a directory, conservatively return 1 (skip link to prevent loops)
        if (attrs & FILE_ATTRIBUTE_DIRECTORY) {
            return 1;
        }
        return -1;
    }

    BYTE buffer[MAXIMUM_REPARSE_DATA_BUFFER_SIZE];
    DWORD bytes_returned = 0;
    BOOL ok = DeviceIoControl(
        h,
        FSCTL_GET_REPARSE_DATA_BUFFER,
        NULL,
        0,
        buffer,
        sizeof(buffer),
        &bytes_returned,
        NULL
    );
    CloseHandle(h);

    if (!ok) {
        if (attrs & FILE_ATTRIBUTE_DIRECTORY) {
            return 1; // Directory reparse point: must skip
        }
        return -1;
    }

    TRG_REPARSE_DATA_BUFFER* rdb = (TRG_REPARSE_DATA_BUFFER*)buffer;
    if (rdb->ReparseTag == IO_REPARSE_TAG_SYMLINK ||
        rdb->ReparseTag == IO_REPARSE_TAG_MOUNT_POINT ||
        rdb->ReparseTag == 0x80000018L /* IO_REPARSE_TAG_APPEXECLINK */) {
        return 1; // Link to skip
    }

    if (attrs & FILE_ATTRIBUTE_DIRECTORY) {
        return 1; // Any directory reparse point: skip
    }

    return 0; // Non-link reparse file
}

// readlink compatibility for Windows: detects symlinks & junctions (mount points)
long long readlink(const char* path, char* buf, size_t bufsiz) {
    if (!path || !buf || bufsiz == 0) {
        errno = EINVAL;
        return -1;
    }

    wchar_t* wpath = utf8_to_utf16(path);
    if (!wpath) {
        errno = EINVAL;
        return -1;
    }

    DWORD attrs = GetFileAttributesW(wpath);
    if (attrs == INVALID_FILE_ATTRIBUTES || !(attrs & FILE_ATTRIBUTE_REPARSE_POINT)) {
        free(wpath);
        errno = EINVAL;
        return -1;
    }

    HANDLE h = CreateFileW(
        wpath,
        0,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        NULL
    );
    free(wpath);
    if (h == INVALID_HANDLE_VALUE) {
        errno = EACCES;
        return -1;
    }

    BYTE buffer[MAXIMUM_REPARSE_DATA_BUFFER_SIZE];
    DWORD bytes_returned = 0;
    BOOL ok = DeviceIoControl(
        h,
        FSCTL_GET_REPARSE_DATA_BUFFER,
        NULL,
        0,
        buffer,
        sizeof(buffer),
        &bytes_returned,
        NULL
    );
    CloseHandle(h);

    if (!ok) {
        errno = EINVAL;
        return -1;
    }

    TRG_REPARSE_DATA_BUFFER* rdb = (TRG_REPARSE_DATA_BUFFER*)buffer;
    const WCHAR* target_wstr = NULL;
    USHORT target_len_bytes = 0;

    if (rdb->ReparseTag == IO_REPARSE_TAG_SYMLINK) {
        if (rdb->SymbolicLinkReparseBuffer.PrintNameLength > 0) {
            target_wstr = (const WCHAR*)((const char*)rdb->SymbolicLinkReparseBuffer.PathBuffer + rdb->SymbolicLinkReparseBuffer.PrintNameOffset);
            target_len_bytes = rdb->SymbolicLinkReparseBuffer.PrintNameLength;
        } else {
            target_wstr = (const WCHAR*)((const char*)rdb->SymbolicLinkReparseBuffer.PathBuffer + rdb->SymbolicLinkReparseBuffer.SubstituteNameOffset);
            target_len_bytes = rdb->SymbolicLinkReparseBuffer.SubstituteNameLength;
        }
    } else if (rdb->ReparseTag == IO_REPARSE_TAG_MOUNT_POINT) {
        if (rdb->MountPointReparseBuffer.PrintNameLength > 0) {
            target_wstr = (const WCHAR*)((const char*)rdb->MountPointReparseBuffer.PathBuffer + rdb->MountPointReparseBuffer.PrintNameOffset);
            target_len_bytes = rdb->MountPointReparseBuffer.PrintNameLength;
        } else {
            target_wstr = (const WCHAR*)((const char*)rdb->MountPointReparseBuffer.PathBuffer + rdb->MountPointReparseBuffer.SubstituteNameOffset);
            target_len_bytes = rdb->MountPointReparseBuffer.SubstituteNameLength;
        }
    } else {
        errno = EINVAL;
        return -1;
    }

    if (!target_wstr || target_len_bytes == 0) {
        errno = EINVAL;
        return -1;
    }

    int wchar_count = target_len_bytes / sizeof(WCHAR);
    WCHAR* null_terminated_wstr = (WCHAR*)malloc((wchar_count + 1) * sizeof(WCHAR));
    if (!null_terminated_wstr) {
        errno = ENOMEM;
        return -1;
    }
    memcpy(null_terminated_wstr, target_wstr, target_len_bytes);
    null_terminated_wstr[wchar_count] = L'\0';

    const WCHAR* clean_wstr = null_terminated_wstr;
    if (wcsncmp(clean_wstr, L"\\??\\", 4) == 0 || wcsncmp(clean_wstr, L"\\\\?\\", 4) == 0) {
        clean_wstr += 4;
    }

    char temp_utf8[4096];
    int utf8_bytes = WideCharToMultiByte(CP_UTF8, 0, clean_wstr, -1, temp_utf8, 4096, NULL, NULL);
    free(null_terminated_wstr);

    if (utf8_bytes <= 0) {
        errno = EINVAL;
        return -1;
    }

    size_t actual_len = (size_t)(utf8_bytes - 1);
    size_t copy_len = actual_len;
    if (copy_len > bufsiz) {
        copy_len = bufsiz;
    }
    memcpy(buf, temp_utf8, copy_len);
    return (long long)copy_len;
}

// realpath compatibility for Windows: resolves symlinks, junctions, and relative segments
char* realpath(const char* path, char* resolved_path) {
    if (!path) {
        errno = EINVAL;
        return NULL;
    }
    wchar_t* wpath = utf8_to_utf16(path);
    if (!wpath) {
        errno = ENOMEM;
        return NULL;
    }

    // Open handle following symlinks and junctions (NO FILE_FLAG_OPEN_REPARSE_POINT)
    HANDLE h = CreateFileW(
        wpath,
        0,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        NULL
    );

    wchar_t final_wpath[4096];
    DWORD len = 0;
    if (h != INVALID_HANDLE_VALUE) {
        len = GetFinalPathNameByHandleW(h, final_wpath, 4096, FILE_NAME_NORMALIZED);
        CloseHandle(h);
    }

    if (len == 0 || len >= 4096) {
        // Fallback to GetFullPathNameW if file cannot be opened (e.g. permission or not found)
        len = GetFullPathNameW(wpath, 4096, final_wpath, NULL);
        if (len == 0 || len >= 4096) {
            free(wpath);
            errno = ENOENT;
            return NULL;
        }
    }
    free(wpath);

    const wchar_t* p = final_wpath;
    char unc_prefix[3] = "";
    if (wcsncmp(p, L"\\\\?\\UNC\\", 8) == 0) {
        p += 8;
        unc_prefix[0] = '/';
        unc_prefix[1] = '/';
        unc_prefix[2] = '\0';
    } else if (wcsncmp(p, L"\\\\?\\", 4) == 0) {
        p += 4;
    } else if (wcsncmp(p, L"\\??\\", 4) == 0) {
        p += 4;
    }

    if (!resolved_path) {
        resolved_path = (char*)malloc(4096);
        if (!resolved_path) {
            errno = ENOMEM;
            return NULL;
        }
    }

    char temp_buf[4096];
    utf16_to_utf8(p, temp_buf, 4096);

    if (unc_prefix[0] != '\0') {
        snprintf(resolved_path, 4096, "%s%s", unc_prefix, temp_buf);
    } else {
        strncpy(resolved_path, temp_buf, 4096);
        resolved_path[4095] = '\0';
    }

    for (char* s = resolved_path; *s; s++) {
        if (*s == '\\') *s = '/';
    }
    return resolved_path;
}

// Strong implementation of read/write bridging 32-bit CRT _read/_write to POSIX isize
long long read(int fd, void *buf, size_t count) {
    if (count > 0x7FFFFFFF) count = 0x7FFFFFFF;
    int res = _read(fd, buf, (unsigned int)count);
    return (long long)res;
}

long long write(int fd, const void *buf, size_t count) {
    if (count > 0x7FFFFFFF) count = 0x7FFFFFFF;
    SetLastError(0);
    int res = _write(fd, buf, (unsigned int)count);
    if (res < 0) {
        DWORD win_err = GetLastError();
        if (win_err == 109 || win_err == 232) { // ERROR_BROKEN_PIPE or ERROR_NO_DATA
            intptr_t osfh = _get_osfhandle(fd);
            if (osfh != -1 && GetFileType((HANDLE)osfh) == FILE_TYPE_PIPE) {
                errno = 32; // EPIPE
            }
        }
    }
    return (long long)res;
}

int close(int fd) {
    return _close(fd);
}

int trg_win_is_readable_stdin(void) {
    HANDLE h = GetStdHandle(STD_INPUT_HANDLE);
    if (h == NULL || h == INVALID_HANDLE_VALUE) {
        return 0;
    }
    DWORD file_type = GetFileType(h);
    DWORD base_type = file_type & ~FILE_TYPE_REMOTE;
    if (base_type == FILE_TYPE_PIPE || base_type == FILE_TYPE_DISK) {
        return 1;
    }
    return 0;
}

#else // !_WIN32

void trg_win_init_platform(void) {}
void trg_win_clear_last_error(void) {}
int trg_win_get_last_error(void) { return 0; }
int trg_win_resolve_path(const char* rel_path, char* out_buf, size_t out_size) {
    (void)rel_path; (void)out_buf; (void)out_size;
    return -1;
}
int trg_win_classify_reparse(const char* path) {
    (void)path;
    return 0;
}
int trg_win_is_readable_stdin(void) {
    return 0;
}

#endif // _WIN32

