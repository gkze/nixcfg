#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 MUTABLE_CACHE SOURCE_CACHE ALLOWED_SOURCE_ROOT PYTHON" >&2
  exit 2
fi

mutableCache="$1"
sourceCache="$2"
allowedSourceRoot="$3"
python="$4"

realpath_with_python() {
  "$python" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

mutableCache="$(realpath_with_python "$mutableCache")"
sourceCache="$(realpath_with_python "$sourceCache")"
allowedSourceRoot="$(realpath_with_python "$allowedSourceRoot")"

materialize_bun_cache_package() {
  expectedName="$1"
  expectedVersion="$2"
  shift 2

  case "$expectedName" in
  @*/*)
    searchRoot="$mutableCache/${expectedName%%/*}"
    cacheName="${expectedName#*/}"
    ;;
  *)
    searchRoot="$mutableCache"
    cacheName="$expectedName"
    ;;
  esac

  if [ ! -d "$searchRoot" ] || [ -L "$searchRoot" ]; then
    echo "expected a local Bun cache directory: $searchRoot" >&2
    return 1
  fi
  chmod u+w "$searchRoot"

  shopt -s nullglob
  candidates=("$searchRoot/$cacheName"@*)
  shopt -u nullglob
  matches=()
  for candidate in "${candidates[@]}"; do
    [ -f "$candidate/package.json" ] || continue
    identity="$("$python" -c 'import json, sys; package = json.load(open(sys.argv[1], encoding="utf-8")); print(package["name"] + "@" + package["version"])' "$candidate/package.json")"
    if [ "$identity" = "$expectedName@$expectedVersion" ]; then
      matches+=("$candidate")
    fi
  done

  if [ "${#matches[@]}" -ne 1 ]; then
    echo "expected exactly one Bun cache entry for $expectedName@$expectedVersion, found ${#matches[@]}" >&2
    return 1
  fi

  cacheEntry="${matches[0]}"
  relativeEntry="${cacheEntry#"$mutableCache"/}"
  sourceEntry="$sourceCache/$relativeEntry"
  if [ ! -L "$cacheEntry" ] || [ ! -L "$sourceEntry" ]; then
    echo "expected immutable Bun cache symlinks for $expectedName@$expectedVersion" >&2
    return 1
  fi

  storeTarget="$(realpath_with_python "$cacheEntry")"
  sourceTarget="$(realpath_with_python "$sourceEntry")"
  case "$storeTarget" in
  "$allowedSourceRoot"/*) ;;
  *)
    echo "unexpected Bun cache target outside $allowedSourceRoot: $storeTarget" >&2
    return 1
    ;;
  esac
  if [ "$sourceTarget" != "$storeTarget" ]; then
    echo "temporary Bun cache entry diverged from its immutable source" >&2
    return 1
  fi

  for expectedFile in "$@"; do
    if [ ! -f "$cacheEntry/$expectedFile" ]; then
      echo "missing $expectedFile in $expectedName@$expectedVersion" >&2
      return 1
    fi
  done

  staging="$(mktemp -d "${cacheEntry}.executor-writable.XXXXXX")"
  cp -R -L "$cacheEntry"/. "$staging"/
  chmod -R u+rwX "$staging"

  copiedIdentity="$("$python" -c 'import json, sys; package = json.load(open(sys.argv[1], encoding="utf-8")); print(package["name"] + "@" + package["version"])' "$staging/package.json")"
  if [ "$copiedIdentity" != "$expectedName@$expectedVersion" ]; then
    echo "copied Bun package identity changed: $copiedIdentity" >&2
    return 1
  fi
  for expectedFile in "$@"; do
    if [ ! -w "$staging/$expectedFile" ]; then
      echo "copied Bun package file is not writable: $expectedFile" >&2
      return 1
    fi
  done

  backup="${cacheEntry}.executor-store-link"
  if [ -e "$backup" ] || [ -L "$backup" ]; then
    echo "refusing to replace existing Bun cache backup: $backup" >&2
    return 1
  fi
  mv "$cacheEntry" "$backup"
  if ! mv "$staging" "$cacheEntry"; then
    mv "$backup" "$cacheEntry"
    return 1
  fi
  rm "$backup"

  materializedTarget="$(realpath_with_python "$cacheEntry")"
  case "$materializedTarget" in
  "$mutableCache"/*) ;;
  *)
    echo "materialized Bun cache entry escaped its temporary cache" >&2
    return 1
    ;;
  esac
  if [ ! -d "$cacheEntry" ] || [ -L "$cacheEntry" ]; then
    echo "failed to materialize Bun cache entry: $cacheEntry" >&2
    return 1
  fi
  if [ ! -L "$sourceEntry" ] || [ "$(realpath_with_python "$sourceEntry")" != "$storeTarget" ]; then
    echo "immutable Bun cache source changed during materialization" >&2
    return 1
  fi
}

materialize_bun_cache_package \
  typescript \
  5.9.3 \
  lib/typescript.js \
  lib/_tsc.js
materialize_bun_cache_package \
  @typescript/native-preview-darwin-arm64 \
  7.0.0-dev.20260415.1 \
  lib/tsgo
