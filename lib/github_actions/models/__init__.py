"""Generated Pydantic models for GitHub Actions workflow schemas."""

from ._generated import (
    GitHubWorkflow,
    JobNeeds,
    Matrix,
    NormalJob,
    ReusableWorkflowCallJob,
    Step,
    Strategy,
)

# Keep ``Workflow`` as a compatibility alias for earlier callers, but prefer the
# explicit ``GitHubWorkflow`` name in new code to avoid confusion with the
# GitHub Actions REST ``Workflow`` model exposed by GitHubKit.
Workflow = GitHubWorkflow

__all__ = [
    "GitHubWorkflow",
    "JobNeeds",
    "Matrix",
    "NormalJob",
    "ReusableWorkflowCallJob",
    "Step",
    "Strategy",
    "Workflow",
]
