package workflow

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
)

func expandWorkflow(logical *Workflow, options ParseOptions) (*Workflow, []Diagnostic) {
	expanded := &Workflow{
		Name:            logical.Name,
		RunName:         logical.RunName,
		SourcePath:      logical.SourcePath,
		Events:          append([]Event(nil), logical.Events...),
		Jobs:            make(JobMap),
		LogicalJobs:     cloneJobMap(logical.Jobs),
		LogicalJobOrder: logical.JobOrder.Clone(),
		DeferredJobs:    make(DeferredJobMap),
	}

	instancesByLogicalID := make(map[JobID][]*Job, len(logical.Jobs))
	for _, logicalID := range logical.JobOrder {
		logicalJob := logical.Jobs[logicalID]
		if logicalJob == nil {
			continue
		}
		instances, deferred, err := expandJobInstances(logicalJob, options)
		if err != nil {
			return nil, []Diagnostic{matrixDiagnostic(logical.SourcePath, logicalJob, err)}
		}
		instancesByLogicalID[logicalID] = instances
		if deferred != nil {
			expanded.DeferredJobs[logicalID] = deferred
		}
	}

	for {
		changed := false
		for _, logicalID := range logical.JobOrder {
			logicalJob := logical.Jobs[logicalID]
			if logicalJob == nil || len(instancesByLogicalID[logicalID]) == 0 {
				continue
			}
			waitsOn := unresolvedNeeds(logical, instancesByLogicalID, logicalJob.Needs)
			if len(waitsOn) == 0 {
				continue
			}
			instancesByLogicalID[logicalID] = nil
			expanded.DeferredJobs[logicalID] = &DeferredJob{
				LogicalID: logicalID,
				Needs:     logicalJob.Needs.Clone(),
				WaitsOn:   waitsOn,
				Reason:    "waiting for deferred dependencies to materialize",
				Position:  logicalJob.Position,
			}
			changed = true
		}
		if !changed {
			break
		}
	}

	for _, logicalID := range logical.JobOrder {
		logicalJob := logical.Jobs[logicalID]
		if logicalJob == nil {
			continue
		}
		if deferred, ok := expanded.DeferredJobs[logicalID]; ok {
			expanded.DeferredOrder = append(expanded.DeferredOrder, deferred.LogicalID)
			continue
		}
		rewrittenNeeds := expandNeeds(instancesByLogicalID, logicalJob.Needs)
		for _, instance := range instancesByLogicalID[logicalID] {
			instance.Needs = rewrittenNeeds.Clone()
			expanded.Jobs[instance.ID] = instance
			expanded.JobOrder = append(expanded.JobOrder, instance.ID)
		}
	}

	return expanded, nil
}

