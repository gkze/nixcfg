"""Auto-generated Pydantic models from JSON schemas.

DO NOT EDIT MANUALLY. Regenerate with:
    nixcfg schema generate github-actions
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, constr

# === github-workflow ===


class Type(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    DELETED = "deleted"


class BranchProtectionRule(BaseModel):
    """Runs your workflow anytime the branch_protection_rule event occurs. More than one activity type triggers this event."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type] | None = ["created", "edited", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type2(StrEnum):
    CREATED = "created"
    REREQUESTED = "rerequested"
    COMPLETED = "completed"
    REQUESTED_ACTION = "requested_action"


class CheckRun(BaseModel):
    """Runs your workflow anytime the check_run event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/checks/runs."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type2] | None = [
        "created",
        "rerequested",
        "completed",
        "requested_action",
    ]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type4(StrEnum):
    COMPLETED = "completed"
    REQUESTED = "requested"
    REREQUESTED = "rerequested"


class CheckSuite(BaseModel):
    """Runs your workflow anytime the check_suite event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/checks/suites/."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type4] | None = ["completed", "requested", "rerequested"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type6(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    DELETED = "deleted"
    TRANSFERRED = "transferred"
    PINNED = "pinned"
    UNPINNED = "unpinned"
    LABELED = "labeled"
    UNLABELED = "unlabeled"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    CATEGORY_CHANGED = "category_changed"
    ANSWERED = "answered"
    UNANSWERED = "unanswered"


class Discussion(BaseModel):
    """Runs your workflow anytime the discussion event occurs. More than one activity type triggers this event. For information about the GraphQL API, see https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type6] | None = [
        "created",
        "edited",
        "deleted",
        "transferred",
        "pinned",
        "unpinned",
        "labeled",
        "unlabeled",
        "locked",
        "unlocked",
        "category_changed",
        "answered",
        "unanswered",
    ]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class DiscussionComment(BaseModel):
    """Runs your workflow anytime the discussion_comment event occurs. More than one activity type triggers this event. For information about the GraphQL API, see https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type] | None = ["created", "edited", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class IssueComment(BaseModel):
    """Runs your workflow anytime the issue_comment event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues/comments/."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type] | None = ["created", "edited", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type12(StrEnum):
    OPENED = "opened"
    EDITED = "edited"
    DELETED = "deleted"
    TRANSFERRED = "transferred"
    PINNED = "pinned"
    UNPINNED = "unpinned"
    CLOSED = "closed"
    REOPENED = "reopened"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    LABELED = "labeled"
    UNLABELED = "unlabeled"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    MILESTONED = "milestoned"
    DEMILESTONED = "demilestoned"


class Issues(BaseModel):
    """Runs your workflow anytime the issues event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type12] | None = [
        "opened",
        "edited",
        "deleted",
        "transferred",
        "pinned",
        "unpinned",
        "closed",
        "reopened",
        "assigned",
        "unassigned",
        "labeled",
        "unlabeled",
        "locked",
        "unlocked",
        "milestoned",
        "demilestoned",
    ]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Label(BaseModel):
    """Runs your workflow anytime the label event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues/labels/."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type] | None = ["created", "edited", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type16(StrEnum):
    CHECKS_REQUESTED = "checks_requested"


class MergeGroup(BaseModel):
    """Runs your workflow when a pull request is added to a merge queue, which adds the pull request to a merge group. For information about the merge queue, see https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/merging-a-pull-request-with-a-merge-queue ."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type16] | None = ["checks_requested"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type18(StrEnum):
    CREATED = "created"
    CLOSED = "closed"
    OPENED = "opened"
    EDITED = "edited"
    DELETED = "deleted"


class Milestone(BaseModel):
    """Runs your workflow anytime the milestone event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues/milestones/."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type18] | None = ["created", "closed", "opened", "edited", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type20(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    CLOSED = "closed"
    REOPENED = "reopened"
    EDITED = "edited"
    DELETED = "deleted"


class Project(BaseModel):
    """Runs your workflow anytime the project event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/projects/."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type20] | None = [
        "created",
        "updated",
        "closed",
        "reopened",
        "edited",
        "deleted",
    ]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type22(StrEnum):
    CREATED = "created"
    MOVED = "moved"
    CONVERTED = "converted"
    EDITED = "edited"
    DELETED = "deleted"


class ProjectCard(BaseModel):
    """Runs your workflow anytime the project_card event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/projects/cards."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type22] | None = ["created", "moved", "converted", "edited", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type24(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    MOVED = "moved"
    DELETED = "deleted"


class ProjectColumn(BaseModel):
    """Runs your workflow anytime the project_column event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/projects/columns."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type24] | None = ["created", "updated", "moved", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type26(StrEnum):
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    LABELED = "labeled"
    UNLABELED = "unlabeled"
    OPENED = "opened"
    EDITED = "edited"
    CLOSED = "closed"
    REOPENED = "reopened"
    SYNCHRONIZE = "synchronize"
    CONVERTED_TO_DRAFT = "converted_to_draft"
    READY_FOR_REVIEW = "ready_for_review"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    MILESTONED = "milestoned"
    DEMILESTONED = "demilestoned"
    REVIEW_REQUESTED = "review_requested"
    REVIEW_REQUEST_REMOVED = "review_request_removed"
    AUTO_MERGE_ENABLED = "auto_merge_enabled"
    AUTO_MERGE_DISABLED = "auto_merge_disabled"
    ENQUEUED = "enqueued"
    DEQUEUED = "dequeued"


class Type27(StrEnum):
    SUBMITTED = "submitted"
    EDITED = "edited"
    DISMISSED = "dismissed"


