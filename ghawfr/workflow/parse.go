package workflow

import (
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/rhysd/actionlint"
)

// ParseError reports one or more workflow parser diagnostics.
type ParseError struct {
	SourcePath  string
	Diagnostics []Diagnostic
}

func (e *ParseError) Error() string {
	if len(e.Diagnostics) == 0 {
		if e.SourcePath == "" {
			return "workflow parse failed"
		}
		return fmt.Sprintf("workflow parse failed: %s", e.SourcePath)
	}
	first := e.Diagnostics[0]
	if first.Position.Line > 0 {
		return fmt.Sprintf(
			"workflow parse failed: %s:%d:%d: %s [%s]",
			first.SourcePath,
			first.Position.Line,
			first.Position.Column,
			first.Message,
			first.Kind,
		)
	}
	return fmt.Sprintf("workflow parse failed: %s: %s [%s]", first.SourcePath, first.Message, first.Kind)
}

// Parse parses one workflow document into ghawfr's normalized IR.
func Parse(sourcePath string, data []byte) (*Workflow, error) {
	return ParseWithOptions(sourcePath, data, ParseOptions{})
}

// ParseWithOptions parses one workflow document into ghawfr's normalized IR with explicit evaluation options.
func ParseWithOptions(sourcePath string, data []byte, options ParseOptions) (*Workflow, error) {
	parsed, errs := actionlint.Parse(data)
	if len(errs) > 0 {
		return nil, &ParseError{SourcePath: sourcePath, Diagnostics: normalizeDiagnostics(sourcePath, errs)}
	}
	if parsed == nil {
		return nil, &ParseError{SourcePath: sourcePath}
	}
	workflow, diagnostics := normalizeWorkflow(sourcePath, parsed, options)
	if len(diagnostics) > 0 {
		return nil, &ParseError{SourcePath: sourcePath, Diagnostics: diagnostics}
	}
	return workflow, nil
}

// ParseFile reads and parses one workflow file from disk.
func ParseFile(path string) (*Workflow, error) {
	return ParseFileWithOptions(path, ParseOptions{})
}

// ParseFileWithOptions reads and parses one workflow file from disk with explicit evaluation options.
func ParseFileWithOptions(path string, options ParseOptions) (*Workflow, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read workflow %q: %w", path, err)
	}
	workflow, err := ParseWithOptions(path, data, options)
	if err != nil {
		return nil, err
	}
	return workflow, nil
}

func normalizeDiagnostics(sourcePath string, errs []*actionlint.Error) []Diagnostic {
	diagnostics := make([]Diagnostic, 0, len(errs))
	for _, err := range errs {
		if err == nil {
			continue
		}
		path := sourcePath
		if err.Filepath != "" {
			path = err.Filepath
		}
		diagnostics = append(diagnostics, Diagnostic{
			Message:    err.Message,
			Kind:       err.Kind,
			SourcePath: path,
			Position: Position{
				Line:   err.Line,
				Column: err.Column,
			},
		})
	}
	return diagnostics
}

func normalizeWorkflow(sourcePath string, parsed *actionlint.Workflow, options ParseOptions) (*Workflow, []Diagnostic) {
	workflow := &Workflow{
		Name:       stringValue(parsed.Name),
		RunName:    stringValue(parsed.RunName),
		SourcePath: sourcePath,
		Events:     normalizeEvents(parsed.On),
		Jobs:       make(JobMap, len(parsed.Jobs)),
	}

	jobs := make([]*Job, 0, len(parsed.Jobs))
	for jobID, parsedJob := range parsed.Jobs {
		job := normalizeJob(jobID, parsedJob)
		workflow.Jobs[job.ID] = job
		jobs = append(jobs, job)
	}
	sort.SliceStable(jobs, func(i, j int) bool {
		return compareJobOrder(jobs[i], jobs[j]) < 0
	})
	workflow.JobOrder = make(JobIDs, 0, len(jobs))
	for _, job := range jobs {
		workflow.JobOrder = append(workflow.JobOrder, job.ID)
	}
	return expandWorkflow(workflow, options)
}

