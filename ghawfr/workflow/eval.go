package workflow

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	actexpr "github.com/nektos/act/pkg/exprparser"
	actmodel "github.com/nektos/act/pkg/model"
	"github.com/rhysd/actionlint"
)

var (
	reExpressionString = regexp.MustCompile(`(?:''|[^'])*'`)
)

type expressionEvaluator struct {
	interpreter actexpr.Interpreter
}

func newExpressionEvaluator(job *Job, context ExpressionContext) expressionEvaluator {
	strategy := make(map[string]interface{})
	if job != nil && job.Strategy != nil {
		if job.Strategy.FailFast != nil {
			strategy["fail-fast"] = *job.Strategy.FailFast
		}
		if job.Strategy.MaxParallel != nil {
			strategy["max-parallel"] = *job.Strategy.MaxParallel
		}
	}
	return expressionEvaluator{
		interpreter: actexpr.NewInterpeter(&actexpr.EvaluationEnvironment{
			Github:   toActGitHubContext(context.GitHub),
			Runner:   toActRunnerContext(context.Runner),
			Env:      map[string]string(context.Env.Clone()),
			Vars:     map[string]string(context.Vars.Clone()),
			Secrets:  map[string]string(context.Secrets.Clone()),
			Inputs:   toActInputMap(context.Inputs),
			Needs:    toActNeeds(context.Needs),
			Steps:    toActSteps(context.Steps),
			Matrix:   toActMatrix(job),
			Strategy: strategy,
		}, actexpr.Config{Context: "job"}),
	}
}

// InterpolateString resolves GitHub Actions expression placeholders in one string.
func InterpolateString(job *Job, input string, context ExpressionContext) (string, error) {
	if input == "" || !actionlint.ContainsExpression(input) {
		return input, nil
	}
	result, err := newExpressionEvaluator(job, context).evaluateAny(input, actexpr.DefaultStatusCheckNone)
	if err != nil {
		return "", err
	}
	return stringifyAny(result), nil
}

// EvaluateCondition resolves one GitHub Actions if-expression to a boolean.
func EvaluateCondition(job *Job, input string, context ExpressionContext) (bool, error) {
	if strings.TrimSpace(input) == "" {
		return true, nil
	}
	result, err := newExpressionEvaluator(job, context).evaluateAny(input, actexpr.DefaultStatusCheckNone)
	if err != nil {
		return false, err
	}
	return actexpr.IsTruthy(result), nil
}

// InterpolateEnvironment resolves expressions inside one environment map.
func InterpolateEnvironment(job *Job, values EnvironmentMap, context ExpressionContext) (EnvironmentMap, error) {
	return interpolateStringMap(job, values, context)
}

// InterpolateActionInputs resolves expressions inside one action input map.
func InterpolateActionInputs(job *Job, values ActionInputMap, context ExpressionContext) (ActionInputMap, error) {
	return interpolateStringMap(job, values, context)
}

// ResolveRunner resolves expression-driven runs-on labels for one job.
func ResolveRunner(job *Job, context ExpressionContext) (Runner, error) {
	if job == nil {
		return Runner{}, nil
	}
	runner := job.RunsOn
	if runner.LabelsExpression == "" {
		return runner, nil
	}
	text, err := InterpolateString(job, runner.LabelsExpression, context)
	if err != nil {
		return Runner{}, err
	}
	text = strings.TrimSpace(text)
	runner.LabelsExpression = ""
	if text == "" {
		runner.Labels = nil
		return runner, nil
	}
	runner.Labels = []string{text}
	return runner, nil
}

// ResolveJobOutputs resolves declared job outputs from the given expression context.
func ResolveJobOutputs(job *Job, context ExpressionContext) (OutputMap, error) {
	if job == nil || len(job.OutputExpressions) == 0 {
		return nil, nil
	}
	outputs := make(OutputMap, len(job.OutputExpressions))
	for _, key := range job.OutputKeys {
		value, err := InterpolateString(job, job.OutputExpressions[key], context)
		if err != nil {
			return nil, fmt.Errorf("resolve output %q: %w", key, err)
		}
		outputs[key] = value
	}
	return outputs, nil
}

