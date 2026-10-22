import ../../modules/darwin/homebrew-ruby-bootstrap.nix {
  ruby = {
    __toString = _: "/nix/store/test-ruby";
    gemPath = "lib/ruby/gems/4.0.0";
  };
}
