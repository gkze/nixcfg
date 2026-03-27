package workerproto

import (
	"encoding/json"
	"fmt"
	"io"
	"sync"
)

// Client is a sequential request/response client for the worker protocol.
type Client struct {
	enc    *json.Encoder
	dec    *json.Decoder
	closer io.Closer
	mu     sync.Mutex
	nextID uint64
}

// NewClient creates one sequential worker protocol client.
func NewClient(reader io.Reader, writer io.Writer, closer io.Closer) *Client {
	return &Client{
		enc:    json.NewEncoder(writer),
		dec:    json.NewDecoder(reader),
		closer: closer,
	}
}

// Close closes the underlying transport if one was provided.
func (c *Client) Close() error {
	if c == nil || c.closer == nil {
		return nil
	}
	return c.closer.Close()
}

// Hello negotiates the worker protocol and returns worker metadata.
func (c *Client) Hello(controller string) (HelloResponse, error) {
	response, err := c.exchange(Request{
		Method: "hello",
		Hello: &HelloRequest{
			Protocol:   ProtocolVersion,
			Controller: controller,
		},
	})
	if err != nil {
		return HelloResponse{}, err
	}
	if response.Hello == nil {
		return HelloResponse{}, fmt.Errorf("hello response is missing payload")
	}
	return *response.Hello, nil
}

// Exec asks the worker to execute one shell command.
func (c *Client) Exec(command string, workingDirectory string, environment map[string]string) (ExecResponse, error) {
	response, err := c.exchange(Request{
		Method: "exec",
		Exec: &ExecRequest{
			Command:          command,
			WorkingDirectory: workingDirectory,
			Environment:      cloneEnvironment(environment),
		},
	})
	if err != nil {
		return ExecResponse{}, err
	}
	if response.Exec == nil {
		return ExecResponse{}, fmt.Errorf("exec response is missing payload")
	}
	return *response.Exec, nil
}

func (c *Client) exchange(request Request) (Response, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.nextID++
	request.ID = c.nextID
	if err := c.enc.Encode(request); err != nil {
		return Response{}, fmt.Errorf("encode worker request %q: %w", request.Method, err)
	}
	var response Response
	if err := c.dec.Decode(&response); err != nil {
		return Response{}, fmt.Errorf("decode worker response %q: %w", request.Method, err)
	}
	if response.ID != request.ID {
		return Response{}, fmt.Errorf("worker response id = %d, want %d", response.ID, request.ID)
	}
	if response.Error != "" {
		return Response{}, fmt.Errorf("worker %q request failed: %s", request.Method, response.Error)
	}
	return response, nil
}

func cloneEnvironment(environment map[string]string) map[string]string {
	if len(environment) == 0 {
		return nil
	}
	cloned := make(map[string]string, len(environment))
	for key, value := range environment {
		cloned[key] = value
	}
	return cloned
}