func interpolateStringMap[M ~map[string]string](job *Job, values M, context ExpressionContext) (M, error) {
	if len(values) == 0 {
		var zero M
		return zero, nil
	}
	resolved := make(M, len(values))
	for key, value := range values {
		text, err := InterpolateString(job, value, context)
		if err != nil {
			var zero M
			return zero, fmt.Errorf("resolve %q: %w", key, err)
		}
		resolved[key] = text
	}
	return resolved, nil
}

func (e expressionEvaluator) evaluateExpression(input string) (Value, error) {
	result, err := e.evaluateAny(input, actexpr.DefaultStatusCheckNone)
	if err != nil {
		return Value{}, err
	}
	return anyToValue(result), nil
}

func (e expressionEvaluator) evaluateAny(input string, status actexpr.DefaultStatusCheck) (any, error) {
	expr, err := rewriteSubExpression(input, false)
	if err != nil {
		return nil, err
	}
	return e.interpreter.Evaluate(expr, status)
}

func (e expressionEvaluator) evaluateValue(value Value) (Value, error) {
	switch value.Kind {
	case ValueKindArray:
		items := make([]Value, 0, len(value.Array))
		for _, item := range value.Array {
			evaluated, err := e.evaluateValue(item)
			if err != nil {
				return Value{}, err
			}
			if item.Kind == ValueKindScalar && isExpressionAssigned(item.Scalar) && evaluated.Kind == ValueKindArray {
				items = append(items, cloneValues(evaluated.Array)...)
				continue
			}
			items = append(items, evaluated)
		}
		return Value{Kind: ValueKindArray, Array: items}, nil
	case ValueKindObject:
		object := make(map[string]Value, len(value.Object))
		for _, key := range value.ObjectKeys() {
			evaluatedValue, err := e.evaluateValue(value.Object[key])
			if err != nil {
				return Value{}, err
			}
			if isInsertDirective(key) {
				if evaluatedValue.Kind != ValueKindObject {
					return Value{}, fmt.Errorf("insert directive expects object value, got %s", evaluatedValue.Kind)
				}
				for childKey, childValue := range evaluatedValue.Object {
					object[canonicalID(childKey)] = childValue
				}
				continue
			}
			evaluatedKey, err := e.evaluateStringKey(key)
			if err != nil {
				return Value{}, err
			}
			object[canonicalID(evaluatedKey)] = evaluatedValue
		}
		return Value{Kind: ValueKindObject, Object: object}, nil
	default:
		if !actionlint.ContainsExpression(value.Scalar) {
			return value, nil
		}
		return e.evaluateExpression(value.Scalar)
	}
}

func (e expressionEvaluator) evaluateStringKey(input string) (string, error) {
	if !actionlint.ContainsExpression(input) {
		return input, nil
	}
	value, err := e.evaluateExpression(input)
	if err != nil {
		return "", err
	}
	if value.Kind != ValueKindScalar {
		return "", fmt.Errorf("expression key must resolve to a scalar string")
	}
	return value.Scalar, nil
}

func resolveDynamicMatrix(job *Job, context ExpressionContext) (*Matrix, error) {
	if job == nil || job.Strategy == nil || job.Strategy.Matrix == nil {
		return nil, nil
	}
	evaluator := newExpressionEvaluator(job, context)
	return evaluator.resolveMatrix(job.Strategy.Matrix)
}

