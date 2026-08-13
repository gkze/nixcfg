{
  bundleName,
  executableName,
  binaryName,
}:
''
  executable="$out/Applications/${bundleName}/Contents/MacOS/${executableName}"
  if [ ! -x "$executable" ]; then
    echo "Expected executable ${executableName} in ${bundleName}" >&2
    executable_dir="$(dirname "$executable")"
    if [ -d "$executable_dir" ]; then
      find "$executable_dir" -maxdepth 1 -type f >&2
    fi
    exit 1
  fi
  ln -s "$executable" "$out/bin/${binaryName}"
''
