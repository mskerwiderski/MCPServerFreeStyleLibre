# Installing for Claude

Two routes:

- **Standalone, on your own computer** - Claude Code or Claude Desktop starts
  `libre-mcp stdio` locally. Anyone with a LibreLinkUp follower account can
  use this; no server, no account. Full guide: [standalone.md](standalone.md).
  In short:

  ```bash
  uv tool install git+https://github.com/mskerwiderski/MCPServerFreeStyleLibre
  libre-mcp login
  claude mcp add --scope user libre -- libre-mcp stdio
  ```

- **Hosted, as a remote connector** - for claude.ai in the browser and the
  phone app. Needs a hosted server and your account there (MyAuth account with
  the app enabled, LibreLinkUp connected in MyAuth): [account.md](account.md).
  The steps below use the placeholder `https://mcp.example.com/libre`; use
  your server's connector address.

---

## claude.ai (browser and phone app)

1. **Settings → Connectors → Add custom connector**.
2. Paste the connector address, e.g. `https://mcp.example.com/libre`.
3. Claude registers itself and sends you to the server's consent page. You are
   signed in through MyAuth (sign in there first if asked) - click **Allow**.
4. The connector appears in your list. Open a new chat and ask something.

The connector also shows up in the Claude phone app and in Claude Desktop once
it is added on claude.ai.

## Claude Code (hosted)

```bash
claude mcp add --transport http libre https://mcp.example.com/libre
```

Run `/mcp` in a session afterwards to complete the OAuth login (consent page as
above). MCP servers are loaded when a session starts - start a new one.

---

## Troubleshooting

**"not signed in to LibreLinkUp yet - run `libre-mcp login` …"** (standalone)
Run `libre-mcp login` in a terminal on the same computer, then ask again.

**"The LibreLinkUp sign-in has expired"** (standalone)
The session lasts about half a year. Run `libre-mcp login` again.

**"LibreLinkUp is not connected yet"** (hosted)
The server is reachable, but your MyAuth account has no LibreLinkUp
connection. Connect it in MyAuth (the account page links to it). The connector
keeps working afterwards - nothing to re-add.

**"Nobody shares their glucose with this LibreLinkUp account yet"**
The invitation from the FreeStyle Libre app has not been accepted in the
LibreLinkUp app yet, or it went to a different e-mail address.

**"LibreLinkUp asks to wait …"**
Abbott locked the account briefly after too many requests or sign-ins. The
server pauses as asked (at most 15 minutes) and continues by itself.

**The consent page does not load when adding the connector** (hosted)
It signs you in through MyAuth. If MyAuth is private, your device has to be on
its network or VPN - connect and try again. *No access* there means the app is
not enabled for your MyAuth account.

**The newest value is missing or old**
The phone app uploads to LibreLinkUp irregularly in the background; check
`minutes_ago` in `get_current_glucose`. Opening the FreeStyle Libre app on the
phone usually triggers an upload.

**Answers are empty for a period**
Ask Claude to call `whoami`: it shows how far the stored history reaches and
the collector's last success and error. LibreLinkUp only shows 12 hours, so
the history starts when collecting started - in the hosted mode, earlier
readings can be imported from LibreView ([account.md](account.md)).
