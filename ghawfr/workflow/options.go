package workflow

// EnvironmentMap is an open-ended environment-variable namespace.
type EnvironmentMap map[string]string

// Clone returns a copy of the environment map.
func (m EnvironmentMap) Clone() EnvironmentMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(EnvironmentMap, len(m))
	for key, value := range m {
		clone[key] = value
	}
	return clone
}

// VariableMap is an open-ended workflow vars namespace.
type VariableMap map[string]string

// Clone returns a copy of the vars map.
func (m VariableMap) Clone() VariableMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(VariableMap, len(m))
	for key, value := range m {
		clone[key] = value
	}
	return clone
}

// ActionInputMap is an open-ended action with: input namespace.
type ActionInputMap map[string]string

// Clone returns a copy of the action input map.
func (m ActionInputMap) Clone() ActionInputMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(ActionInputMap, len(m))
	for key, value := range m {
		clone[key] = value
	}
	return clone
}

// InputMap is an open-ended input namespace forwarded to the expression adapter.
type InputMap map[string]Data

// Clone returns a deep copy of the input map.
func (m InputMap) Clone() InputMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(InputMap, len(m))
	for key, value := range m {
		clone[key] = value.Clone()
	}
	return clone
}

// GitHubEventMap is the GitHub event payload forwarded to the expression adapter.
type GitHubEventMap map[string]Data

// Clone returns a deep copy of the event payload map.
func (m GitHubEventMap) Clone() GitHubEventMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(GitHubEventMap, len(m))
	for key, value := range m {
		clone[key] = value.Clone()
	}
	return clone
}

// StepContext is the ghawfr-owned subset of one steps.<id> context.
type StepContext struct {
	Outputs    OutputMap
	Outcome    string
	Conclusion string
}

// StepContextMap indexes step context by step identifier.
type StepContextMap map[StepID]StepContext

// Clone returns a deep copy of the step context map.
func (m StepContextMap) Clone() StepContextMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(StepContextMap, len(m))
	for key, value := range m {
		clone[key] = StepContext{
			Outputs:    value.Outputs.Clone(),
			Outcome:    value.Outcome,
			Conclusion: value.Conclusion,
		}
	}
	return clone
}

// SecretMap is an open-ended secret namespace exposed to expression evaluation.
type SecretMap map[string]string

// Clone returns a copy of the secret map.
func (m SecretMap) Clone() SecretMap {
	if len(m) == 0 {
		return nil
	}
	clone := make(SecretMap, len(m))
	for key, value := range m {
		clone[key] = value
	}
	return clone
}

// ParseOptions controls workflow normalization and expansion.
type ParseOptions struct {
	Expressions ExpressionContext
}

// ExpressionContext provides values for expression-driven matrix evaluation.
type ExpressionContext struct {
	GitHub  GitHubContext
	Runner  RunnerContext
	Env     EnvironmentMap
	Vars    VariableMap
	Secrets SecretMap
	Inputs  InputMap
	Needs   NeedContextMap
	Steps   StepContextMap
}

// RunnerContext is the ghawfr-owned subset of the runner expression context.
type RunnerContext struct {
	OS        string
	Arch      string
	Name      string
	Temp      string
	ToolCache string
	Home      string
}

// GitHubContext is the ghawfr-owned subset of the GitHub expression context.
type GitHubContext struct {
	Event      GitHubEventMap
	EventName  string
	Ref        string
	RefName    string
	RefType    string
	Sha        string
	HeadRef    string
	BaseRef    string
	Repository string
	Actor      string
	Workspace  string
}

// NeedContext is the ghawfr-owned subset of one needs.<job> context.
type NeedContext struct {
	Outputs OutputMap
	Result  string
}
