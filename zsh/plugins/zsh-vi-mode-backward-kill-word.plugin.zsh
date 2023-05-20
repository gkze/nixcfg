# vi: ft=bash
# shellcheck shell=bash

# Set the default WORDCHARS
WORDCHARS='*?_-.[]~=&;!#$%^(){}<>'

# Your custom widget for removing the entire path
function granular-backward-kill-word() {
  # Save the original WORDCHARS
  local wordchars=$WORDCHARS

  # Change the WORDCHARS
  WORDCHARS='/'

  # Call the backward-kill-word widget
  zle backward-kill-word

  # Restore the WORDCHARS
  WORDCHARS=$wordchars
}

# The plugin will auto execute this zvm_after_init function
function zvm_after_init() {
  # Here we define the custom widget
  zvm_define_widget granular-backward-kill-word

  # In viins mode,  quickly press Ctrl-W to invoke this widget
  zvm_bindkey viins '^W' granular-backward-kill-word
}