func (e expressionEvaluator) resolveMatrix(matrix *Matrix) (*Matrix, error) {
	if matrix == nil {
		return nil, nil
	}
	if matrix.Expression != "" {
		value, err := e.evaluateExpression(matrix.Expression)
		if err != nil {
			return nil, err
		}
		return matrixFromValue(value)
	}

	resolved := &Matrix{Rows: make([]MatrixRow, 0, len(matrix.Rows))}
	for _, row := range matrix.Rows {
		resolvedRow, err := e.resolveMatrixRow(row)
		if err != nil {
			return nil, err
		}
		resolved.Rows = append(resolved.Rows, resolvedRow)
	}
	sort.Slice(resolved.Rows, func(i, j int) bool {
		return resolved.Rows[i].Name < resolved.Rows[j].Name
	})

	include, err := e.resolveMatrixCombinations(matrix.Include)
	if err != nil {
		return nil, err
	}
	exclude, err := e.resolveMatrixCombinations(matrix.Exclude)
	if err != nil {
		return nil, err
	}
	resolved.Include = include
	resolved.Exclude = exclude
	return resolved, nil
}

func (e expressionEvaluator) resolveMatrixRow(row MatrixRow) (MatrixRow, error) {
	resolved := MatrixRow{Name: row.Name}
	if row.Expression != "" {
		value, err := e.evaluateExpression(row.Expression)
		if err != nil {
			return MatrixRow{}, err
		}
		resolved.Values = rowValuesFromValue(value)
		return resolved, nil
	}
	items := make([]Value, 0, len(row.Values))
	for _, value := range row.Values {
		evaluated, err := e.evaluateValue(value)
		if err != nil {
			return MatrixRow{}, err
		}
		if value.Kind == ValueKindScalar && isExpressionAssigned(value.Scalar) && evaluated.Kind == ValueKindArray {
			items = append(items, cloneValues(evaluated.Array)...)
			continue
		}
		items = append(items, evaluated)
	}
	resolved.Values = items
	return resolved, nil
}

func (e expressionEvaluator) resolveMatrixCombinations(combinations []MatrixCombination) ([]MatrixCombination, error) {
	resolved := make([]MatrixCombination, 0, len(combinations))
	for _, combination := range combinations {
		if combination.Expression != "" {
			value, err := e.evaluateExpression(combination.Expression)
			if err != nil {
				return nil, err
			}
			converted, err := matrixCombinationsFromValue(value)
			if err != nil {
				return nil, err
			}
			resolved = append(resolved, converted...)
			continue
		}
		values := make(map[string]Value, len(combination.Values))
		for _, key := range combination.Keys {
			evaluated, err := e.evaluateValue(combination.Values[key])
			if err != nil {
				return nil, err
			}
			values[key] = evaluated
		}
		resolved = append(resolved, MatrixCombination{Values: values, Keys: sortedMatrixKeys(values)})
	}
	return resolved, nil
}

func matrixFromValue(value Value) (*Matrix, error) {
	if value.Kind != ValueKindObject {
		return nil, fmt.Errorf("matrix expression must resolve to an object, got %s", value.Kind)
	}
	matrix := &Matrix{Rows: make([]MatrixRow, 0, len(value.Object))}
	for _, key := range value.ObjectKeys() {
		switch key {
		case "include":
			combinations, err := matrixCombinationsFromValue(value.Object[key])
			if err != nil {
				return nil, fmt.Errorf("decode include combinations: %w", err)
			}
			matrix.Include = combinations
		case "exclude":
			combinations, err := matrixCombinationsFromValue(value.Object[key])
			if err != nil {
				return nil, fmt.Errorf("decode exclude combinations: %w", err)
			}
			matrix.Exclude = combinations
		default:
			matrix.Rows = append(matrix.Rows, MatrixRow{Name: key, Values: rowValuesFromValue(value.Object[key])})
		}
	}
	sort.Slice(matrix.Rows, func(i, j int) bool {
		return matrix.Rows[i].Name < matrix.Rows[j].Name
	})
	return matrix, nil
}

func matrixCombinationsFromValue(value Value) ([]MatrixCombination, error) {
	switch value.Kind {
	case ValueKindObject:
		return []MatrixCombination{{Values: cloneMatrixValues(value.Object), Keys: value.ObjectKeys()}}, nil
	case ValueKindArray:
		combinations := make([]MatrixCombination, 0, len(value.Array))
		for _, item := range value.Array {
			if item.Kind != ValueKindObject {
				return nil, fmt.Errorf("matrix combination array items must be objects, got %s", item.Kind)
			}
			combinations = append(combinations, MatrixCombination{Values: cloneMatrixValues(item.Object), Keys: item.ObjectKeys()})
		}
		return combinations, nil
	default:
		return nil, fmt.Errorf("matrix combinations must resolve to an object or array, got %s", value.Kind)
	}
}