func normalizeEvents(events []actionlint.Event) []Event {
	result := make([]Event, 0, len(events))
	for _, event := range events {
		if event == nil {
			continue
		}
		result = append(result, Event{
			Name:     event.EventName(),
			Position: eventPosition(event),
		})
	}
	return result
}

func normalizeJob(jobID string, parsed *actionlint.Job) *Job {
	normalizedID := canonicalJobID(jobID)
	if parsed != nil && parsed.ID != nil && parsed.ID.Value != "" {
		normalizedID = canonicalJobID(parsed.ID.Value)
	}
	outputExpressions, outputKeys := normalizeOutputExpressions(parsed)
	job := &Job{
		ID:                normalizedID,
		LogicalID:         normalizedID,
		Name:              stringValue(jobString(parsed, func(job *actionlint.Job) *actionlint.String { return job.Name })),
		Needs:             normalizeNeeds(parsed),
		If:                stringValue(jobString(parsed, func(job *actionlint.Job) *actionlint.String { return job.If })),
		Env:               normalizeEnv(parsedEnv(parsed)),
		RunsOn:            normalizeRunner(parsed),
		Strategy:          normalizeStrategy(parsed),
		OutputExpressions: outputExpressions,
		OutputKeys:        outputKeys,
		Steps:             normalizeSteps(parsed),
		Position:          jobPosition(parsed),
	}
	if parsed != nil && parsed.WorkflowCall != nil {
		job.WorkflowCall = &WorkflowCall{
			Uses:           stringValue(parsed.WorkflowCall.Uses),
			InheritSecrets: parsed.WorkflowCall.InheritSecrets,
		}
	}
	return job
}

func normalizeOutputExpressions(parsed *actionlint.Job) (OutputMap, []string) {
	if parsed == nil || len(parsed.Outputs) == 0 {
		return nil, nil
	}
	outputs := make(OutputMap, len(parsed.Outputs))
	keys := make([]string, 0, len(parsed.Outputs))
	for key, output := range parsed.Outputs {
		if output == nil {
			continue
		}
		outputs[canonicalID(key)] = stringValue(output.Value)
		keys = append(keys, canonicalID(key))
	}
	sort.Strings(keys)
	return outputs, keys
}

func normalizeNeeds(parsed *actionlint.Job) JobIDs {
	if parsed == nil || len(parsed.Needs) == 0 {
		return nil
	}
	seen := make(map[JobID]struct{}, len(parsed.Needs))
	needs := make(JobIDs, 0, len(parsed.Needs))
	for _, need := range parsed.Needs {
		value := canonicalJobID(stringValue(need))
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		needs = append(needs, value)
	}
	return needs
}

func normalizeEnv(parsed *actionlint.Env) EnvironmentMap {
	if parsed == nil || len(parsed.Vars) == 0 {
		return nil
	}
	values := make(EnvironmentMap, len(parsed.Vars))
	for key, variable := range parsed.Vars {
		if variable == nil || variable.Value == nil {
			continue
		}
		values[key] = variable.Value.Value
	}
	if len(values) == 0 {
		return nil
	}
	return values
}

func normalizeRunner(parsed *actionlint.Job) Runner {
	if parsed == nil || parsed.RunsOn == nil {
		return Runner{}
	}
	runner := Runner{
		Group:            stringValue(parsed.RunsOn.Group),
		LabelsExpression: stringValue(parsed.RunsOn.LabelsExpr),
	}
	labels := make([]string, 0, len(parsed.RunsOn.Labels))
	for _, label := range parsed.RunsOn.Labels {
		if value := stringValue(label); value != "" {
			labels = append(labels, value)
		}
	}
	runner.Labels = labels
	return runner
}

