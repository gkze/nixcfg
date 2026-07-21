{
  pkgs,
  src ? ../../..,
}:
pkgs.runCommand "check-test-sops-age-policy" { } ''
  policy_recipients="$(${pkgs.yq-go}/bin/yq -o=json \
    '[.creation_rules[] | select(.path_regex == "secrets\\.yaml$") | .key_groups[].age[]] | sort | unique' \
    ${src}/.sops.yaml)"
  encrypted_recipients="$(${pkgs.yq-go}/bin/yq -o=json \
    '[.sops.age[].recipient] | sort | unique' \
    ${src}/secrets.yaml)"

  test "$policy_recipients" != '[]'
  test "$policy_recipients" = "$encrypted_recipients"
  touch $out
''
