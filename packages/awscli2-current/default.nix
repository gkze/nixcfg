{
  awscli2,
  fetchFromGitHub,
  fetchurl,
  lib,
  outputs,
  python3,
  selfSource,
  testers,
  ...
}:
assert lib.versionAtLeast selfSource.version awscli2.version;
let
  pname = "awscli2-current";
  dependencySource =
    name: version:
    let
      url = "https://files.pythonhosted.org/packages/source/${builtins.substring 0 1 name}/${name}/${name}-${version}.tar.gz";
      entry = outputs.lib.selectSourceHashEntry selfSource.hashes "sha256" { inherit url; };
    in
    assert entry != null;
    fetchurl {
      inherit url;
      inherit (entry) hash;
    };
  localstub = python3.pkgs.buildPythonPackage {
    pname = "localstub";
    version = selfSource.pins.localstubVersion;
    pyproject = true;
    src = dependencySource "localstub" selfSource.pins.localstubVersion;
    build-system = [ python3.pkgs.hatchling ];
    dependencies = with python3.pkgs; [
      anyio
      cryptography
      httptools
      rich
      trustme
      truststore
    ];
    pythonImportsCheck = [
      "localstub.server"
      "localstub.tlsproxy"
    ];
  };
  awsPython = python3 // {
    pkgs = python3.pkgs.overrideScope (
      _final: prev: {
        awscrt = prev.awscrt.overridePythonAttrs {
          version = selfSource.pins.awscrtVersion;
          src = dependencySource "awscrt" selfSource.pins.awscrtVersion;
        };
      }
    );
  };
  package = (awscli2.override { python3 = awsPython; }).overridePythonAttrs (old: {
    inherit (selfSource) version;
    src = fetchFromGitHub {
      owner = "aws";
      repo = "aws-cli";
      rev = selfSource.commit;
      hash = outputs.lib.sourceHash pname "srcHash";
    };
    # Keep the blackbox subprocess timezone aligned and arm transfer faults in
    # the matching request handler, so they cannot close the preceding response.
    patches = (old.patches or [ ]) ++ [ ./blackbox-fixtures.patch ];
    # Retain the maintained recipe's dependency policy, full unit checks,
    # credential isolation, pager dependencies and shell completions.
    nativeCheckInputs = (old.nativeCheckInputs or [ ]) ++ [
      localstub
      python3.pkgs.pytest-asyncio
    ];
    preCheck = (old.preCheck or "") + ''
      export AWS_TEST_COMMAND="$out/bin/aws"
    '';
    postInstallCheck = (old.postInstallCheck or "") + ''
      "$out/bin/aws" --version | grep -F "aws-cli/${selfSource.version} "
      AWS_EC2_METADATA_DISABLED=true "$out/bin/aws" help > /dev/null
      test -x "$out/bin/aws_completer"
      test -f "$out/share/bash-completion/completions/aws.bash"
      test -f "$out/share/zsh/site-functions/_aws"
    '';
    meta = old.meta // {
      changelog = "https://github.com/aws/aws-cli/blob/${selfSource.commit}/CHANGELOG.rst";
      platforms = [ "aarch64-darwin" ];
    };
    passthru = old.passthru // {
      tests = old.passthru.tests // {
        version = testers.testVersion {
          inherit package;
          command = "aws --version";
          inherit (selfSource) version;
        };
      };
    };
  });
in
package
