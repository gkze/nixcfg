# Interactive toolchain ownership

George's Darwin profile selects `go-toolchain`, `uv-cli`, and `python-current`
through the existing Home Manager program/language options. Project-specific
runtime pins and the interpreters used inside other Nix derivations remain
independent. Other operating systems retain their existing selections.

The source owners discover stable releases from the publishers. Go and Python
source downloads must match their publishers' SHA-256 values. UV uses the
release's exact platform archives; Darwin binaries retain their signatures.

Go reuses the maintained `go_1_27` recipe, including its bootstrap and platform
patches. Python reuses `python314`, preserving its package-set interface for
`withPackages`, `ptpython`, and `catppuccin`. Both reject a new version family
until its corresponding Nix recipe is selected and validated.

`python-current` is a native interpreter. Its native packaging helpers use the
same interpreter release: upstream's generic splice propagation omits the
attribute-set `sourceVersion` override.

The interactive Python series is selected by `python-current/compatibility.json`.
Parso/Jedi currently support grammar through Python 3.14; current upstream
Parso still reports valid Python 3.15 unpacking comprehensions as syntax errors.
Use the latest 3.14 patch with the configured REPL until that support is reviewed.
The separately installed Python 3.15.1 remains available for project use.

Validation must include the configured Python environment, not only the bare
interpreter. That exercises the packaging helpers and the actual REPL packages.
