package workerproto

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
)

// Server serves worker protocol requests over one sequential stream.
type Server struct {
	WorkerName string
}

// ServeStdio serves the worker protocol over one reader/writer pair.
func (s Server) ServeStdio(reader io.Reader, writer io.Writer) error {
	workerName := s.WorkerName
	if workerName == "" {
		workerName = "ghawfr-worker"
	}
	dec := json.NewDecoder(reader)
	enc := json.NewEncoder(writer)
	for {
		var request Request
		if err := dec.Decode(&request); err != nil {
			if err == io.EOF {
				return nil
			}
			return fmt.Errorf("decode worker request: %w", err)
		}
		response := Response{ID: request.ID}
		switch request.Method {
		case "hello":
			response.Hello = &HelloResponse{
				Protocol:     ProtocolVersion,
				Worker:       workerName,
				PID:          os.Getpid(),
				Capabilities: []string{"exec"},
			}
		case "exec":
			payload, err := handleExec(request.Exec)
			if err != nil {
				response.Error = err.Error()
			} else {
				response.Exec = payload
			}
		default:
			response.Error = fmt.Sprintf("unsupported worker method %q", request.Method)
		}
		if err := enc.Encode(response); err != nil {
			return fmt.Errorf("encode worker response %q: %w", request.Method, err)
		}
	}
}

func handleExec(request *ExecRequest) (*ExecResponse, error) {
	if request == nil {
		return nil, fmt.Errorf("exec request payload is missing")
	}
	if request.Command == "" {
		return nil, fmt.Errorf("exec command is empty")
	}
	cmd := exec.Command("sh", "-c", request.Command)
	if request.WorkingDirectory != "" {
		cmd.Dir = request.WorkingDirectory
	}
	if len(request.Environment) > 0 {
		env := os.Environ()
		for key, value := range request.Environment {
			env = append(env, key+"="+value)
		}
		cmd.Env = env
	}
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	response := &ExecResponse{Stdout: stdout.String(), Stderr: stderr.String()}
	if err == nil {
		return response, nil
	}
	if exitError, ok := err.(*exec.ExitError); ok {
		response.ExitCode = exitError.ExitCode()
		return response, nil
	}
	return nil, fmt.Errorf("start exec command %q: %w", request.Command, err)
}
