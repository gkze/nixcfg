#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef NODE_EXECUTABLE
#error "NODE_EXECUTABLE must name the immutable Node executable"
#endif

static int own_executable(char resolved[PATH_MAX]) {
  uint32_t size = PATH_MAX;
  char executable[PATH_MAX];
  if (_NSGetExecutablePath(executable, &size) != 0) {
    fprintf(stderr, "HQ Recall launcher: executable path is too long\n");
    return -1;
  }
  if (realpath(executable, resolved) == NULL) {
    const int saved_errno = errno;
    fprintf(stderr, "HQ Recall launcher: cannot resolve executable path: %s\n",
            strerror(saved_errno));
    return -1;
  }
  return 0;
}

static int packaged_bridge(char bridge[PATH_MAX]) {
  char executable[PATH_MAX];
  if (own_executable(executable) != 0) {
    return -1;
  }

  char *const macos = strrchr(executable, '/');
  if (macos == NULL) {
    fprintf(stderr, "HQ Recall launcher: executable has no parent directory\n");
    return -1;
  }
  *macos = '\0';
  char *const contents = strrchr(executable, '/');
  if (contents == NULL || strcmp(contents + 1, "MacOS") != 0) {
    fprintf(stderr, "HQ Recall launcher: expected an app Contents/MacOS path\n");
    return -1;
  }
  *contents = '\0';

  const int written = snprintf(
      bridge, PATH_MAX, "%s/Resources/recall-sdk-bridge/bridge.mjs", executable);
  if (written < 0 || written >= PATH_MAX) {
    fprintf(stderr, "HQ Recall launcher: packaged bridge path is too long\n");
    return -1;
  }
  if (access(bridge, R_OK) != 0) {
    const int saved_errno = errno;
    fprintf(stderr, "HQ Recall launcher: packaged Recall bridge is unavailable: %s\n",
            strerror(saved_errno));
    return -1;
  }
  return 0;
}

int main(int argc, char *argv[]) {
  char bridge[PATH_MAX];
  if (packaged_bridge(bridge) != 0) {
    return EXIT_FAILURE;
  }

  char **const node_argv = calloc((size_t)argc + 2U, sizeof(*node_argv));
  if (node_argv == NULL) {
    fprintf(stderr, "HQ Recall launcher: cannot allocate argument vector\n");
    return EXIT_FAILURE;
  }
  node_argv[0] = (char *)NODE_EXECUTABLE;
  node_argv[1] = bridge;
  for (int index = 1; index < argc; ++index) {
    node_argv[index + 1] = argv[index];
  }
  node_argv[argc + 1] = NULL;

  execv(NODE_EXECUTABLE, node_argv);
  const int saved_errno = errno;
  fprintf(stderr, "HQ Recall launcher: cannot execute packaged Node: %s\n",
          strerror(saved_errno));
  free(node_argv);
  return EXIT_FAILURE;
}
