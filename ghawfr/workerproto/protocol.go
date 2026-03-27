package workerproto

// ProtocolVersion is the current worker protocol version.
const ProtocolVersion = "ghawfr-worker-v1"

// Request is one controller-to-worker request frame.
type Request struct {
	ID     uint64        `json:"id"`
	Method string        `json:"method"`
	Hello  *HelloRequest `json:"hello,omitempty"`
	Exec   *ExecRequest  `json:"exec,omitempty"`
}

// Response is one worker-to-controller response frame.
type Response struct {
	ID    uint64         `json:"id"`
	Hello *HelloResponse `json:"hello,omitempty"`
	Exec  *ExecResponse  `json:"exec,omitempty"`
	Error string         `json:"error,omitempty"`
}

// HelloRequest starts protocol negotiation.
type HelloRequest struct {
	Protocol   string `json:"protocol,omitempty"`
	Controller string `json:"controller,omitempty"`
}

// HelloResponse confirms the protocol and worker identity.
type HelloResponse struct {
	Protocol     string   `json:"protocol"`
	Worker       string   `json:"worker"`
	PID          int      `json:"pid"`
	Capabilities []string `json:"capabilities,omitempty"`
}

// ExecRequest asks the worker to execute one shell command.
type ExecRequest struct {
	Command          string            `json:"command"`
	WorkingDirectory string            `json:"working_directory,omitempty"`
	Environment      map[string]string `json:"environment,omitempty"`
}

// ExecResponse is the result of one executed command.
type ExecResponse struct {
	Stdout   string `json:"stdout,omitempty"`
	Stderr   string `json:"stderr,omitempty"`
	ExitCode int    `json:"exit_code"`
}