func rowValuesFromValue(value Value) []Value {
	if value.Kind == ValueKindArray {
		return cloneValues(value.Array)
	}
	return []Value{value.Clone()}
}

func anyToValue(value any) Value {
	return dataToValue(DataFromAny(value))
}

func dataToValue(value Data) Value {
	switch value.Kind {
	case DataKindNull:
		return Value{Kind: ValueKindScalar, Scalar: ""}
	case DataKindString:
		return Value{Kind: ValueKindScalar, Scalar: value.String}
	case DataKindBoolean:
		if value.Boolean {
			return Value{Kind: ValueKindScalar, Scalar: "true"}
		}
		return Value{Kind: ValueKindScalar, Scalar: "false"}
	case DataKindNumber:
		return Value{Kind: ValueKindScalar, Scalar: fmt.Sprintf("%v", value.Number)}
	case DataKindArray:
		items := make([]Value, 0, len(value.Array))
		for _, item := range value.Array {
			items = append(items, dataToValue(item))
		}
		return Value{Kind: ValueKindArray, Array: items}
	case DataKindObject:
		object := make(map[string]Value, len(value.Object))
		for key, item := range value.Object {
			object[canonicalID(key)] = dataToValue(item)
		}
		return Value{Kind: ValueKindObject, Object: object}
	default:
		return Value{Kind: ValueKindScalar, Scalar: value.String}
	}
}

func stringifyAny(value any) string {
	switch value := value.(type) {
	case nil:
		return ""
	case string:
		return value
	case bool:
		if value {
			return "true"
		}
		return "false"
	case int:
		return fmt.Sprintf("%d", value)
	case int64:
		return fmt.Sprintf("%d", value)
	case float64:
		return fmt.Sprintf("%v", value)
	default:
		return anyToValue(value).IdentifierString()
	}
}

func toActGitHubContext(context GitHubContext) *actmodel.GithubContext {
	return &actmodel.GithubContext{
		Event:      toActDataMap(context.Event),
		EventName:  context.EventName,
		Ref:        context.Ref,
		RefName:    context.RefName,
		RefType:    context.RefType,
		Sha:        context.Sha,
		HeadRef:    context.HeadRef,
		BaseRef:    context.BaseRef,
		Repository: context.Repository,
		Actor:      context.Actor,
		Workspace:  context.Workspace,
	}
}

func toActRunnerContext(context RunnerContext) map[string]any {
	if context == (RunnerContext{}) {
		return nil
	}
	values := make(map[string]any)
	if context.OS != "" {
		values["os"] = context.OS
	}
	if context.Arch != "" {
		values["arch"] = context.Arch
	}
	if context.Name != "" {
		values["name"] = context.Name
	}
	if context.Temp != "" {
		values["temp"] = context.Temp
	}
	if context.ToolCache != "" {
		values["tool_cache"] = context.ToolCache
	}
	if len(values) == 0 {
		return nil
	}
	return values
}

func toActInputMap(values InputMap) map[string]any {
	return toActDataMap(values)
}

func toActDataMap[T ~map[string]Data](values T) map[string]any {
	if len(values) == 0 {
		return nil
	}
	converted := make(map[string]any, len(values))
	for key, value := range values {
		converted[key] = value.Any()
	}
	return converted
}

func toActNeeds(needs NeedContextMap) map[string]actexpr.Needs {
	if len(needs) == 0 {
		return nil
	}
	converted := make(map[string]actexpr.Needs, len(needs))
	for key, need := range needs {
		converted[key.String()] = actexpr.Needs{Outputs: map[string]string(need.Outputs.Clone()), Result: need.Result}
	}
	return converted
}

