# Direct third-party MCP registrations are intentionally absent here.
# External integrations are reached through Executor, so this catalog only
# carries the Executor gateway endpoints.
_:
let
  executorRemote = url: {
    type = "remote";
    inherit url;
    enable = true;
  };
in
{
  base = {
    # Canonical Executor v2 entries, named executor-<location>-<workspace>.
    # Local is this machine's background server; clients sign in through the
    # browser, so no token is stored here. No elicitation_mode: local v2.0.0-beta.41
    # rejects it ("Unsupported elicitation_mode", HTTP 400); pick the approval
    # mode on the sign-in page instead (2026-11-15).
    executor-local-george = executorRemote "http://127.0.0.1:4312/mcp";
    # Hosted v2 serves every organization at one address. Choose the
    # organization when signing in: george (george-81ff29) or town.
    # The mode is kept by the grant chosen at sign-in; the URL preselects it.
    executor-cloud-george = executorRemote "https://mcp.executor.sh/mcp?connection=george&elicitation_mode=automatic";
    executor-cloud-town = executorRemote "https://mcp.executor.sh/mcp";
  };
  work = { };
  zed = { };
}
