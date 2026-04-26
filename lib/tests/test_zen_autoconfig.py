"""Regression checks for Twilight AutoConfig glue used by the Zen theme."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from lib.update.paths import REPO_ROOT

_NODE = shutil.which("node")


def _run_node_json(script: str, source_path: Path) -> object:
    assert _NODE is not None
    result = subprocess.run(  # noqa: S603
        [_NODE, "-e", script, str(source_path)],
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(_NODE is None, reason="node command not available")
def test_twilight_autoconfig_bootstrap_points_at_repo_managed_cfg() -> None:
    """The app-bundle prefs shim should load the repo-managed Twilight config."""
    prefs = _run_node_json(
        dedent(
            """
            const fs = require("node:fs");
            const vm = require("node:vm");
            const prefs = [];
            function pref(name, value) {
              prefs.push([name, value]);
            }
            vm.runInNewContext(
              fs.readFileSync(process.argv[1], "utf8"),
              { pref },
              { filename: process.argv[1] },
            );
            console.log(JSON.stringify(prefs));
            """
        ),
        REPO_ROOT / "home/george/zen/autoconfig/autoconfig.js",
    )

    assert prefs == [
        ["general.config.filename", "twilight.cfg"],
        ["general.config.obscure_value", 0],
        ["general.config.sandbox_enabled", False],
    ]


@pytest.mark.skipif(_NODE is None, reason="node command not available")
def test_twilight_cfg_targets_common_dialog_accept_button_inside_shadow_root() -> None:
    """The Twilight AutoConfig script should patch the internal accept button."""
    result = _run_node_json(
        dedent(
            r"""
            const fs = require("node:fs");
            const vm = require("node:vm");
            const sourcePath = process.argv[1];
            const source = fs.readFileSync(sourcePath, "utf8");
            const observed = [];
            const reports = [];
            const ObserverService = {
              addObserver(observer, topic) {
                observed.push({ observer, topic });
              },
            };
            const classTargets = {
              "@mozilla.org/observer-service;1": {
                getService(service) {
                  if (service !== Components.interfaces.nsIObserverService) {
                    throw new Error("unexpected observer service interface");
                  }
                  return ObserverService;
                },
              },
            };
            const Components = {
              classes: new Proxy(classTargets, {
                get(target, prop) {
                  if (Object.prototype.hasOwnProperty.call(target, prop)) {
                    return target[prop];
                  }
                  throw new Error(`unexpected XPCOM class lookup: ${String(prop)}`);
                },
              }),
              interfaces: { nsIObserverService: Symbol("nsIObserverService") },
              utils: {
                reportError(error) {
                  reports.push(String(error && error.stack ? error.stack : error));
                },
              },
            };
            vm.runInNewContext(source, { Components }, { filename: sourcePath });
            if (reports.length) {
              throw new Error(reports.join("\n"));
            }
            if (observed.length !== 1) {
              throw new Error(`expected one observer, got ${observed.length}`);
            }

            const observer = observed[0].observer;
            let registeredEvent = null;
            observer.observe({
              addEventListener(type, handler, options) {
                registeredEvent = {
                  type,
                  sameHandler: handler === observer,
                  once: options && options.once === true,
                };
              },
            });

            class FakeMutationObserver {
              constructor(callback) {
                this.callback = callback;
              }
              observe(target, options) {
                target.mutationOptions = options;
                target.observerCallback = this.callback;
              }
            }
            class FakeButton {
              constructor() {
                this.attrs = new Map([
                  ["part", "dialog-button"],
                  ["default", "true"],
                ]);
                this.ownerGlobal = { MutationObserver: FakeMutationObserver };
              }
              getAttribute(name) {
                return this.attrs.has(name) ? this.attrs.get(name) : null;
              }
              setAttribute(name, value) {
                this.attrs.set(name, String(value));
              }
              removeAttribute(name) {
                this.attrs.delete(name);
              }
            }

            const button = new FakeButton();
            let selector = null;
            let commonDialogId = null;
            const timeouts = [];
            const document = {
              documentURI: "chrome://global/content/commonDialog.xhtml?x",
              defaultView: {
                setTimeout(fn, delay) {
                  timeouts.push(delay);
                  fn();
                },
              },
              getElementById(id) {
                commonDialogId = id;
                return {
                  shadowRoot: {
                    appendChild() {
                      throw new Error("style injection should not happen");
                    },
                    querySelectorAll(value) {
                      selector = value;
                      return [button];
                    },
                  },
                };
              },
            };

            observer.handleEvent({ originalTarget: document });
            button.attrs.set("default", "true");
            button.attrs.set("part", "dialog-button");
            button.observerCallback();

            console.log(JSON.stringify({
              topic: observed[0].topic,
              registeredEvent,
              commonDialogId,
              selector,
              timeouts,
              attrs: Object.fromEntries(button.attrs),
              mutationOptions: button.mutationOptions,
            }));
            """
        ),
        REPO_ROOT / "home/george/zen/autoconfig/twilight.cfg",
    )

    assert result == {
        "attrs": {
            "data-catppuccin-primary-button": "true",
            "data-catppuccin-primary-button-watched": "true",
            "part": "dialog-button catppuccin-primary-button",
        },
        "commonDialogId": "commonDialog",
        "mutationOptions": {
            "attributeFilter": ["default", "part"],
            "attributes": True,
        },
        "registeredEvent": {
            "once": True,
            "sameHandler": True,
            "type": "DOMContentLoaded",
        },
        "selector": 'button:is([dlgtype="accept"], [label^="Quit "])',
        "timeouts": [0],
        "topic": "chrome-document-global-created",
    }
