# vi: ft=bash
# shellcheck shell=bash

zvm_vi_yank() {
  zvm_yank
  echo "$CUTBUFFER" | pbcopy
  zvm_exit_visual_mode
}
