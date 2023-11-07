# GENERATED WITH nix-init - DO NOT EDIT
{ lib
, python3
, fetchPypi
}:

python3.pkgs.buildPythonApplication rec {
  pname = "esbonio";
  version = "0.16.2";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-coJKM3Pyk/X04q0rvWokIBA0TifVsc3/sh+2+jfcWGQ=";
  };

  nativeBuildInputs = [
    python3.pkgs.setuptools
    python3.pkgs.wheel
  ];

  propagatedBuildInputs = with python3.pkgs; [
    platformdirs
    pygls
    pyspellchecker
    sphinx
    typing-extensions
  ];

  passthru.optional-dependencies = with python3.pkgs; {
    dev = [
      black
      flake8
      pre-commit
      tox
    ];
    test = [
      mock
      pytest
      pytest-cov
      pytest-lsp
      pytest-timeout
    ];
    typecheck = [
      mypy
      pytest-lsp
      types-appdirs
      types-docutils
      types-pygments
    ];
  };

  pythonImportsCheck = [ "esbonio" ];

  meta = with lib; {
    description = "A Language Server for Sphinx projects";
    homepage = "https://pypi.org/project/esbonio/";
    license = licenses.mit;
    mainProgram = "esbonio";
  };
}
