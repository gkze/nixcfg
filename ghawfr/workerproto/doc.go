// Package workerproto defines the ghawfr guest-worker protocol used between the
// controller and one guest-side worker process.
//
// The protocol is intentionally ghawfr-owned and transport-agnostic. The first
// transport is stdio over an SSH-launched guest worker, but the same message
// model is intended to move to vsock or other transports later.
package workerproto
