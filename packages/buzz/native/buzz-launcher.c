#define _DARWIN_C_SOURCE

#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pwd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sysexits.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define RUNTIME_BUNDLE_ENV "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR"
#define RUNTIME_CACHE_ENV "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR"
#define RUNTIME_MANIFEST_URL_ENV "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL"
#define CACHE_SUFFIX                                                           \
  "Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes"

static void fail_message(const char *message, const char *path) {
  if (path == NULL) {
    (void)fprintf(stderr, "Buzz launcher: %s\n", message);
  } else {
    (void)fprintf(stderr, "Buzz launcher: %s: %s\n", message, path);
  }
  (void)fflush(stderr);
  _exit(EX_CONFIG);
}

static void fail_errno(const char *operation, const char *path) {
  const int saved_errno = errno;
  if (path == NULL) {
    (void)fprintf(stderr, "Buzz launcher: %s: %s\n", operation,
                  strerror(saved_errno));
  } else {
    (void)fprintf(stderr, "Buzz launcher: %s %s: %s\n", operation, path,
                  strerror(saved_errno));
  }
  (void)fflush(stderr);
  _exit(EX_OSERR);
}

static void *allocate(size_t size) {
  void *allocation = malloc(size);
  if (allocation == NULL) {
    fail_errno("allocate launcher memory", NULL);
  }
  return allocation;
}

static char *canonical_path(const char *path, const char *label) {
  char *resolved = realpath(path, NULL);
  if (resolved == NULL) {
    fail_errno(label, path);
  }
  return resolved;
}

static char *launcher_path(void) {
  char stack_buffer[PATH_MAX];
  uint32_t size = (uint32_t)sizeof(stack_buffer);
  if (_NSGetExecutablePath(stack_buffer, &size) == 0) {
    return canonical_path(stack_buffer, "resolve launcher path");
  }
  if (size == 0) {
    fail_message("determine launcher path length", NULL);
  }

  char *dynamic_buffer = allocate((size_t)size);
  if (_NSGetExecutablePath(dynamic_buffer, &size) != 0) {
    fail_message("read launcher path", NULL);
  }
  char *resolved = canonical_path(dynamic_buffer, "resolve launcher path");
  free(dynamic_buffer);
  return resolved;
}

static char *parent_directory(const char *path) {
  const size_t length = strlen(path);
  char *parent = allocate(length + 1);
  (void)memcpy(parent, path, length + 1);
  char *separator = strrchr(parent, '/');
  if (separator == NULL || separator == parent) {
    fail_message("launcher path has no bundle parent", path);
  }
  *separator = '\0';
  return parent;
}

static const char *last_component(const char *path) {
  const char *separator = strrchr(path, '/');
  return separator == NULL ? path : separator + 1;
}

static char *join_path(const char *directory, const char *relative) {
  const size_t directory_length = strlen(directory);
  const size_t relative_length = strlen(relative);
  if (directory_length > SIZE_MAX - relative_length - 2) {
    fail_message("derived path is too long", directory);
  }
  char *joined = allocate(directory_length + relative_length + 2);
  (void)memcpy(joined, directory, directory_length);
  joined[directory_length] = '/';
  (void)memcpy(joined + directory_length + 1, relative, relative_length + 1);
  return joined;
}

static void require_directory(const char *path, const char *label) {
  struct stat metadata;
  if (lstat(path, &metadata) != 0) {
    fail_errno(label, path);
  }
  if (!S_ISDIR(metadata.st_mode)) {
    fail_message(label, path);
  }
}

static void require_regular_file(const char *path, int executable,
                                 const char *label) {
  struct stat metadata;
  if (lstat(path, &metadata) != 0) {
    fail_errno(label, path);
  }
  if (!S_ISREG(metadata.st_mode)) {
    fail_message(label, path);
  }
  const int access_mode = executable != 0 ? X_OK : R_OK;
  if (access(path, access_mode) != 0) {
    fail_errno(label, path);
  }
}

static char *passwd_home_directory(void) {
  long configured_size = sysconf(_SC_GETPW_R_SIZE_MAX);
  size_t buffer_size =
      configured_size > 0 ? (size_t)configured_size : (size_t)16384;
  struct passwd entry;
  struct passwd *result = NULL;

  for (;;) {
    char *buffer = allocate(buffer_size);
    const int error = getpwuid_r(getuid(), &entry, buffer, buffer_size, &result);
    if (error == 0 && result != NULL) {
      if (entry.pw_dir == NULL || entry.pw_dir[0] != '/') {
        free(buffer);
        fail_message("account home directory is not absolute", NULL);
      }
      const size_t home_length = strlen(entry.pw_dir);
      char *home = allocate(home_length + 1);
      (void)memcpy(home, entry.pw_dir, home_length + 1);
      char *resolved = realpath(home, NULL);
      const int saved_errno = errno;
      free(buffer);
      if (resolved == NULL) {
        errno = saved_errno;
        fail_errno("resolve account home directory", home);
      }
      free(home);
      return resolved;
    }
    free(buffer);
    if (error == 0) {
      fail_message("account home directory is unavailable", NULL);
    }
    if (error != ERANGE) {
      errno = error;
      fail_errno("read account home directory", NULL);
    }
    if (buffer_size > SIZE_MAX / 2) {
      fail_message("account record is too large", NULL);
    }
    buffer_size *= 2;
  }
}

static char *absolute_home_directory(void) {
  const char *environment_home = getenv("HOME");
  if (environment_home != NULL && environment_home[0] == '/') {
    char *resolved = realpath(environment_home, NULL);
    if (resolved != NULL) {
      return resolved;
    }
  }
  return passwd_home_directory();
}

int main(int argc, char *argv[]) {
  (void)argc;
  char *self = launcher_path();
  char *macos_directory = parent_directory(self);
  if (strcmp(last_component(macos_directory), "MacOS") != 0) {
    fail_message("launcher is not inside Contents/MacOS", self);
  }
  char *contents_directory = parent_directory(macos_directory);
  if (strcmp(last_component(contents_directory), "Contents") != 0) {
    fail_message("launcher is not inside an app Contents directory", self);
  }

  char *payload = join_path(macos_directory, "buzz-desktop.real");
  char *runtime = join_path(contents_directory, "Resources/mesh-runtime");
  char *manifest = join_path(runtime, "manifest.json");
  require_regular_file(payload, 1, "payload executable is unavailable");
  require_directory(runtime, "runtime bundle directory is unavailable");
  require_regular_file(manifest, 0, "runtime manifest is unavailable");

  char *home = absolute_home_directory();
  char *cache = join_path(home, CACHE_SUFFIX);
  if (setenv(RUNTIME_BUNDLE_ENV, runtime, 1) != 0) {
    fail_errno("set runtime bundle environment", RUNTIME_BUNDLE_ENV);
  }
  if (setenv(RUNTIME_CACHE_ENV, cache, 1) != 0) {
    fail_errno("set runtime cache environment", RUNTIME_CACHE_ENV);
  }
  if (unsetenv(RUNTIME_MANIFEST_URL_ENV) != 0) {
    fail_errno("unset runtime manifest URL environment",
               RUNTIME_MANIFEST_URL_ENV);
  }

  execv(payload, argv);
  fail_errno("execute Buzz payload", payload);
}
