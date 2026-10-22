{ ruby }:
''
  # nixcfg: use the Nix-provided Ruby while preserving Homebrew's setup-ruby-path
  # bookkeeping (GEM_HOME, BUNDLE_GEMFILE, bootsnap path, etc.).  Nix's Ruby 4.0
  # ships fiddle as a bundled gem, so keep gems enabled. The final Ruby
  # exec preloads it before Homebrew Bundler restricts the gem set.
  export HOMEBREW_RUBY_DISABLE_OPTIONS="--disable=rubyopt"
  # Keep bootstrap discovery in this Ruby's gem directory before Bundler narrows it.
  export GEM_HOME="${ruby}/${ruby.gemPath}"
  export GEM_PATH="$GEM_HOME"
  system_ruby_supported() { return 0; }
  find_ruby() { echo "${ruby}/bin/ruby"; }
''
