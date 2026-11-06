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
    # Select George (george-81ff29) during OAuth. Cloud George v1 retired 2026-11-04.
    executor-v2-george = executorRemote "https://mcp.executor.sh/mcp";
    # Executor 2's local background server. Clients sign in through the
    # browser with the dashboard `executor` paired, so no token is stored here.
    # Executor 1's daemon on port 4788 was retired 2026-11-05.
    executor-local = executorRemote "http://127.0.0.1:4312/mcp";
  };
  work = { };
  zed = { };
}