func expandJobInstances(job *Job, options ParseOptions) ([]*Job, *DeferredJob, error) {
	if job == nil {
		return nil, nil, nil
	}
	if job.LogicalID == "" {
		job.LogicalID = job.ID
	}
	workingJob := job
	if job.Strategy != nil && job.Strategy.Matrix != nil && job.Strategy.Matrix.Dynamic {
		resolved, err := resolveDynamicMatrix(job, options.Expressions)
		if err != nil {
			if isDeferrableMatrixError(err) {
				return nil, &DeferredJob{
					LogicalID: job.LogicalID,
					Needs:     job.Needs.Clone(),
					WaitsOn:   job.Needs.Clone(),
					Reason:    err.Error(),
					Position:  job.Position,
				}, nil
			}
			return nil, nil, err
		}
		workingJob = cloneJob(job)
		workingJob.Strategy = cloneStrategy(job.Strategy)
		workingJob.Strategy.Matrix = resolved
	}
	if workingJob.Strategy == nil || workingJob.Strategy.Matrix == nil || workingJob.Strategy.Matrix.Dynamic {
		instance := cloneJob(workingJob)
		instance.ID = workingJob.LogicalID
		instance.LogicalID = workingJob.LogicalID
		instance.MatrixValues = nil
		instance.MatrixKeys = nil
		resolvedRunner, err := ResolveRunner(instance, options.Expressions)
		if err != nil {
			return nil, nil, fmt.Errorf("resolve runner for %q: %w", instance.ID, err)
		}
		instance.RunsOn = resolvedRunner
		return []*Job{instance}, nil, nil
	}

	combinations, err := expandMatrix(workingJob.Strategy.Matrix)
	if err != nil {
		return nil, nil, fmt.Errorf("expand matrix for %q: %w", job.LogicalID, err)
	}
	if len(combinations) == 1 && len(combinations[0]) == 0 {
		instance := cloneJob(workingJob)
		instance.ID = workingJob.LogicalID
		instance.LogicalID = workingJob.LogicalID
		instance.MatrixValues = nil
		instance.MatrixKeys = nil
		resolvedRunner, err := ResolveRunner(instance, options.Expressions)
		if err != nil {
			return nil, nil, fmt.Errorf("resolve runner for %q: %w", instance.ID, err)
		}
		instance.RunsOn = resolvedRunner
		return []*Job{instance}, nil, nil
	}

	instances := make([]*Job, 0, len(combinations))
	seen := make(map[JobID]int, len(combinations))
	for index, combination := range combinations {
		instance := cloneJob(workingJob)
		instance.LogicalID = workingJob.LogicalID
		instance.MatrixValues = cloneMatrixValues(combination)
		instance.MatrixKeys = sortedMatrixKeys(combination)
		instance.ID = buildMatrixJobID(workingJob.LogicalID, instance.MatrixValues, instance.MatrixKeys, index)
		resolvedRunner, err := ResolveRunner(instance, options.Expressions)
		if err != nil {
			return nil, nil, fmt.Errorf("resolve runner for %q: %w", instance.ID, err)
		}
		instance.RunsOn = resolvedRunner
		seen[instance.ID]++
		if seen[instance.ID] > 1 {
			instance.ID = JobID(fmt.Sprintf("%s#%d", instance.ID, seen[instance.ID]))
		}
		instance.Name = renderMatrixString(instance.Name, instance.MatrixValues)
		instances = append(instances, instance)
	}
	return instances, nil, nil
}

func unresolvedNeeds(definition *Workflow, instancesByLogicalID map[JobID][]*Job, needs JobIDs) JobIDs {
	if len(needs) == 0 {
		return nil
	}
	waitsOn := make(JobIDs, 0, len(needs))
	for _, dependency := range needs {
		if _, ok := definition.Jobs[dependency]; !ok {
			continue
		}
		if len(instancesByLogicalID[dependency]) > 0 {
			continue
		}
		waitsOn = append(waitsOn, dependency)
	}
	return waitsOn
}

func expandNeeds(instancesByLogicalID map[JobID][]*Job, needs JobIDs) JobIDs {
	if len(needs) == 0 {
		return nil
	}
	expanded := make(JobIDs, 0, len(needs))
	seen := make(map[JobID]struct{})
	for _, dependency := range needs {
		instances, ok := instancesByLogicalID[dependency]
		if !ok || len(instances) == 0 {
			if _, ok := seen[dependency]; ok {
				continue
			}
			seen[dependency] = struct{}{}
			expanded = append(expanded, dependency)
			continue
		}
		for _, instance := range instances {
			if _, ok := seen[instance.ID]; ok {
				continue
			}
			seen[instance.ID] = struct{}{}
			expanded = append(expanded, instance.ID)
		}
	}
	return expanded
}

func expandMatrix(matrix *Matrix) ([]map[string]Value, error) {
	if matrix == nil || matrix.Dynamic {
		return []map[string]Value{{}}, nil
	}
	rows := make(map[string][]Value, len(matrix.Rows))
	for _, row := range matrix.Rows {
		rows[row.Name] = cloneValues(row.Values)
	}
	for _, exclude := range matrix.Exclude {
		for _, key := range exclude.Keys {
			if _, ok := rows[key]; ok {
				continue
			}
			return nil, fmt.Errorf("exclude key %q does not match any declared matrix row", key)
		}
	}

	combinations := cartesianMatrix(rows)
	filtered := make([]map[string]Value, 0, len(combinations))
CombinationLoop:
	for _, combination := range combinations {
		for _, exclude := range matrix.Exclude {
			if combinationMatches(combination, exclude.Values) {
				continue CombinationLoop
			}
		}
		filtered = append(filtered, combination)
	}

	extra := make([]map[string]Value, 0)
	for _, include := range matrix.Include {
		matched := false
		for _, combination := range filtered {
			if includeMatches(combination, include.Values, rows) {
				matched = true
				for _, key := range include.Keys {
					combination[key] = include.Values[key].Clone()
				}
			}
		}
		if !matched {
			extra = append(extra, cloneMatrixValues(include.Values))
		}
	}

	result := append(filtered, extra...)
	if len(result) == 0 {
		return []map[string]Value{{}}, nil
	}
	return result, nil
}