class PullRequestReview(BaseModel):
    """Runs your workflow anytime the pull_request_review event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/pulls/reviews.
    Note: Workflows do not run on private base repositories when you open a pull request from a forked repository.
    When you create a pull request from a forked repository to the base repository, GitHub sends the pull_request event to the base repository and no pull request events occur on the forked repository.
    Workflows don't run on forked repositories by default. You must enable GitHub Actions in the Actions tab of the forked repository.
    The permissions for the GITHUB_TOKEN in forked repositories is read-only. For more information about the GITHUB_TOKEN, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type27] | None = ["submitted", "edited", "dismissed"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class PullRequestReviewComment(BaseModel):
    """Runs your workflow anytime a comment on a pull request's unified diff is modified, which triggers the pull_request_review_comment event. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/pulls/comments.
    Note: Workflows do not run on private base repositories when you open a pull request from a forked repository.
    When you create a pull request from a forked repository to the base repository, GitHub sends the pull_request event to the base repository and no pull request events occur on the forked repository.
    Workflows don't run on forked repositories by default. You must enable GitHub Actions in the Actions tab of the forked repository.
    The permissions for the GITHUB_TOKEN in forked repositories is read-only. For more information about the GITHUB_TOKEN, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type] | None = ["created", "edited", "deleted"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type31(StrEnum):
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    LABELED = "labeled"
    UNLABELED = "unlabeled"
    OPENED = "opened"
    EDITED = "edited"
    CLOSED = "closed"
    REOPENED = "reopened"
    SYNCHRONIZE = "synchronize"
    CONVERTED_TO_DRAFT = "converted_to_draft"
    READY_FOR_REVIEW = "ready_for_review"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    REVIEW_REQUESTED = "review_requested"
    REVIEW_REQUEST_REMOVED = "review_request_removed"
    AUTO_MERGE_ENABLED = "auto_merge_enabled"
    AUTO_MERGE_DISABLED = "auto_merge_disabled"


class Type32(StrEnum):
    PUBLISHED = "published"
    UPDATED = "updated"


class RegistryPackage(BaseModel):
    """Runs your workflow anytime a package is published or updated. For more information, see https://help.github.com/en/github/managing-packages-with-github-packages."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type32] | None = ["published", "updated"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type34(StrEnum):
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    CREATED = "created"
    EDITED = "edited"
    DELETED = "deleted"
    PRERELEASED = "prereleased"
    RELEASED = "released"


class Release(BaseModel):
    """Runs your workflow anytime the release event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/repos/releases/ in the GitHub Developer documentation."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type34] | None = [
        "published",
        "unpublished",
        "created",
        "edited",
        "deleted",
        "prereleased",
        "released",
    ]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """


class Type36(StrEnum):
    """Required if input is defined for the on.workflow_call keyword. The value of this parameter is a string specifying the data type of the input. This must be one of: boolean, number, or string."""

    BOOLEAN = "boolean"
    NUMBER = "number"
    STRING = "string"


class Inputs(BaseModel):
    """A string identifier to associate with the input. The value of <input_id> is a map of the input's metadata. The <input_id> must be a unique identifier within the inputs object. The <input_id> must start with a letter or _ and contain only alphanumeric characters, -, or _."""

    model_config = ConfigDict(
        extra="forbid",
    )
    description: str | None = None
    """
    A string description of the input parameter.
    """
    required: bool | None = None
    """
    A boolean to indicate whether the action requires the input parameter. Set to true when the parameter is required.
    """
    type: Type36
    """
    Required if input is defined for the on.workflow_call keyword. The value of this parameter is a string specifying the data type of the input. This must be one of: boolean, number, or string.
    """
    default: bool | float | str | None = None
    """
    The default value is used when an input parameter isn't specified in a workflow file.
    """


class Outputs(BaseModel):
    """A string identifier to associate with the output. The value of <output_id> is a map of the output's metadata. The <output_id> must be a unique identifier within the outputs object. The <output_id> must start with a letter or _ and contain only alphanumeric characters, -, or _."""

    model_config = ConfigDict(
        extra="forbid",
    )
    description: str | None = None
    """
    A string description of the output parameter.
    """
    value: str
    """
    The value that the output parameter will be mapped to. You can set this to a string or an expression with context. For example, you can use the steps context to set the value of an output to the output value of a step.
    """


class Secrets(BaseModel):
    """A string identifier to associate with the secret."""

    model_config = ConfigDict(
        extra="forbid",
    )
    description: str | None = None
    """
    A string description of the secret parameter.
    """
    required: bool | None = None
    """
    A boolean specifying whether the secret must be supplied.
    """


class WorkflowCall(BaseModel):
    """Allows workflows to be reused by other workflows."""

    inputs: dict[constr(pattern=r"^[_a-zA-Z][a-zA-Z0-9_-]*$"), Inputs] | None = None
    """
    When using the workflow_call keyword, you can optionally specify inputs that are passed to the called workflow from the caller workflow.
    """
    outputs: dict[constr(pattern=r"^[_a-zA-Z][a-zA-Z0-9_-]*$"), Outputs] | None = None
    """
    When using the workflow_call keyword, you can optionally specify inputs that are passed to the called workflow from the caller workflow.
    """
    secrets: dict[constr(pattern=r"^[_a-zA-Z][a-zA-Z0-9_-]*$"), Secrets] | None = None
    """
    A map of the secrets that can be used in the called workflow. Within the called workflow, you can use the secrets context to refer to a secret.
    """


class Type37(StrEnum):
    REQUESTED = "requested"
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"


