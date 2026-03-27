package workflow

import (
	"sort"
	"strings"
)

// JobID is one normalized workflow job identifier.
type JobID string

func (id JobID) String() string { return string(id) }

// JobIDs is a deterministic ordered list of job identifiers.
type JobIDs []JobID

// Strings returns the identifiers as plain strings.
func (ids JobIDs) Strings() []string {
	values := make([]string, 0, len(ids))
	for _, id := range ids {
		values = append(values, id.String())
	}
	return values
}

// Join joins the identifiers with the given separator.
func (ids JobIDs) Join(sep string) string {
	return strings.Join(ids.Strings(), sep)
}

// Clone returns a copy of the identifier list.
func (ids JobIDs) Clone() JobIDs {
	return append(JobIDs(nil), ids...)
}

// StepID is one normalized step identifier.
type StepID string

func (id StepID) String() string { return string(id) }

// JobMap indexes executable or logical jobs by job identifier.
type JobMap map[JobID]*Job

// DeferredJobMap indexes deferred logical jobs by logical job identifier.
type DeferredJobMap map[JobID]*DeferredJob

// JobSet tracks completed or otherwise selected jobs by identifier.
type JobSet map[JobID]bool

// OutputMap is an open-ended string output namespace.
type OutputMap map[string]string

// Clone returns a copy of the output map.
func (m OutputMap) Clone() OutputMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(OutputMap, len(m))
	for key, value := range m {
		clone[key] = value
	}
	return clone
}

// NeedContextMap indexes needs context by upstream logical job identifier.
type NeedContextMap map[JobID]NeedContext

// Clone returns a copy of the needs context map.
func (m NeedContextMap) Clone() NeedContextMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(NeedContextMap, len(m))
	for key, value := range m {
		clone[key] = NeedContext{Outputs: value.Outputs.Clone(), Result: value.Result}
	}
	return clone
}

// Position is a 1-based source location.
type Position struct {
	Line   int
	Column int
}

// Diagnostic is one parser diagnostic attached to a source position.
type Diagnostic struct {
	Message    string
	Kind       string
	SourcePath string
	Position   Position
}

// Event is one normalized workflow trigger.
type Event struct {
	Name     string
	Position Position
}

// Workflow is ghawfr's normalized workflow IR.
type Workflow struct {
	Name            string
	RunName         string
	SourcePath      string
	Events          []Event
	Jobs            JobMap
	JobOrder        JobIDs
	LogicalJobs     JobMap
	LogicalJobOrder JobIDs
	DeferredJobs    DeferredJobMap
	DeferredOrder   JobIDs
}

// Job is one normalized executable workflow job instance.
type Job struct {
	ID                JobID
	LogicalID         JobID
	Name              string
	Needs             JobIDs
	If                string
	Env               EnvironmentMap
	RunsOn            Runner
	WorkflowCall      *WorkflowCall
	Strategy          *Strategy
	OutputExpressions OutputMap
	OutputKeys        []string
	MatrixValues      map[string]Value
	MatrixKeys        []string
	Steps             []Step
	Position          Position
}

// DeferredJob is one logical job that cannot yet be materialized into executable instances.
type DeferredJob struct {
	LogicalID JobID
	Needs     JobIDs
	WaitsOn   JobIDs
	Reason    string
	Position  Position
}

// Runner is a normalized runs-on specification.
type Runner struct {
	Labels           []string
	Group            string
	LabelsExpression string
}

// WorkflowCall is a normalized reusable-workflow invocation.
type WorkflowCall struct {
	Uses           string
	InheritSecrets bool
}

// Strategy is a normalized job strategy summary.
type Strategy struct {
	Matrix      *Matrix
	FailFast    *bool
	MaxParallel *int
}

// Matrix is a normalized matrix definition.
type Matrix struct {
	Rows       []MatrixRow
	Include    []MatrixCombination
	Exclude    []MatrixCombination
	Expression string
	Dynamic    bool
}

// MatrixRow is one matrix dimension.
type MatrixRow struct {
	Name       string
	Values     []Value
	Expression string
}

// MatrixCombination is one include/exclude matrix combination.
type MatrixCombination struct {
	Values     map[string]Value
	Keys       []string
	Expression string
}

// ValueKind identifies one matrix value shape.
type ValueKind string

const (
	// ValueKindScalar is a scalar YAML value.
	ValueKindScalar ValueKind = "scalar"
	// ValueKindArray is a sequence YAML value.
	ValueKindArray ValueKind = "array"
	// ValueKindObject is a mapping YAML value.
	ValueKindObject ValueKind = "object"
)

// Value is a typed workflow value tree for matrix data.
type Value struct {
	Kind   ValueKind
	Scalar string
	Array  []Value
	Object map[string]Value
}

// Clone returns a deep copy of the value.
func (v Value) Clone() Value {
	clone := Value{Kind: v.Kind, Scalar: v.Scalar}
	if len(v.Array) > 0 {
		clone.Array = make([]Value, 0, len(v.Array))
		for _, item := range v.Array {
			clone.Array = append(clone.Array, item.Clone())
		}
	}
	if len(v.Object) > 0 {
		clone.Object = make(map[string]Value, len(v.Object))
		for key, value := range v.Object {
			clone.Object[key] = value.Clone()
		}
	}
	return clone
}

// Equal reports whether two values are deeply equal.
func (v Value) Equal(other Value) bool {
	if v.Kind != other.Kind || v.Scalar != other.Scalar {
		return false
	}
	if len(v.Array) != len(other.Array) {
		return false
	}
	for index := range v.Array {
		if !v.Array[index].Equal(other.Array[index]) {
			return false
		}
	}
	if len(v.Object) != len(other.Object) {
		return false
	}
	for key, value := range v.Object {
		otherValue, ok := other.Object[key]
		if !ok || !value.Equal(otherValue) {
			return false
		}
	}
	return true
}

// ObjectKeys returns deterministic object keys.
func (v Value) ObjectKeys() []string {
	keys := make([]string, 0, len(v.Object))
	for key := range v.Object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// MatrixPairs returns ordered key/value pairs for one expanded job instance.
func (j *Job) MatrixPairs() []MatrixPair {
	if j == nil || len(j.MatrixKeys) == 0 {
		return nil
	}
	pairs := make([]MatrixPair, 0, len(j.MatrixKeys))
	for _, key := range j.MatrixKeys {
		pairs = append(pairs, MatrixPair{Key: key, Value: j.MatrixValues[key]})
	}
	return pairs
}

// MatrixPair is one ordered matrix key/value pair.
type MatrixPair struct {
	Key   string
	Value Value
}

// StepKind identifies how one step executes.
type StepKind string

const (
	// StepKindRun is a shell-script step.
	StepKindRun StepKind = "run"
	// StepKindAction is an action step.
	StepKindAction StepKind = "action"
)

// Step is one normalized workflow step.
type Step struct {
	ID       StepID
	Name     string
	If       string
	Env      EnvironmentMap
	Kind     StepKind
	Run      *RunStep
	Action   *ActionStep
	Position Position
}

// RunStep is a normalized shell-script step.
type RunStep struct {
	Command          string
	Shell            string
	WorkingDirectory string
}

// ActionStep is a normalized action step.
type ActionStep struct {
	Uses       string
	Inputs     ActionInputMap
	Entrypoint string
	Args       string
}