func normalizeStrategy(parsed *actionlint.Job) *Strategy {
	if parsed == nil || parsed.Strategy == nil {
		return nil
	}
	strategy := &Strategy{}
	if parsed.Strategy.FailFast != nil {
		value := parsed.Strategy.FailFast.Value
		strategy.FailFast = &value
	}
	if parsed.Strategy.MaxParallel != nil {
		value := parsed.Strategy.MaxParallel.Value
		strategy.MaxParallel = &value
	}
	if parsed.Strategy.Matrix != nil {
		strategy.Matrix = normalizeMatrix(parsed.Strategy.Matrix)
	}
	return strategy
}

func normalizeMatrix(parsed *actionlint.Matrix) *Matrix {
	if parsed == nil {
		return nil
	}
	matrix := &Matrix{
		Expression: stringValue(parsed.Expression),
		Dynamic:    parsed.Expression != nil,
	}
	rows := make([]MatrixRow, 0, len(parsed.Rows))
	for rowName, row := range parsed.Rows {
		if row == nil {
			continue
		}
		values := normalizeRawValues(row.Values)
		matrixRow := MatrixRow{
			Name:       canonicalID(rowName),
			Values:     values,
			Expression: stringValue(row.Expression),
		}
		if row.Expression != nil {
			matrix.Dynamic = true
		}
		for _, value := range values {
			if valueContainsExpression(value) {
				matrix.Dynamic = true
				break
			}
		}
		rows = append(rows, matrixRow)
	}
	sort.Slice(rows, func(i, j int) bool {
		return rows[i].Name < rows[j].Name
	})
	matrix.Rows = rows
	matrix.Include = normalizeMatrixCombinations(parsed.Include, &matrix.Dynamic)
	matrix.Exclude = normalizeMatrixCombinations(parsed.Exclude, &matrix.Dynamic)
	return matrix
}

