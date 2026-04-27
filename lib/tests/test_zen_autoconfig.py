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


def _run_node_json(script: str, source_path: Path, *extra_paths: Path) -> object:
    assert _NODE is not None
    result = subprocess.run(  # noqa: S603
        [_NODE, "-e", script, str(source_path), *(str(path) for path in extra_paths)],
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
            const cssPath = process.argv[2];
            const source = fs.readFileSync(sourcePath, "utf8");
            const cssSource = fs.readFileSync(cssPath, "utf8");
            const observed = [];
            const reports = [];
            let resolvedCssPath = null;
            const ObserverService = {
              addObserver(observer, topic) {
                observed.push({ observer, topic });
              },
            };
            class FakeFile {
              constructor() {
                this.pathSegments = [];
              }
              append(segment) {
                this.pathSegments.push(segment);
              }
            }
            const classTargets = {
              "@mozilla.org/observer-service;1": {
                getService(service) {
                  if (service !== Components.interfaces.nsIObserverService) {
                    throw new Error("unexpected observer service interface");
                  }
                  return ObserverService;
                },
              },
              "@mozilla.org/file/directory_service;1": {
                getService(service) {
                  if (service !== Components.interfaces.nsIProperties) {
                    throw new Error("unexpected directory service interface");
                  }
                  return {
                    get(name, iface) {
                      if (name !== "Home" || iface !== Components.interfaces.nsIFile) {
                        throw new Error("unexpected directory lookup");
                      }
                      return new FakeFile();
                    },
                  };
                },
              },
              "@mozilla.org/network/file-input-stream;1": {
                createInstance(service) {
                  if (service !== Components.interfaces.nsIFileInputStream) {
                    throw new Error("unexpected file input stream interface");
                  }
                  return {
                    init(file) {
                      resolvedCssPath = file.pathSegments.join("/");
                    },
                    close() {},
                  };
                },
              },
              "@mozilla.org/intl/converter-input-stream;1": {
                createInstance(service) {
                  if (service !== Components.interfaces.nsIConverterInputStream) {
                    throw new Error("unexpected converter stream interface");
                  }
                  let done = false;
                  return {
                    init() {},
                    readString(_count, out) {
                      if (done) {
                        return 0;
                      }
                      done = true;
                      out.value = cssSource;
                      return cssSource.length;
                    },
                    close() {},
                  };
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
              interfaces: {
                nsIConverterInputStream: Symbol("nsIConverterInputStream"),
                nsIFile: Symbol("nsIFile"),
                nsIFileInputStream: Symbol("nsIFileInputStream"),
                nsIObserverService: Symbol("nsIObserverService"),
                nsIProperties: Symbol("nsIProperties"),
              },
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

            class FakeStyleDeclaration {
              constructor() {
                this.values = {};
              }
              setProperty(name, value, priority) {
                this.values[name] = { value, priority };
              }
            }
            class FakeMutationObserver {
              constructor(callback) {
                this.callback = callback;
              }
              observe(target, options) {
                target.mutationOptions = options;
                target.observerCallback = this.callback;
              }
            }
            class FakeElement {
              constructor() {
                this.attrs = new Map();
                this.children = new Map();
                this.style = new FakeStyleDeclaration();
              }
              addChild(selector, child) {
                this.children.set(selector, child);
              }
              querySelector(selector) {
                return this.children.get(selector) || null;
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
            class FakeButton extends FakeElement {
              constructor() {
                super();
                this.attrs = new Map([
                  ["part", "dialog-button"],
                  ["default", "true"],
                ]);
                this.ownerGlobal = { MutationObserver: FakeMutationObserver };
              }
            }

            const button = new FakeButton();
            const buttonBox = new FakeElement();
            const buttonIcon = new FakeElement();
            const buttonText = new FakeElement();
            button.addChild(".button-box", buttonBox);
            button.addChild(".button-icon", buttonIcon);
            button.addChild(".button-text", buttonText);

            let selector = null;
            let commonDialogId = null;
            let appendedStyle = null;
            const timeouts = [];
            const shadowRoot = {
              getElementById(id) {
                return appendedStyle && appendedStyle.id === id ? appendedStyle : null;
              },
              appendChild(element) {
                appendedStyle = element;
              },
              querySelectorAll(value) {
                selector = value;
                return [button];
              },
            };
            const document = {
              documentURI: "chrome://global/content/commonDialog.xhtml?x",
              defaultView: {
                setTimeout(fn, delay) {
                  timeouts.push(delay);
                  fn();
                },
              },
              createElementNS(namespace, name) {
                if (namespace !== "http://www.w3.org/1999/xhtml" || name !== "style") {
                  throw new Error(`unexpected element creation: ${namespace} ${name}`);
                }
                return { id: null, textContent: "" };
              },
              getElementById(id) {
                commonDialogId = id;
                return { shadowRoot };
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
              appendedStyle,
              resolvedCssPath,
              timeouts,
              attrs: Object.fromEntries(button.attrs),
              buttonStyles: button.style.values,
              buttonBoxStyles: buttonBox.style.values,
              buttonIconStyles: buttonIcon.style.values,
              buttonTextStyles: buttonText.style.values,
              mutationOptions: button.mutationOptions,
            }));
            """
        ),
        REPO_ROOT / "home/george/zen/autoconfig/twilight.cfg",
        REPO_ROOT / "home/george/zen/quit-dialog-primary.css",
    )

    assert result == {
        "attrs": {
            "data-catppuccin-primary-button": "true",
            "data-catppuccin-primary-button-watched": "true",
            "data-nixcfg-catppuccin-patched": "true",
            "part": "dialog-button catppuccin-primary-button",
        },
        "appendedStyle": {
            "id": "nixcfg-catppuccin-quit-dialog-accept-style",
            "textContent": result["appendedStyle"]["textContent"],
        },
        "buttonBoxStyles": {},
        "buttonIconStyles": {},
        "buttonStyles": {},
        "buttonTextStyles": {},
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
        "resolvedCssPath": ".config/zen/quit-dialog-primary.css",
        "selector": 'button:is([dlgtype="accept"], [default="true"], [label^="Quit "])',
        "timeouts": [0],
        "topic": "chrome-document-global-created",
    }
    css = Path(REPO_ROOT / "home/george/zen/quit-dialog-primary.css").read_text(
        encoding="utf-8"
    )
    assert result["appendedStyle"]["textContent"] == css
    assert result["appendedStyle"]["textContent"].strip()
