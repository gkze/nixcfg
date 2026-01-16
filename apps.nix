{
  update-vscode-insiders =
    { python3, ... }: "${python3}/bin/python3 ${./scripts/update_vscode_insiders.py}";
}