func toActSteps(steps StepContextMap) map[string]*actmodel.StepResult {
	if len(steps) == 0 {
		return nil
	}
	converted := make(map[string]*actmodel.StepResult, len(steps))
	for key, step := range steps {
		result := &actmodel.StepResult{Outputs: map[string]string(step.Outputs.Clone())}
		_ = result.Conclusion.UnmarshalText([]byte(step.Conclusion))
		_ = result.Outcome.UnmarshalText([]byte(step.Outcome))
		converted[key.String()] = result
	}
	return converted
}

func toActMatrix(job *Job) map[string]any {
	if job == nil || len(job.MatrixValues) == 0 {
		return nil
	}
	converted := make(map[string]any, len(job.MatrixValues))
	for key, value := range job.MatrixValues {
		converted[key] = valueToAny(value)
	}
	return converted
}

func valueToAny(value Value) any {
	switch value.Kind {
	case ValueKindArray:
		items := make([]any, 0, len(value.Array))
		for _, item := range value.Array {
			items = append(items, valueToAny(item))
		}
		return items
	case ValueKindObject:
		items := make(map[string]any, len(value.Object))
		for key, item := range value.Object {
			items[key] = valueToAny(item)
		}
		return items
	default:
		return value.Scalar
	}
}

func isInsertDirective(input string) bool {
	trimmed := canonicalID(strings.TrimSpace(input))
	return trimmed == "${{ insert }}" || trimmed == "${{insert}}"
}

func isExpressionAssigned(input string) bool {
	trimmed := strings.TrimSpace(input)
	return strings.HasPrefix(trimmed, "${{") && strings.HasSuffix(trimmed, "}}") && strings.Count(trimmed, "${{") == 1
}

func valueContainsExpression(value Value) bool {
	switch value.Kind {
	case ValueKindArray:
		for _, item := range value.Array {
			if valueContainsExpression(item) {
				return true
			}
		}
	case ValueKindObject:
		for key, item := range value.Object {
			if actionlint.ContainsExpression(key) || valueContainsExpression(item) {
				return true
			}
		}
	default:
		return actionlint.ContainsExpression(value.Scalar)
	}
	return false
}

func rewriteSubExpression(input string, forceFormat bool) (string, error) {
	if !strings.Contains(input, "${{") || !strings.Contains(input, "}}") {
		return input, nil
	}
	position := 0
	exprStart := -1
	stringStart := -1
	results := make([]string, 0)
	formatOut := ""
	for position < len(input) {
		switch {
		case stringStart > -1:
			matches := reExpressionString.FindStringIndex(input[position:])
			if matches == nil {
				return "", fmt.Errorf("unclosed string in expression %q", input)
			}
			stringStart = -1
			position += matches[1]
		case exprStart > -1:
			exprEnd := strings.Index(input[position:], "}}")
			stringStart = strings.Index(input[position:], "'")
			if exprEnd > -1 && stringStart > -1 {
				if exprEnd < stringStart {
					stringStart = -1
				} else {
					exprEnd = -1
				}
			}
			if exprEnd > -1 {
				formatOut += fmt.Sprintf("{%d}", len(results))
				results = append(results, strings.TrimSpace(input[exprStart:position+exprEnd]))
				position += exprEnd + 2
				exprStart = -1
				continue
			}
			if stringStart > -1 {
				position += stringStart + 1
				continue
			}
			return "", fmt.Errorf("unclosed expression %q", input)
		default:
			exprStart = strings.Index(input[position:], "${{")
			if exprStart == -1 {
				formatOut += escapeFormatString(input[position:])
				position = len(input)
				continue
			}
			formatOut += escapeFormatString(input[position : position+exprStart])
			exprStart = position + exprStart + 3
			position = exprStart
		}
	}
	if len(results) == 1 && formatOut == "{0}" && !forceFormat {
		return input, nil
	}
	return fmt.Sprintf("format('%s', %s)", strings.ReplaceAll(formatOut, "'", "''"), strings.Join(results, ", ")), nil
}

func escapeFormatString(input string) string {
	return strings.ReplaceAll(strings.ReplaceAll(input, "{", "{{"), "}", "}}")
}
