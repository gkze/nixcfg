"""GitHub expression subset used by the workflow files."""

import pytest

from lib.buildkite.execute import eval_text
from lib.buildkite.github_expr import (
    ExprContext,
    GitHubExprError,
    Name,
    expression_text,
    interpolate,
    parse,
    references_needs,
    stringify,
    tokenize,
    truthy,
)


def _context() -> ExprContext:
    return ExprContext(
        inputs={},
        needs={},
        steps={},
        github={},
        runner={},
        secrets={},
        matrix={},
    )


def test_tokenizer_rejects_junk_and_unterminated_strings() -> None:
    with pytest.raises(GitHubExprError, match="unexpected"):
        tokenize("$")
    with pytest.raises(GitHubExprError, match="unterminated"):
        tokenize("'open")


def test_parser_rejects_trailing_tokens_and_early_end() -> None:
    with pytest.raises(GitHubExprError, match="trailing"):
        parse("true true")
    with pytest.raises(GitHubExprError, match="end of expression"):
        parse("format(")
    with pytest.raises(GitHubExprError, match="expected a value"):
        parse("( )")


def test_truthy_and_stringify_follow_actions() -> None:
    assert truthy("false") is True
    assert truthy("") is False
    assert truthy(None) is False
    assert truthy(value=False) is False
    assert truthy(0) is False
    assert truthy(2) is True
    assert truthy([1]) is True
    assert stringify(None) == ""
    assert stringify(value=True) == "true"
    assert stringify(value=False) == "false"
    assert stringify({"a": 1}) == '{"a":1}'
    assert stringify([1]) == "[1]"


def test_operators_and_calls() -> None:
    context = ExprContext(
        inputs={"stage": "prepare", "count": 0},
        needs={"prepare-pins": {"result": "success", "outputs": {"device_fault": ""}}},
        steps={"candidate": {"outputs": {"device_fault": "true"}}},
        github={
            "ref_name": "topic",
            "event": {"repository": {"default_branch": "main"}},
        },
        runner={"temp": "/tmp"},
        secrets={"CACHIX_AUTH_TOKEN": "secret-value"},
        matrix={"slot": "1"},
        success=False,
        failure=True,
        cancelled=True,
    )
    assert context.eval(parse("inputs.stage == 'prepare' && inputs.missing")) == ""
    assert context.eval(parse("inputs.missing || 'fallback'")) == "fallback"
    assert context.eval(parse("inputs.stage != 'validate'")) is True
    assert context.eval(parse("success()")) is False
    assert context.eval(parse("failure()")) is True
    assert context.eval(parse("cancelled()")) is True
    assert context.eval(parse("true || false")) is True
    assert context.eval(parse("false && true")) is False
    assert context.eval(parse("contains(fromJSON('[\"a\"]'), 'a')")) is True
    assert context.eval(parse("contains('abc', 'b')")) is True
    assert context.eval(parse("startsWith(github.ref_name, 'top')")) is True
    assert context.eval(parse("format('{0}-{1}', matrix.slot, 'ok')")) == "1-ok"
    assert context.eval(parse("null")) is None
    assert context.eval(parse("secrets.CACHIX_AUTH_TOKEN")) == "secret-value"
    assert context.eval(parse("needs.*.result")) == ["success"]
    assert references_needs(parse("!needs.prepare-pins.result")) is True
    assert (
        references_needs(parse("needs.prepare-pins.result == 'success' || true"))
        is True
    )
    assert references_needs(parse("contains(needs.prepare-pins.result, 's')")) is True
    assert references_needs(parse("'literal'")) is False
    assert eval_text("  ", context) == ""
    assert eval_text("inputs.stage", context) == "prepare"
    assert eval_text("pre ${{ inputs.stage }}", context) == "pre prepare"
    assert expression_text("plain") is None
    assert expression_text("${{ inputs.stage }}") == " inputs.stage "
    assert interpolate("${{ true }}", context) == "true"
    assert context.eval(parse("12")) == 12


def test_expression_errors() -> None:
    context = _context()
    context.inputs = {"stage": "prepare"}
    with pytest.raises(GitHubExprError, match="unknown context"):
        context.eval(parse("job.result"))
    with pytest.raises(GitHubExprError, match="cannot read"):
        context.eval(parse("inputs.stage.length"))
    with pytest.raises(GitHubExprError, match="wildcard"):
        context.eval(parse("matrix.*.slot"))
    with pytest.raises(GitHubExprError, match="expects 0"):
        context.eval(parse("always(1)"))
    with pytest.raises(GitHubExprError, match="requires a template"):
        context.eval(parse("format()"))
    with pytest.raises(GitHubExprError, match="invalid JSON"):
        context.eval(parse("fromJSON('{')"))
    with pytest.raises(GitHubExprError, match="unsupported function"):
        context.eval(parse("hashFiles('a')"))
    with pytest.raises(GitHubExprError, match="string or a list"):
        context.eval(parse("contains(1, 1)"))
    with pytest.raises(GitHubExprError, match="end of expression"):
        parse("true ==")
    with pytest.raises(GitHubExprError, match="expected"):
        parse("(true true)")
    assert references_needs(Name(("matrix", "slot"))) is False
