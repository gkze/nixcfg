# Constructors declare their platform without evaluating a module graph.
# Already evaluated configurations retain the framework's platform lookup.
configuration:
if configuration ? config.system.build.toplevel then
  configuration.pkgs.stdenv.hostPlatform.system
else
  configuration.system