func cartesianMatrix(rows map[string][]Value) []map[string]Value {
	keys := make([]string, 0, len(rows))
	for key := range rows {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	if len(keys) == 0 {
		return nil
	}
	result := []map[string]Value{{}}
	result[0] = make(map[string]Value)
	for _, key := range keys {
		values := rows[key]
		if len(values) == 0 {
			return nil
		}
		next := make([]map[string]Value, 0, len(result)*len(values))
		for _, combination := range result {
			for _, value := range values {
				clone := cloneMatrixValues(combination)
				clone[key] = value.Clone()
				next = append(next, clone)
			}
		}
		result = next
	}
	return result
}

func combinationMatches(left map[string]Value, right map[string]Value) bool {
	for key, rightValue := range right {
		leftValue, ok := left[key]
		if !ok || !leftValue.Equal(rightValue) {
			return false
		}
	}
	return true
}

func includeMatches(base map[string]Value, include map[string]Value, rows map[string][]Value) bool {
	for key, baseValue := range base {
		if _, ok := rows[key]; !ok {
			continue
		}
		includeValue, ok := include[key]
		if ok && !baseValue.Equal(includeValue) {
			return false
		}
	}
	return true
}

func cloneJob(job *Job) *Job {
	if job == nil {
		return nil
	}
	clone := *job
	clone.Needs = job.Needs.Clone()
	clone.Env = job.Env.Clone()
	clone.OutputExpressions = job.OutputExpressions.Clone()
	clone.OutputKeys = append([]string(nil), job.OutputKeys...)
	clone.MatrixKeys = append([]string(nil), job.MatrixKeys...)
	clone.Strategy = cloneStrategy(job.Strategy)
	if len(job.MatrixValues) > 0 {
		clone.MatrixValues = cloneMatrixValues(job.MatrixValues)
	}
	if len(job.Steps) > 0 {
		clone.Steps = cloneSteps(job.Steps)
	}
	return &clone
}

func cloneJobMap(jobs JobMap) JobMap {
	if len(jobs) == 0 {
		return nil
	}
	clone := make(JobMap, len(jobs))
	for key, job := range jobs {
		clone[key] = cloneJob(job)
	}
	return clone
}

func cloneStrategy(strategy *Strategy) *Strategy {
	if strategy == nil {
		return nil
	}
	clone := &Strategy{}
	if strategy.FailFast != nil {
		value := *strategy.FailFast
		clone.FailFast = &value
	}
	if strategy.MaxParallel != nil {
		value := *strategy.MaxParallel
		clone.MaxParallel = &value
	}
	if strategy.Matrix != nil {
		clone.Matrix = cloneMatrix(strategy.Matrix)
	}
	return clone
}

func cloneMatrix(matrix *Matrix) *Matrix {
	if matrix == nil {
		return nil
	}
	clone := &Matrix{
		Expression: matrix.Expression,
		Dynamic:    matrix.Dynamic,
	}
	if len(matrix.Rows) > 0 {
		clone.Rows = make([]MatrixRow, 0, len(matrix.Rows))
		for _, row := range matrix.Rows {
			clone.Rows = append(clone.Rows, MatrixRow{
				Name:       row.Name,
				Values:     cloneValues(row.Values),
				Expression: row.Expression,
			})
		}
	}
	if len(matrix.Include) > 0 {
		clone.Include = cloneMatrixCombinations(matrix.Include)
	}
	if len(matrix.Exclude) > 0 {
		clone.Exclude = cloneMatrixCombinations(matrix.Exclude)
	}
	return clone
}

func cloneSteps(steps []Step) []Step {
	clone := make([]Step, 0, len(steps))
	for _, step := range steps {
		copy := step
		copy.Env = step.Env.Clone()
		if step.Run != nil {
			run := *step.Run
			copy.Run = &run
		}
		if step.Action != nil {
			action := *step.Action
			action.Inputs = step.Action.Inputs.Clone()
			copy.Action = &action
		}
		clone = append(clone, copy)
	}
	return clone
}

func cloneMatrixCombinations(values []MatrixCombination) []MatrixCombination {
	clone := make([]MatrixCombination, 0, len(values))
	for _, value := range values {
		clone = append(clone, MatrixCombination{
			Values:     cloneMatrixValues(value.Values),
			Keys:       append([]string(nil), value.Keys...),
			Expression: value.Expression,
		})
	}
	return clone
}

func cloneValues(values []Value) []Value {
	clones := make([]Value, 0, len(values))
	for _, value := range values {
		clones = append(clones, value.Clone())
	}
	return clones
}

func cloneMatrixValues(values map[string]Value) map[string]Value {
	if values == nil {
		return nil
	}
	clone := make(map[string]Value, len(values))
	for key, value := range values {
		clone[key] = value.Clone()
	}
	return clone
}

func sortedMatrixKeys(values map[string]Value) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func buildMatrixJobID(logicalID JobID, values map[string]Value, keys []string, ordinal int) JobID {
	suffix := matrixIdentitySuffix(values, keys, ordinal)
	if suffix == "" {
		return logicalID
	}
	return JobID(fmt.Sprintf("%s[%s]", logicalID, suffix))
}

func matrixIdentitySuffix(values map[string]Value, keys []string, ordinal int) string {
	if len(values) == 0 {
		return strconv.Itoa(ordinal + 1)
	}
	if name, ok := values["name"]; ok && name.Kind == ValueKindScalar && name.Scalar != "" {
		return "name=" + shortIdentityValue(name)
	}
	parts := make([]string, 0, len(keys))
	total := 0
	for _, key := range keys {
		part := key + "=" + shortIdentityValue(values[key])
		parts = append(parts, part)
		total += len(part)
	}
	if total > 96 {
		return strconv.Itoa(ordinal + 1)
	}
	return strings.Join(parts, ",")
}

func shortIdentityValue(value Value) string {
	text := value.IdentifierString()
	if len(text) <= 48 {
		return text
	}
	return text[:45] + "..."
}

// IdentifierString renders the value in a deterministic compact form.
func (v Value) IdentifierString() string {
	switch v.Kind {
	case ValueKindArray:
		parts := make([]string, 0, len(v.Array))
		for _, item := range v.Array {
			parts = append(parts, item.IdentifierString())
		}
		return "[" + strings.Join(parts, ",") + "]"
	case ValueKindObject:
		parts := make([]string, 0, len(v.Object))
		for _, key := range v.ObjectKeys() {
			parts = append(parts, key+":"+v.Object[key].IdentifierString())
		}
		return "{" + strings.Join(parts, ",") + "}"
	default:
		return quoteIfNeeded(v.Scalar)
	}
}

func quoteIfNeeded(value string) string {
	if value == "" {
		return strconv.Quote(value)
	}
	for _, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || strings.ContainsRune("._/-", r) {
			continue
		}
		return strconv.Quote(value)
	}
	return value
}

func isDeferrableMatrixError(err error) bool {
	if err == nil {
		return false
	}
	message := err.Error()
	return strings.Contains(message, "Unavailable context:") ||
		strings.Contains(message, "Cannot parse non-string type invalid as JSON")
}

func renderMatrixString(input string, values map[string]Value) string {
	if input == "" || len(values) == 0 {
		return input
	}
	result := input
	for key, value := range values {
		placeholder := "${{ matrix." + key + " }}"
		result = strings.ReplaceAll(result, placeholder, value.IdentifierString())
	}
	return result
}

func matrixDiagnostic(sourcePath string, job *Job, err error) Diagnostic {
	position := Position{}
	var logicalID JobID
	if job != nil {
		position = job.Position
		logicalID = job.LogicalID
		if logicalID == "" {
			logicalID = job.ID
		}
	}
	message := err.Error()
	if logicalID != "" {
		message = fmt.Sprintf("job %q: %s", logicalID, err.Error())
	}
	return Diagnostic{
		Message:    message,
		Kind:       "matrix-expansion",
		SourcePath: sourcePath,
		Position:   position,
	}
}