class WorkflowRun(BaseModel):
    """This event occurs when a workflow run is requested or completed, and allows you to execute a workflow based on the finished result of another workflow. For example, if your pull_request workflow generates build artifacts, you can create a new workflow that uses workflow_run to analyze the results and add a comment to the original pull request."""

    model_config = ConfigDict(
        extra="allow",
    )
    types: list[Type37] | None = ["requested", "completed"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """
    workflows: Annotated[list[str] | None, Field(min_length=1)] = None


class ScheduleItem(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    cron: str
    """
    A cron expression that represents a schedule. A scheduled workflow will run at most once every 5 minutes.
    """
    timezone: str | None = None
    """
    A string that represents the time zone a scheduled workflow will run relative to in IANA format (e.g. 'America/New_York' or 'Europe/London'). If omitted, the workflow will run relative to midnight UTC.
    """


class Architecture(StrEnum):
    ARM32 = "ARM32"
    X64 = "x64"
    X86 = "x86"


type Configuration = str | float | bool | dict[str, Configuration] | list[Configuration]


class Credentials(BaseModel):
    """If the image's container registry requires authentication to pull the image, you can use credentials to set a map of the username and password. The credentials are the same values that you would provide to the `docker login` command."""

    username: str | None = None
    password: str | None = None


class Permissions1(StrEnum):
    """You can modify the default permissions granted to the GITHUB_TOKEN, adding or removing access as required, so that you only allow the minimum required access."""

    READ_ALL = "read-all"
    WRITE_ALL = "write-all"


class Models(StrEnum):
    READ = "read"
    NONE = "none"


class PermissionsLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    NONE = "none"


class Event(StrEnum):
    BRANCH_PROTECTION_RULE = "branch_protection_rule"
    CHECK_RUN = "check_run"
    CHECK_SUITE = "check_suite"
    CREATE = "create"
    DELETE = "delete"
    DEPLOYMENT = "deployment"
    DEPLOYMENT_STATUS = "deployment_status"
    DISCUSSION = "discussion"
    DISCUSSION_COMMENT = "discussion_comment"
    FORK = "fork"
    GOLLUM = "gollum"
    ISSUE_COMMENT = "issue_comment"
    ISSUES = "issues"
    LABEL = "label"
    MERGE_GROUP = "merge_group"
    MILESTONE = "milestone"
    PAGE_BUILD = "page_build"
    PROJECT = "project"
    PROJECT_CARD = "project_card"
    PROJECT_COLUMN = "project_column"
    PUBLIC = "public"
    PULL_REQUEST = "pull_request"
    PULL_REQUEST_REVIEW = "pull_request_review"
    PULL_REQUEST_REVIEW_COMMENT = "pull_request_review_comment"
    PULL_REQUEST_TARGET = "pull_request_target"
    PUSH = "push"
    REGISTRY_PACKAGE = "registry_package"
    RELEASE = "release"
    STATUS = "status"
    WATCH = "watch"
    WORKFLOW_CALL = "workflow_call"
    WORKFLOW_DISPATCH = "workflow_dispatch"
    WORKFLOW_RUN = "workflow_run"
    REPOSITORY_DISPATCH = "repository_dispatch"


type EventObject = dict[str, Any] | None


type ExpressionSyntax = Annotated[str, Field(pattern="^\\$\\{\\{(.|[\r\n])*\\}\\}$")]


type StringContainingExpressionSyntax = Annotated[
    str, Field(pattern="^.*\\$\\{\\{(.|[\r\n])*\\}\\}.*$")
]


type Glob = Annotated[str, Field(min_length=1)]


type Globs = Annotated[list[Glob], Field(min_length=1)]


class Machine(StrEnum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"


type Name = Annotated[str, Field(pattern="^[_a-zA-Z][a-zA-Z0-9_-]*$")]


type Path = Globs
"""
When using the push and pull_request events, you can configure a workflow to run when at least one file does not match paths-ignore or at least one modified file matches the configured paths. Path filters are not evaluated for pushes to tags.
The paths-ignore and paths keywords accept glob patterns that use the * and ** wildcard characters to match more than one path name. For more information, see https://help.github.com/en/github/automating-your-workflow-with-github-actions/workflow-syntax-for-github-actions#filter-pattern-cheat-sheet.
You can exclude paths using two types of filters. You cannot use both of these filters for the same event in a workflow.
- paths-ignore - Use the paths-ignore filter when you only need to exclude path names.
- paths - Use the paths filter when you need to filter paths for positive matches and exclude paths.
"""


class Shell1(StrEnum):
    """You can override the default shell settings in the runner's operating system using the shell keyword. You can use built-in shell keywords, or you can define a custom set of shell options."""

    BASH = "bash"
    PWSH = "pwsh"
    PYTHON = "python"
    SH = "sh"
    CMD = "cmd"
    POWERSHELL = "powershell"


type Shell = str | Shell1
"""
You can override the default shell settings in the runner's operating system using the shell keyword. You can use built-in shell keywords, or you can define a custom set of shell options.
"""


class Snapshot1(BaseModel):
    """You can use the mapping syntax with `snapshot` to define both the `image-name` and the optional `version`. When you specify a major version, the minor versioning automatically increments if that major version already exists. Patch versions are not supported."""

    model_config = ConfigDict(
        extra="forbid",
    )
    image_name: Annotated[str, Field(alias="image-name")]
    version: Annotated[str | None, Field(pattern="^\\d+(\\.\\d+|\\*)?$")] = None


type Snapshot = str | Snapshot1
"""
You can use `jobs.<job_id>.snapshot` to generate a custom image.
Add the snapshot keyword to the job, using either the string syntax or mapping syntax as shown in https://docs.github.com/en/actions/how-tos/manage-runners/larger-runners/use-custom-images#generating-a-custom-image.
Each job that includes the snapshot keyword creates a separate image. To generate only one image or image version, include all workflow steps in a single job. Each successful run of a job that includes the snapshot keyword creates a new version of that image.
For more information, see https://docs.github.com/en/actions/how-tos/manage-runners/larger-runners/use-custom-images.
"""


class With(BaseModel):
    """A map of the input parameters defined by the action. Each input parameter is a key/value pair. Input parameters are set as environment variables. The variable is prefixed with INPUT_ and converted to upper case."""

    args: str | None = None
    entrypoint: str | None = None


type Types1 = Annotated[list[Any], Field(min_length=1)]
"""
Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
"""


type Types = Types1 | str
"""
Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
"""


type WorkingDirectory = str
"""
Using the working-directory keyword, you can specify the working directory of where to run the command.
"""


type JobNeeds1 = Annotated[list[Name], Field(min_length=1)]
"""
Identifies any jobs that must complete successfully before this job will run. It can be a string or array of strings. If a job fails, all jobs that need it are skipped unless the jobs use a conditional statement that causes the job to continue.
"""


type JobNeeds = JobNeeds1 | Name
"""
Identifies any jobs that must complete successfully before this job will run. It can be a string or array of strings. If a job fails, all jobs that need it are skipped unless the jobs use a conditional statement that causes the job to continue.
"""


type Matrix1 = Annotated[list[dict[str, Configuration]], Field(min_length=1)]


type Matrix = (
    dict[constr(pattern=r"^(in|ex)clude$"), ExpressionSyntax | Matrix1]
    | ExpressionSyntax
)
"""
A build matrix is a set of different configurations of the virtual environment. For example you might run a job against more than one supported version of a language, operating system, or tool. Each configuration is a copy of the job that runs and reports a status.
You can specify a matrix by supplying an array for the configuration options. For example, if the GitHub virtual environment supports Node.js versions 6, 8, and 10 you could specify an array of those versions in the matrix.
When you define a matrix of operating systems, you must set the required runs-on keyword to the operating system of the current job, rather than hard-coding the operating system name. To access the operating system name, you can use the matrix.os context parameter to set runs-on. For more information, see https://help.github.com/en/articles/contexts-and-expression-syntax-for-github-actions.
"""


class Secrets1(StrEnum):
    """When a job is used to call a reusable workflow, you can use 'secrets' to provide a map of secrets that are passed to the called workflow. Any secrets that you pass must match the names defined in the called workflow."""

    INHERIT = "inherit"


class Strategy(BaseModel):
    """A strategy creates a build matrix for your jobs. You can define different variations of an environment to run each job in."""

    model_config = ConfigDict(
        extra="forbid",
    )
    matrix: Matrix
    fail_fast: Annotated[bool | str | None, Field(alias="fail-fast")] = True
    """
    When set to true, GitHub cancels all in-progress jobs if any matrix job fails. Default: true
    """
    max_parallel: Annotated[float | str | None, Field(alias="max-parallel")] = None
    """
    The maximum number of jobs that can run simultaneously when using a matrix job strategy. By default, GitHub will maximize the number of jobs run in parallel depending on the available runners on GitHub-hosted virtual machines.
    """


class RunsOn(BaseModel):
    """The type of machine to run the job on. The machine can be either a GitHub-hosted runner, or a self-hosted runner."""

    group: str | None = None
    labels: str | list[str] | None = None


class Type39(StrEnum):
    """A string representing the type of the input."""

    STRING = "string"
    CHOICE = "choice"
    BOOLEAN = "boolean"
    NUMBER = "number"
    ENVIRONMENT = "environment"


class WorkflowDispatchInput(BaseModel):
    """A string identifier to associate with the input. The value of <input_id> is a map of the input's metadata. The <input_id> must be a unique identifier within the inputs object. The <input_id> must start with a letter or _ and contain only alphanumeric characters, -, or _."""

    model_config = ConfigDict(
        extra="forbid",
    )
    description: str | None = None
    """
    A string description of the input parameter.
    """
    deprecation_message: Annotated[str | None, Field(alias="deprecationMessage")] = None
    """
    A string shown to users using the deprecated input.
    """
    required: bool | None = None
    """
    A boolean to indicate whether the action requires the input parameter. Set to true when the parameter is required.
    """
    default: Any | None = None
    """
    A string representing the default value. The default value is used when an input parameter isn't specified in a workflow file.
    """
    type: Type39 | None = None
    """
    A string representing the type of the input.
    """
    options: Annotated[list[str] | None, Field(min_length=1)] = None
    """
    The options of the dropdown list, if the type is a choice.
    """


type On = Annotated[list[Event], Field(min_length=1)]
"""
The name of the GitHub event that triggers the workflow. You can provide a single event string, array of events, array of event types, or an event configuration map that schedules a workflow or restricts the execution of a workflow to specific files, tags, or branch changes. For a list of available events, see https://help.github.com/en/github/automating-your-workflow-with-github-actions/events-that-trigger-workflows.
"""


class WorkflowDispatch(BaseModel):
    """You can now create workflows that are manually triggered with the new workflow_dispatch event. You will then see a 'Run workflow' button on the Actions tab, enabling you to easily trigger a run."""

    model_config = ConfigDict(
        extra="forbid",
    )
    inputs: (
        dict[constr(pattern=r"^[_a-zA-Z][a-zA-Z0-9_-]*$"), WorkflowDispatchInput] | None
    ) = None
    """
    Input parameters allow you to specify data that the action expects to use during runtime. GitHub stores input parameters as environment variables. Input ids with uppercase letters are converted to lowercase during runtime. We recommended using lowercase input ids.
    """


type Branch = Globs
"""
When using the push and pull_request events, you can configure a workflow to run on specific branches or tags. If you only define only tags or only branches, the workflow won't run for events affecting the undefined Git ref.
The branches, branches-ignore, tags, and tags-ignore keywords accept glob patterns that use the * and ** wildcard characters to match more than one branch or tag name. For more information, see https://help.github.com/en/github/automating-your-workflow-with-github-actions/workflow-syntax-for-github-actions#filter-pattern-cheat-sheet.
The patterns defined in branches and tags are evaluated against the Git ref's name. For example, defining the pattern mona/octocat in branches will match the refs/heads/mona/octocat Git ref. The pattern releases/** will match the refs/heads/releases/10 Git ref.
You can use two types of filters to prevent a workflow from running on pushes and pull requests to tags and branches:
- branches or branches-ignore - You cannot use both the branches and branches-ignore filters for the same event in a workflow. Use the branches filter when you need to filter branches for positive matches and exclude branches. Use the branches-ignore filter when you only need to exclude branch names.
- tags or tags-ignore - You cannot use both the tags and tags-ignore filters for the same event in a workflow. Use the tags filter when you need to filter tags for positive matches and exclude tags. Use the tags-ignore filter when you only need to exclude tag names.
You can exclude tags and branches using the ! character. The order that you define patterns matters.
- A matching negative pattern (prefixed with !) after a positive match will exclude the Git ref.
- A matching positive pattern after a negative match will include the Git ref again.
"""


class Concurrency(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    group: str
    """
    When a concurrent job or workflow is queued, if another job or workflow using the same concurrency group in the repository is in progress, the queued job or workflow will be pending. Any previously pending job or workflow in the concurrency group will be canceled.
    """
    cancel_in_progress: Annotated[
        bool | ExpressionSyntax | None, Field(alias="cancel-in-progress")
    ] = None
    """
    To cancel any currently running job or workflow in the same concurrency group, specify cancel-in-progress: true.
    """


class Run(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    shell: Shell | None = None
    working_directory: Annotated[
        WorkingDirectory | None, Field(alias="working-directory")
    ] = None


class Defaults(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    run: Run | None = None


class PermissionsEvent(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    actions: PermissionsLevel | None = None
    artifact_metadata: Annotated[
        PermissionsLevel | None, Field(alias="artifact-metadata")
    ] = None
    attestations: PermissionsLevel | None = None
    checks: PermissionsLevel | None = None
    contents: PermissionsLevel | None = None
    deployments: PermissionsLevel | None = None
    discussions: PermissionsLevel | None = None
    id_token: Annotated[PermissionsLevel | None, Field(alias="id-token")] = None
    issues: PermissionsLevel | None = None
    models: Models | None = None
    packages: PermissionsLevel | None = None
    pages: PermissionsLevel | None = None
    pull_requests: Annotated[PermissionsLevel | None, Field(alias="pull-requests")] = (
        None
    )
    repository_projects: Annotated[
        PermissionsLevel | None, Field(alias="repository-projects")
    ] = None
    security_events: Annotated[
        PermissionsLevel | None, Field(alias="security-events")
    ] = None
    statuses: PermissionsLevel | None = None


type Env = dict[str, str | float | bool] | StringContainingExpressionSyntax
"""
To set custom environment variables, you need to specify the variables in the workflow file. You can define environment variables for a step, job, or entire workflow using the jobs.<job_id>.steps[*].env, jobs.<job_id>.env, and env keywords. For more information, see https://docs.github.com/en/actions/learn-github-actions/workflow-syntax-for-github-actions#jobsjob_idstepsenv
"""


class Environment(BaseModel):
    """The environment that the job references."""

    model_config = ConfigDict(
        extra="forbid",
    )
    name: str
    """
    The name of the environment configured in the repo.
    """
    url: str | None = None
    """
    A deployment URL
    """
    deployment: bool | ExpressionSyntax | None = True
    """
    Whether to create a deployment for this job. Setting to false lets the job use environment secrets and variables without creating a deployment record. Wait timers and required reviewers still apply.
    """


class Step1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: str | None = None
    """
    A unique identifier for the step. You can use the id to reference the step in contexts. For more information, see https://help.github.com/en/articles/contexts-and-expression-syntax-for-github-actions.
    """
    if_: Annotated[bool | float | str | None, Field(alias="if")] = None
    """
    You can use the if conditional to prevent a step from running unless a condition is met. You can use any supported context and expression to create a conditional.
    Expressions in an if conditional do not require the ${{ }} syntax. For more information, see https://help.github.com/en/articles/contexts-and-expression-syntax-for-github-actions.
    """
    name: str | None = None
    """
    A name for your step to display on GitHub.
    """
    uses: str
    """
    Selects an action to run as part of a step in your job. An action is a reusable unit of code. You can use an action defined in the same repository as the workflow, a public repository, or in a published Docker container image (https://hub.docker.com/).
    We strongly recommend that you include the version of the action you are using by specifying a Git ref, SHA, or Docker tag number. If you don't specify a version, it could break your workflows or cause unexpected behavior when the action owner publishes an update.
    - Using the commit SHA of a released action version is the safest for stability and security.
    - Using the specific major action version allows you to receive critical fixes and security patches while still maintaining compatibility. It also assures that your workflow should still work.
    - Using the master branch of an action may be convenient, but if someone releases a new major version with a breaking change, your workflow could break.
    Some actions require inputs that you must set using the with keyword. Review the action's README file to determine the inputs required.
    Actions are either JavaScript files or Docker containers. If the action you're using is a Docker container you must run the job in a Linux virtual environment. For more details, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """
    run: str | None = None
    """
    Runs command-line programs using the operating system's shell. If you do not provide a name, the step name will default to the text specified in the run command.
    Commands run using non-login shells by default. You can choose a different shell and customize the shell used to run commands. For more information, see https://help.github.com/en/actions/automating-your-workflow-with-github-actions/workflow-syntax-for-github-actions#using-a-specific-shell.
    Each run keyword represents a new process and shell in the virtual environment. When you provide multi-line commands, each line runs in the same shell.
    """
    working_directory: Annotated[
        WorkingDirectory | None, Field(alias="working-directory")
    ] = None
    shell: Shell | None = None
    with_: Annotated[With | None, Field(alias="with")] = None
    """
    A map of the input parameters defined by the action. Each input parameter is a key/value pair. Input parameters are set as environment variables. The variable is prefixed with INPUT_ and converted to upper case.
    """
    env: Env | None = None
    """
    Sets environment variables for steps to use in the virtual environment. You can also set environment variables for the entire workflow or a job.
    """
    continue_on_error: Annotated[
        bool | ExpressionSyntax | None, Field(alias="continue-on-error")
    ] = False
    """
    Prevents a job from failing when a step fails. Set to true to allow a job to pass when this step fails.
    """
    timeout_minutes: Annotated[
        float | ExpressionSyntax | None, Field(alias="timeout-minutes")
    ] = None
    """
    The maximum number of minutes to run the step before killing the process.
    """


class Step2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: str | None = None
    """
    A unique identifier for the step. You can use the id to reference the step in contexts. For more information, see https://help.github.com/en/articles/contexts-and-expression-syntax-for-github-actions.
    """
    if_: Annotated[bool | float | str | None, Field(alias="if")] = None
    """
    You can use the if conditional to prevent a step from running unless a condition is met. You can use any supported context and expression to create a conditional.
    Expressions in an if conditional do not require the ${{ }} syntax. For more information, see https://help.github.com/en/articles/contexts-and-expression-syntax-for-github-actions.
    """
    name: str | None = None
    """
    A name for your step to display on GitHub.
    """
    uses: str | None = None
    """
    Selects an action to run as part of a step in your job. An action is a reusable unit of code. You can use an action defined in the same repository as the workflow, a public repository, or in a published Docker container image (https://hub.docker.com/).
    We strongly recommend that you include the version of the action you are using by specifying a Git ref, SHA, or Docker tag number. If you don't specify a version, it could break your workflows or cause unexpected behavior when the action owner publishes an update.
    - Using the commit SHA of a released action version is the safest for stability and security.
    - Using the specific major action version allows you to receive critical fixes and security patches while still maintaining compatibility. It also assures that your workflow should still work.
    - Using the master branch of an action may be convenient, but if someone releases a new major version with a breaking change, your workflow could break.
    Some actions require inputs that you must set using the with keyword. Review the action's README file to determine the inputs required.
    Actions are either JavaScript files or Docker containers. If the action you're using is a Docker container you must run the job in a Linux virtual environment. For more details, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """
    run: str
    """
    Runs command-line programs using the operating system's shell. If you do not provide a name, the step name will default to the text specified in the run command.
    Commands run using non-login shells by default. You can choose a different shell and customize the shell used to run commands. For more information, see https://help.github.com/en/actions/automating-your-workflow-with-github-actions/workflow-syntax-for-github-actions#using-a-specific-shell.
    Each run keyword represents a new process and shell in the virtual environment. When you provide multi-line commands, each line runs in the same shell.
    """
    working_directory: Annotated[
        WorkingDirectory | None, Field(alias="working-directory")
    ] = None
    shell: Shell | None = None
    with_: Annotated[With | None, Field(alias="with")] = None
    """
    A map of the input parameters defined by the action. Each input parameter is a key/value pair. Input parameters are set as environment variables. The variable is prefixed with INPUT_ and converted to upper case.
    """
    env: Env | None = None
    """
    Sets environment variables for steps to use in the virtual environment. You can also set environment variables for the entire workflow or a job.
    """
    continue_on_error: Annotated[
        bool | ExpressionSyntax | None, Field(alias="continue-on-error")
    ] = False
    """
    Prevents a job from failing when a step fails. Set to true to allow a job to pass when this step fails.
    """
    timeout_minutes: Annotated[
        float | ExpressionSyntax | None, Field(alias="timeout-minutes")
    ] = None
    """
    The maximum number of minutes to run the step before killing the process.
    """


type Step = Step1 | Step2


class PullRequest(BaseModel):
    """Runs your workflow anytime the pull_request event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/pulls.
    Note: Workflows do not run on private base repositories when you open a pull request from a forked repository.
    When you create a pull request from a forked repository to the base repository, GitHub sends the pull_request event to the base repository and no pull request events occur on the forked repository.
    Workflows don't run on forked repositories by default. You must enable GitHub Actions in the Actions tab of the forked repository.
    The permissions for the GITHUB_TOKEN in forked repositories is read-only. For more information about the GITHUB_TOKEN, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """

    types: list[Type26] | None = ["opened", "synchronize", "reopened"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """
    branches: Branch | None = None
    branches_ignore: Annotated[Branch | None, Field(alias="branches-ignore")] = None
    tags: Branch | None = None
    tags_ignore: Annotated[Branch | None, Field(alias="tags-ignore")] = None
    paths: Path | None = None
    paths_ignore: Annotated[Path | None, Field(alias="paths-ignore")] = None


class PullRequestTarget(BaseModel):
    """This event is similar to pull_request, except that it runs in the context of the base repository of the pull request, rather than in the merge commit. This means that you can more safely make your secrets available to the workflows triggered by the pull request, because only workflows defined in the commit on the base repository are run. For example, this event allows you to create workflows that label and comment on pull requests, based on the contents of the event payload."""

    types: list[Type31] | None = ["opened", "synchronize", "reopened"]
    """
    Selects the types of activity that will trigger a workflow run. Most GitHub events are triggered by more than one type of activity. For example, the event for the release resource is triggered when a release is published, unpublished, created, edited, deleted, or prereleased. The types keyword enables you to narrow down activity that causes the workflow to run. When only one activity type triggers a webhook event, the types keyword is unnecessary.
    You can use an array of event types. For more information about each event and their activity types, see https://help.github.com/en/articles/events-that-trigger-workflows#webhook-events.
    """
    branches: Branch | None = None
    branches_ignore: Annotated[Branch | None, Field(alias="branches-ignore")] = None
    tags: Branch | None = None
    tags_ignore: Annotated[Branch | None, Field(alias="tags-ignore")] = None
    paths: Path | None = None
    paths_ignore: Annotated[Path | None, Field(alias="paths-ignore")] = None


class Push(BaseModel):
    """Runs your workflow when someone pushes to a repository branch, which triggers the push event.
    Note: The webhook payload available to GitHub Actions does not include the added, removed, and modified attributes in the commit object. You can retrieve the full commit object using the REST API. For more information, see https://developer.github.com/v3/repos/commits/#get-a-single-commit.
    """

    branches: Branch | None = None
    branches_ignore: Annotated[Branch | None, Field(alias="branches-ignore")] = None
    tags: Branch | None = None
    tags_ignore: Annotated[Branch | None, Field(alias="tags-ignore")] = None
    paths: Path | None = None
    paths_ignore: Annotated[Path | None, Field(alias="paths-ignore")] = None


class On1(BaseModel):
    """The name of the GitHub event that triggers the workflow. You can provide a single event string, array of events, array of event types, or an event configuration map that schedules a workflow or restricts the execution of a workflow to specific files, tags, or branch changes. For a list of available events, see https://help.github.com/en/github/automating-your-workflow-with-github-actions/events-that-trigger-workflows."""

    model_config = ConfigDict(
        extra="forbid",
    )
    branch_protection_rule: BranchProtectionRule | None = None
    """
    Runs your workflow anytime the branch_protection_rule event occurs. More than one activity type triggers this event.
    """
    check_run: CheckRun | None = None
    """
    Runs your workflow anytime the check_run event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/checks/runs.
    """
    check_suite: CheckSuite | None = None
    """
    Runs your workflow anytime the check_suite event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/checks/suites/.
    """
    create: EventObject | None = None
    """
    Runs your workflow anytime someone creates a branch or tag, which triggers the create event. For information about the REST API, see https://developer.github.com/v3/git/refs/#create-a-reference.
    """
    delete: EventObject | None = None
    """
    Runs your workflow anytime someone deletes a branch or tag, which triggers the delete event. For information about the REST API, see https://developer.github.com/v3/git/refs/#delete-a-reference.
    """
    deployment: EventObject | None = None
    """
    Runs your workflow anytime someone creates a deployment, which triggers the deployment event. Deployments created with a commit SHA may not have a Git ref. For information about the REST API, see https://developer.github.com/v3/repos/deployments/.
    """
    deployment_status: EventObject | None = None
    """
    Runs your workflow anytime a third party provides a deployment status, which triggers the deployment_status event. Deployments created with a commit SHA may not have a Git ref. For information about the REST API, see https://developer.github.com/v3/repos/deployments/#create-a-deployment-status.
    """
    discussion: Discussion | None = None
    """
    Runs your workflow anytime the discussion event occurs. More than one activity type triggers this event. For information about the GraphQL API, see https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions
    """
    discussion_comment: DiscussionComment | None = None
    """
    Runs your workflow anytime the discussion_comment event occurs. More than one activity type triggers this event. For information about the GraphQL API, see https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions
    """
    fork: EventObject | None = None
    """
    Runs your workflow anytime when someone forks a repository, which triggers the fork event. For information about the REST API, see https://developer.github.com/v3/repos/forks/#create-a-fork.
    """
    gollum: EventObject | None = None
    """
    Runs your workflow when someone creates or updates a Wiki page, which triggers the gollum event.
    """
    issue_comment: IssueComment | None = None
    """
    Runs your workflow anytime the issue_comment event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues/comments/.
    """
    issues: Issues | None = None
    """
    Runs your workflow anytime the issues event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues.
    """
    label: Label | None = None
    """
    Runs your workflow anytime the label event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues/labels/.
    """
    merge_group: MergeGroup | None = None
    """
    Runs your workflow when a pull request is added to a merge queue, which adds the pull request to a merge group. For information about the merge queue, see https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/merging-a-pull-request-with-a-merge-queue .
    """
    milestone: Milestone | None = None
    """
    Runs your workflow anytime the milestone event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/issues/milestones/.
    """
    page_build: EventObject | None = None
    """
    Runs your workflow anytime someone pushes to a GitHub Pages-enabled branch, which triggers the page_build event. For information about the REST API, see https://developer.github.com/v3/repos/pages/.
    """
    project: Project | None = None
    """
    Runs your workflow anytime the project event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/projects/.
    """
    project_card: ProjectCard | None = None
    """
    Runs your workflow anytime the project_card event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/projects/cards.
    """
    project_column: ProjectColumn | None = None
    """
    Runs your workflow anytime the project_column event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/projects/columns.
    """
    public: EventObject | None = None
    """
    Runs your workflow anytime someone makes a private repository public, which triggers the public event. For information about the REST API, see https://developer.github.com/v3/repos/#edit.
    """
    pull_request: PullRequest | None = None
    """
    Runs your workflow anytime the pull_request event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/pulls.
    Note: Workflows do not run on private base repositories when you open a pull request from a forked repository.
    When you create a pull request from a forked repository to the base repository, GitHub sends the pull_request event to the base repository and no pull request events occur on the forked repository.
    Workflows don't run on forked repositories by default. You must enable GitHub Actions in the Actions tab of the forked repository.
    The permissions for the GITHUB_TOKEN in forked repositories is read-only. For more information about the GITHUB_TOKEN, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """
    pull_request_review: PullRequestReview | None = None
    """
    Runs your workflow anytime the pull_request_review event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/pulls/reviews.
    Note: Workflows do not run on private base repositories when you open a pull request from a forked repository.
    When you create a pull request from a forked repository to the base repository, GitHub sends the pull_request event to the base repository and no pull request events occur on the forked repository.
    Workflows don't run on forked repositories by default. You must enable GitHub Actions in the Actions tab of the forked repository.
    The permissions for the GITHUB_TOKEN in forked repositories is read-only. For more information about the GITHUB_TOKEN, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """
    pull_request_review_comment: PullRequestReviewComment | None = None
    """
    Runs your workflow anytime a comment on a pull request's unified diff is modified, which triggers the pull_request_review_comment event. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/pulls/comments.
    Note: Workflows do not run on private base repositories when you open a pull request from a forked repository.
    When you create a pull request from a forked repository to the base repository, GitHub sends the pull_request event to the base repository and no pull request events occur on the forked repository.
    Workflows don't run on forked repositories by default. You must enable GitHub Actions in the Actions tab of the forked repository.
    The permissions for the GITHUB_TOKEN in forked repositories is read-only. For more information about the GITHUB_TOKEN, see https://help.github.com/en/articles/virtual-environments-for-github-actions.
    """
    pull_request_target: PullRequestTarget | None = None
    """
    This event is similar to pull_request, except that it runs in the context of the base repository of the pull request, rather than in the merge commit. This means that you can more safely make your secrets available to the workflows triggered by the pull request, because only workflows defined in the commit on the base repository are run. For example, this event allows you to create workflows that label and comment on pull requests, based on the contents of the event payload.
    """
    push: Push | None = None
    """
    Runs your workflow when someone pushes to a repository branch, which triggers the push event.
    Note: The webhook payload available to GitHub Actions does not include the added, removed, and modified attributes in the commit object. You can retrieve the full commit object using the REST API. For more information, see https://developer.github.com/v3/repos/commits/#get-a-single-commit.
    """
    registry_package: RegistryPackage | None = None
    """
    Runs your workflow anytime a package is published or updated. For more information, see https://help.github.com/en/github/managing-packages-with-github-packages.
    """
    release: Release | None = None
    """
    Runs your workflow anytime the release event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/repos/releases/ in the GitHub Developer documentation.
    """
    status: EventObject | None = None
    """
    Runs your workflow anytime the status of a Git commit changes, which triggers the status event. For information about the REST API, see https://developer.github.com/v3/repos/statuses/.
    """
    watch: EventObject | None = None
    """
    Runs your workflow anytime the watch event occurs. More than one activity type triggers this event. For information about the REST API, see https://developer.github.com/v3/activity/starring/.
    """
    workflow_call: WorkflowCall | None = None
    """
    Allows workflows to be reused by other workflows.
    """
    workflow_dispatch: WorkflowDispatch | None = None
    """
    You can now create workflows that are manually triggered with the new workflow_dispatch event. You will then see a 'Run workflow' button on the Actions tab, enabling you to easily trigger a run.
    """
    workflow_run: WorkflowRun | None = None
    """
    This event occurs when a workflow run is requested or completed, and allows you to execute a workflow based on the finished result of another workflow. For example, if your pull_request workflow generates build artifacts, you can create a new workflow that uses workflow_run to analyze the results and add a comment to the original pull request.
    """
    repository_dispatch: EventObject | None = None
    """
    You can use the GitHub API to trigger a webhook event called repository_dispatch when you want to trigger a workflow for activity that happens outside of GitHub. For more information, see https://developer.github.com/v3/repos/#create-a-repository-dispatch-event.
    To trigger the custom repository_dispatch webhook event, you must send a POST request to a GitHub API endpoint and provide an event_type name to describe the activity type. To trigger a workflow run, you must also configure your workflow to use the repository_dispatch event.
    """
    schedule: Annotated[list[ScheduleItem] | None, Field(min_length=1)] = None
    """
    You can schedule a workflow to run at specific UTC times using POSIX cron syntax (https://pubs.opengroup.org/onlinepubs/9699919799/utilities/crontab.html#tag_20_25_07). You can optionally specify a timezone using an IANA timezone string (https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) for timezone-aware scheduling. Scheduled workflows run on the latest commit on the default or base branch. The shortest interval you can run scheduled workflows is once every 5 minutes.
    Note: GitHub Actions does not support the non-standard syntax @yearly, @monthly, @weekly, @daily, @hourly, and @reboot.
    You can use crontab guru (https://crontab.guru/) to help generate your cron syntax and confirm what time it will run. To help you get started, there is also a list of crontab guru examples (https://crontab.guru/examples.html).
    """


class Container(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    image: str
    """
    The Docker image to use as the container to run the action. The value can be the Docker Hub image name or a registry name.
    """
    credentials: Credentials | None = None
    """
    If the image's container registry requires authentication to pull the image, you can use credentials to set a map of the username and password. The credentials are the same values that you would provide to the `docker login` command.
    """
    env: Env | None = None
    """
    Sets an array of environment variables in the container.
    """
    ports: Annotated[list[float | str] | None, Field(min_length=1)] = None
    """
    Sets an array of ports to expose on the container.
    """
    volumes: Annotated[list[str] | None, Field(min_length=1)] = None
    """
    Sets an array of volumes for the container to use. You can use volumes to share data between services or other steps in a job. You can specify named Docker volumes, anonymous Docker volumes, or bind mounts on the host.
    To specify a volume, you specify the source and destination path: <source>:<destinationPath>
    The <source> is a volume name or an absolute path on the host machine, and <destinationPath> is an absolute path in the container.
    """
    options: str | None = None
    """
    Additional Docker container resource options. For a list of options, see https://docs.docker.com/engine/reference/commandline/create/#options.
    """


type Permissions = Permissions1 | PermissionsEvent
"""
You can modify the default permissions granted to the GITHUB_TOKEN, adding or removing access as required, so that you only allow the minimum required access.
"""


class ReusableWorkflowCallJob(BaseModel):
    """Each job must have an id to associate with the job. The key job_id is a string and its value is a map of the job's configuration data. You must replace <job_id> with a string that is unique to the jobs object. The <job_id> must start with a letter or _ and contain only alphanumeric characters, -, or _."""

    model_config = ConfigDict(
        extra="forbid",
    )
    name: str | None = None
    """
    The name of the job displayed on GitHub.
    """
    needs: JobNeeds | None = None
    permissions: Permissions | None = None
    if_: Annotated[bool | float | str | None, Field(alias="if")] = None
    """
    You can use the if conditional to prevent a job from running unless a condition is met. You can use any supported context and expression to create a conditional.
    Expressions in an if conditional do not require the ${{ }} syntax. For more information, see https://help.github.com/en/articles/contexts-and-expression-syntax-for-github-actions.
    """
    uses: Annotated[str, Field(pattern="^(.+\\/)+(.+)\\.(ya?ml)(@.+)?$")]
    """
    The location and version of a reusable workflow file to run as a job, of the form './{path/to}/{localfile}.yml' or '{owner}/{repo}/{path}/{filename}@{ref}'. {ref} can be a SHA, a release tag, or a branch name. Using the commit SHA is the safest for stability and security.
    """
    with_: Annotated[Env | None, Field(alias="with")] = None
    """
    A map of inputs that are passed to the called workflow. Any inputs that you pass must match the input specifications defined in the called workflow. Unlike 'jobs.<job_id>.steps[*].with', the inputs you pass with 'jobs.<job_id>.with' are not be available as environment variables in the called workflow. Instead, you can reference the inputs by using the inputs context.
    """
    secrets: Env | Secrets1 | None = None
    """
    When a job is used to call a reusable workflow, you can use 'secrets' to provide a map of secrets that are passed to the called workflow. Any secrets that you pass must match the names defined in the called workflow.
    """
    strategy: Strategy | None = None
    """
    A strategy creates a build matrix for your jobs. You can define different variations of an environment to run each job in.
    """
    concurrency: str | Concurrency | None = None
    """
    Concurrency ensures that only a single job or workflow using the same concurrency group will run at a time. A concurrency group can be any string or expression. The expression can use any context except for the secrets context.
    You can also specify concurrency at the workflow level.
    When a concurrent job or workflow is queued, if another job or workflow using the same concurrency group in the repository is in progress, the queued job or workflow will be pending. Any previously pending job or workflow in the concurrency group will be canceled. To also cancel any currently running job or workflow in the same concurrency group, specify cancel-in-progress: true.
    """


class NormalJob(BaseModel):
    """Each job must have an id to associate with the job. The key job_id is a string and its value is a map of the job's configuration data. You must replace <job_id> with a string that is unique to the jobs object. The <job_id> must start with a letter or _ and contain only alphanumeric characters, -, or _."""

    model_config = ConfigDict(
        extra="forbid",
    )
    name: str | None = None
    """
    The name of the job displayed on GitHub.
    """
    needs: JobNeeds | None = None
    snapshot: Snapshot | None = None
    permissions: Permissions | None = None
    runs_on: Annotated[
        str | list[Any] | RunsOn | StringContainingExpressionSyntax | ExpressionSyntax,
        Field(alias="runs-on"),
    ]
    """
    The type of machine to run the job on. The machine can be either a GitHub-hosted runner, or a self-hosted runner.
    """
    environment: str | Environment | None = None
    """
    The environment that the job references.
    """
    outputs: dict[str, str] | None = None
    """
    A map of outputs for a job. Job outputs are available to all downstream jobs that depend on this job.
    """
    env: Env | None = None
    """
    A map of environment variables that are available to all steps in the job.
    """
    defaults: Defaults | None = None
    """
    A map of default settings that will apply to all steps in the job.
    """
    if_: Annotated[bool | float | str | None, Field(alias="if")] = None
    """
    You can use the if conditional to prevent a job from running unless a condition is met. You can use any supported context and expression to create a conditional.
    Expressions in an if conditional do not require the ${{ }} syntax. For more information, see https://help.github.com/en/articles/contexts-and-expression-syntax-for-github-actions.
    """
    steps: Annotated[list[Step] | None, Field(min_length=1)] = None
    """
    A job contains a sequence of tasks called steps. Steps can run commands, run setup tasks, or run an action in your repository, a public repository, or an action published in a Docker registry. Not all steps run actions, but all actions run as a step. Each step runs in its own process in the virtual environment and has access to the workspace and filesystem. Because steps run in their own process, changes to environment variables are not preserved between steps. GitHub provides built-in steps to set up and complete a job.
    Must contain either `uses` or `run`

    """
    timeout_minutes: Annotated[
        float | ExpressionSyntax | None, Field(alias="timeout-minutes")
    ] = 360
    """
    The maximum number of minutes to let a workflow run before GitHub automatically cancels it. Default: 360
    """
    strategy: Strategy | None = None
    """
    A strategy creates a build matrix for your jobs. You can define different variations of an environment to run each job in.
    """
    continue_on_error: Annotated[
        bool | ExpressionSyntax | None, Field(alias="continue-on-error")
    ] = None
    """
    Prevents a workflow run from failing when a job fails. Set to true to allow a workflow run to pass when this job fails.
    """
    container: str | Container | None = None
    """
    A container to run any steps in a job that don't already specify a container. If you have steps that use both script and container actions, the container actions will run as sibling containers on the same network with the same volume mounts.
    If you do not set a container, all steps will run directly on the host specified by runs-on unless a step refers to an action configured to run in a container.
    """
    services: dict[str, Container] | None = None
    """
    Additional containers to host services for a job in a workflow. These are useful for creating databases or cache services like redis. The runner on the virtual machine will automatically create a network and manage the life cycle of the service containers.
    When you use a service container for a job or your step uses container actions, you don't need to set port information to access the service. Docker automatically exposes all ports between containers on the same network.
    When both the job and the action run in a container, you can directly reference the container by its hostname. The hostname is automatically mapped to the service name.
    When a step does not use a container action, you must access the service using localhost and bind the ports.
    """
    concurrency: str | Concurrency | None = None
    """
    Concurrency ensures that only a single job or workflow using the same concurrency group will run at a time. A concurrency group can be any string or expression. The expression can use any context except for the secrets context.
    You can also specify concurrency at the workflow level.
    When a concurrent job or workflow is queued, if another job or workflow using the same concurrency group in the repository is in progress, the queued job or workflow will be pending. Any previously pending job or workflow in the concurrency group will be canceled. To also cancel any currently running job or workflow in the same concurrency group, specify cancel-in-progress: true.
    """


class GitHubWorkflow(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: str | None = None
    """
    The name of your workflow. GitHub displays the names of your workflows on your repository's actions page. If you omit this field, GitHub sets the name to the workflow's filename.
    """
    on: Event | On | On1
    """
    The name of the GitHub event that triggers the workflow. You can provide a single event string, array of events, array of event types, or an event configuration map that schedules a workflow or restricts the execution of a workflow to specific files, tags, or branch changes. For a list of available events, see https://help.github.com/en/github/automating-your-workflow-with-github-actions/events-that-trigger-workflows.
    """
    env: Env | None = None
    """
    A map of environment variables that are available to all jobs and steps in the workflow.
    """
    defaults: Defaults | None = None
    """
    A map of default settings that will apply to all jobs in the workflow.
    """
    concurrency: str | Concurrency | None = None
    """
    Concurrency ensures that only a single job or workflow using the same concurrency group will run at a time. A concurrency group can be any string or expression. The expression can use any context except for the secrets context.
    You can also specify concurrency at the workflow level.
    When a concurrent job or workflow is queued, if another job or workflow using the same concurrency group in the repository is in progress, the queued job or workflow will be pending. Any previously pending job or workflow in the concurrency group will be canceled. To also cancel any currently running job or workflow in the same concurrency group, specify cancel-in-progress: true.
    """
    jobs: dict[
        constr(pattern=r"^[_a-zA-Z][a-zA-Z0-9_-]*$"),
        NormalJob | ReusableWorkflowCallJob,
    ]
    """
    A workflow run is made up of one or more jobs. Jobs run in parallel by default. To run jobs sequentially, you can define dependencies on other jobs using the jobs.<job_id>.needs keyword.
    Each job runs in a fresh instance of the virtual environment specified by runs-on.
    You can run an unlimited number of jobs as long as you are within the workflow usage limits. For more information, see https://help.github.com/en/github/automating-your-workflow-with-github-actions/workflow-syntax-for-github-actions#usage-limits.
    """
    run_name: Annotated[str | None, Field(alias="run-name")] = None
    """
    The name for workflow runs generated from the workflow. GitHub displays the workflow run name in the list of workflow runs on your repository's 'Actions' tab.
    """
    permissions: Permissions | None = None
