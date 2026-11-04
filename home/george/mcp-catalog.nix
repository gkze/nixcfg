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
    executor-local = executorRemote "http://localhost:4788/mcp" // {
      # The local daemon authenticates its supervised bearer token and exposes
      # no OAuth metadata, so OAuth discovery must stay off. The token is
      # injected by the Executor service into the login session environment.
      oauth = false;
      headers.Authorization = "Bearer {env:EXECUTOR_LOCAL_TOKEN}";
    };
  };
  work = { };
  zed = { };
}