func normalizeMatrixCombinations(parsed *actionlint.MatrixCombinations, dynamic *bool) []MatrixCombination {
	if parsed == nil {
		return nil
	}
	if parsed.Expression != nil {
		*dynamic = true
	}
	combinations := make([]MatrixCombination, 0, len(parsed.Combinations))
	for _, combination := range parsed.Combinations {
		if combination == nil {
			continue
		}
		if combination.Expression != nil {
			*dynamic = true
		}
		values := make(map[string]Value, len(combination.Assigns))
		for key, assign := range combination.Assigns {
			if assign == nil || assign.Key == nil {
				continue
			}
			value := normalizeRawValue(assign.Value)
			if valueContainsExpression(value) {
				*dynamic = true
			}
			values[canonicalID(key)] = value
		}
		keys := make([]string, 0, len(values))
		for key := range values {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		combinations = append(combinations, MatrixCombination{
			Values:     values,
			Keys:       keys,
			Expression: stringValue(combination.Expression),
		})
	}
	return combinations
}

func normalizeActionInputs(inputs map[string]*actionlint.Input) ActionInputMap {
	if len(inputs) == 0 {
		return nil
	}
	values := make(ActionInputMap, len(inputs))
	for key, input := range inputs {
		if input == nil || input.Value == nil {
			continue
		}
		values[key] = input.Value.Value
	}
	if len(values) == 0 {
		return nil
	}
	return values
}

func normalizeRawValues(values []actionlint.RawYAMLValue) []Value {
	result := make([]Value, 0, len(values))
	for _, value := range values {
		result = append(result, normalizeRawValue(value))
	}
	return result
}

func normalizeRawValue(value actionlint.RawYAMLValue) Value {
	switch value := value.(type) {
	case *actionlint.RawYAMLObject:
		object := make(map[string]Value, len(value.Props))
		for key, child := range value.Props {
			object[canonicalID(key)] = normalizeRawValue(child)
		}
		return Value{Kind: ValueKindObject, Object: object}
	case *actionlint.RawYAMLArray:
		items := make([]Value, 0, len(value.Elems))
		for _, child := range value.Elems {
			items = append(items, normalizeRawValue(child))
		}
		return Value{Kind: ValueKindArray, Array: items}
	case *actionlint.RawYAMLString:
		return Value{Kind: ValueKindScalar, Scalar: value.Value}
	default:
		return Value{Kind: ValueKindScalar}
	}
}

func normalizeSteps(parsed *actionlint.Job) []Step {
	if parsed == nil || len(parsed.Steps) == 0 {
		return nil
	}
	steps := make([]Step, 0, len(parsed.Steps))
	for _, parsedStep := range parsed.Steps {
		if parsedStep == nil {
			continue
		}
		step := Step{
			ID:       StepID(stringValue(parsedStep.ID)),
			Name:     stringValue(parsedStep.Name),
			If:       stringValue(parsedStep.If),
			Env:      normalizeEnv(parsedStep.Env),
			Position: positionFrom(parsedStep.Pos),
		}
		switch exec := parsedStep.Exec.(type) {
		case *actionlint.ExecRun:
			step.Kind = StepKindRun
			step.Run = &RunStep{
				Command:          stringValue(exec.Run),
				Shell:            stringValue(exec.Shell),
				WorkingDirectory: stringValue(exec.WorkingDirectory),
			}
		case *actionlint.ExecAction:
			step.Kind = StepKindAction
			step.Action = &ActionStep{
				Uses:       stringValue(exec.Uses),
				Inputs:     normalizeActionInputs(exec.Inputs),
				Entrypoint: stringValue(exec.Entrypoint),
				Args:       stringValue(exec.Args),
			}
		default:
			continue
		}
		steps = append(steps, step)
	}
	return steps
}

func eventPosition(event actionlint.Event) Position {
	switch event := event.(type) {
	case *actionlint.WebhookEvent:
		return positionFrom(event.Pos)
	case *actionlint.ScheduledEvent:
		return positionFrom(event.Pos)
	case *actionlint.WorkflowDispatchEvent:
		return positionFrom(event.Pos)
	case *actionlint.RepositoryDispatchEvent:
		return positionFrom(event.Pos)
	case *actionlint.WorkflowCallEvent:
		return positionFrom(event.Pos)
	case *actionlint.ImageVersionEvent:
		return positionFrom(event.Pos)
	default:
		return Position{}
	}
}

func jobPosition(parsed *actionlint.Job) Position {
	if parsed == nil {
		return Position{}
	}
	if parsed.ID != nil && parsed.ID.Pos != nil {
		return positionFrom(parsed.ID.Pos)
	}
	return positionFrom(parsed.Pos)
}

func positionFrom(pos *actionlint.Pos) Position {
	if pos == nil {
		return Position{}
	}
	return Position{Line: pos.Line, Column: pos.Col}
}

func jobString(parsed *actionlint.Job, getter func(*actionlint.Job) *actionlint.String) *actionlint.String {
	if parsed == nil {
		return nil
	}
	return getter(parsed)
}

func parsedEnv(parsed *actionlint.Job) *actionlint.Env {
	if parsed == nil {
		return nil
	}
	return parsed.Env
}

func stringValue(value *actionlint.String) string {
	if value == nil {
		return ""
	}
	return value.Value
}

func compareJobOrder(left, right *Job) int {
	if left == nil && right == nil {
		return 0
	}
	if left == nil {
		return 1
	}
	if right == nil {
		return -1
	}
	if left.Position.Line != right.Position.Line {
		return left.Position.Line - right.Position.Line
	}
	if left.Position.Column != right.Position.Column {
		return left.Position.Column - right.Position.Column
	}
	return strings.Compare(left.ID.String(), right.ID.String())
}

func canonicalID(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}

func canonicalJobID(value string) JobID {
	return JobID(canonicalID(value))
}
