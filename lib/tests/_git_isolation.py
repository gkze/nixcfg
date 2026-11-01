"""Git environment that must never reach the test suite's own Git calls."""

# ``git rev-parse --local-env-vars``: Git exports some of these to hooks (a
# linked worktree's pre-push gets ``GIT_DIR``), and any test Git command that
# inherits them acts on the developer's repository instead of its fixture.
GIT_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)
