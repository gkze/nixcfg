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
    # browser, so no token is stored here.
    executor-local-george = executorRemote "http://127.0.0.1:4312/mcp";
    # Hosted v2 serves every organization at one address. Choose the
    # organization when signing in: george (george-81ff29) or town.
    executor-cloud-george = executorRemote "https://mcp.executor.sh/mcp";
    executor-cloud-town = executorRemote "https://mcp.executor.sh/mcp";
  };
  work = { };
  zed = { };
}
