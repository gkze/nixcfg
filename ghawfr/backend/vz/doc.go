// Package vz provides a Virtualization.framework-backed worker provider for
// Apple Silicon Linux and macOS guests.
//
// The implementation is intentionally built around github.com/Code-Hex/vz/v3
// rather than custom ObjC/Swift bindings so ghawfr can stay Go-native while
// reusing a mature public Virtualization.framework wrapper.
package vz
