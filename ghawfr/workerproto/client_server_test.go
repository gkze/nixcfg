package workerproto

import (
	"io"
	"strings"
	"testing"
)

func TestClientServerHelloAndExec(t *testing.T) {
	serverReader, clientWriter := io.Pipe()
	clientReader, serverWriter := io.Pipe()
	defer func() {
		_ = clientWriter.Close()
		_ = clientReader.Close()
		_ = serverReader.Close()
		_ = serverWriter.Close()
	}()

	errCh := make(chan error, 1)
	go func() {
		errCh <- (Server{WorkerName: "test-worker"}).ServeStdio(serverReader, serverWriter)
	}()

	client := NewClient(clientReader, clientWriter, clientWriter)
	hello, err := client.Hello("test-controller")
	if err != nil {
		t.Fatalf("Hello: %v", err)
	}
	if hello.Protocol != ProtocolVersion || hello.Worker != "test-worker" {
		t.Fatalf("hello = %#v, want protocol=%q worker=test-worker", hello, ProtocolVersion)
	}
	result, err := client.Exec("printf hello", "", nil)
	if err != nil {
		t.Fatalf("Exec: %v", err)
	}
	if got, want := result.Stdout, "hello"; got != want {
		t.Fatalf("result.Stdout = %q, want %q", got, want)
	}
	if result.ExitCode != 0 {
		t.Fatalf("result.ExitCode = %d, want 0", result.ExitCode)
	}
	_ = client.Close()
	if err := <-errCh; err != nil {
		t.Fatalf("ServeStdio: %v", err)
	}
}

func TestClientServerExecReturnsExitCodeAndStderr(t *testing.T) {
	serverReader, clientWriter := io.Pipe()
	clientReader, serverWriter := io.Pipe()
	defer func() {
		_ = clientWriter.Close()
		_ = clientReader.Close()
		_ = serverReader.Close()
		_ = serverWriter.Close()
	}()
	go func() {
		_ = (Server{}).ServeStdio(serverReader, serverWriter)
	}()
	client := NewClient(clientReader, clientWriter, clientWriter)
	result, err := client.Exec("echo bad >&2; exit 7", "", nil)
	if err != nil {
		t.Fatalf("Exec: %v", err)
	}
	if got, want := result.ExitCode, 7; got != want {
		t.Fatalf("result.ExitCode = %d, want %d", got, want)
	}
	if !strings.Contains(result.Stderr, "bad") {
		t.Fatalf("result.Stderr = %q, want substring bad", result.Stderr)
	}
}
