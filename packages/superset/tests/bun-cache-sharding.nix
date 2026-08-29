{ package }:
let
  stats = package.bunDeps.passthru.nixcfg;
in
assert stats.packageCount > 3000;
assert stats.shardCount <= 256;
assert stats.shardCount < stats.packageCount;
assert stats.maxShardSize <= 32;
assert stats.minShardSize > 0;
true
