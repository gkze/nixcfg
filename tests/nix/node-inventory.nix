# Tiny package set for checking Nix laziness and per-candidate tryEval behavior.
inventory:
inventory {
  nodejs_18 = throw "unsupported platform";
  nodejs_20 = { };
  nodejs_22.version = "22.19.0";
  nodejs_24.version = 24;
  nodejs_26 = _: "not a package";
  unrelated = throw "unrelated package must stay lazy";
}
