# Hosted: your account

This is for users of a **hosted** server ([hosted.md](hosted.md)). Running it
on your own computer instead? See [standalone.md](standalone.md) - no account
needed there.

A hosted server usually has two addresses; your administrator tells you
yours. The examples below use these placeholders:

| | Address (example) | Reachable |
|---|---|---|
| Connector (for the AI clients) | `https://mcp.example.com/libre` | from anywhere |
| Account and consent pages | `https://home.example.com:1031` | possibly only on the private network |

There is no sign-up and no password of its own: you sign in with **MyAuth**,
the login service the server is run with. This is the setup every AI client
needs once, before adding the connector.

## Before you start

- A **MyAuth account with the app enabled** (the Libre MCP app; its key is
  `mcp-libre` by default). An administrator switches it on in MyAuth. Without
  it the account page says *No access*.
- A **device that can reach MyAuth** and the account page. In a private setup
  that means being on the home network or connected through its VPN. Only the
  connector address has to be public - that is what lets claude.ai, ChatGPT
  and Mistral reach it.
- The **FreeStyle Libre app** on the phone that reads the sensor.

## 1. Create a LibreLinkUp follower account

LibreLinkUp is Abbott's app for followers: the person with the sensor shares
their glucose with them. The server reads through such a follower account,
even when you follow yourself:

1. In the FreeStyle Libre app on the phone with the sensor: **Menu →
   Connected Apps → LibreLinkUp → Add Connection**, and invite an e-mail
   address of your own, e.g. `you+libre@example.com`.
2. Install LibreLinkUp, create an account with that address, accept the
   invitation and the terms of use. After that the LibreLinkUp app is not
   needed any more.

## 2. Connect LibreLinkUp - in MyAuth

The LibreLinkUp connection lives in MyAuth, not on this server, so every app
that reads glucose uses the same one. Open the account page
(`https://home.example.com:1031/account`; without a MyAuth session you are sent
to the MyAuth sign-in and come straight back) and follow **Connect
LibreLinkUp in MyAuth**.

Enter the follower e-mail and password; if several people share their glucose
with that account, also the name of the person to read. MyAuth keeps the
password encrypted, so it can sign in again by itself when the LibreLinkUp
session ends, and hands this server only the session. If LibreLinkUp rejects
the password, MyAuth discards it at once and asks for a new connection -
repeated failed sign-ins would lock the account at Abbott.

The account page now shows LibreLinkUp as **connected** and whose glucose it
reads. The collector fetches the last 12 hours at its next round (within a
minute).

## 3. The account page

It refreshes itself every minute.

- **LibreLinkUp** - the connection state, the last reading, the last error,
  and **how often LibreLinkUp is asked**: every 1, 3, 5, 15, 30 or 60 minutes
  (default 1). Only every minute keeps the minute values - the live value the
  sensor shows, which LibreLinkUp never hands out later and which other apps
  can use, e.g. to draw glucose into a workout. Longer intervals keep only the
  5-minute history, which loses nothing up to 12 hours.
- **Statistics** - mean, time in ranges, GMI and a chart for the last 1 hour
  up to 12 months. The **unit** (mg/dL or mmol/L, by default like the Libre
  app) only changes this page; the tools always answer in mg/dL. The **GMI
  period** (14, 30 or 90 days, default 14, the consensus) sets "your" GMI here
  and for other apps.
- **Sensor** - the current sensor (serial number, activation, day of wear,
  warm-up, the app's alarm limits) and every sensor before it.
- **History** - how many readings are stored, how long they are kept (3
  months up to 10 years, or unlimited; default 2 years - older readings are
  deleted), and the **import from LibreView**.

## 4. Import your earlier history (optional)

LibreLinkUp only shows the last 12 hours, so the server collects from the day
you connect. For everything before: on
[libreview.com](https://www.libreview.com) open **Glucose History → Download
glucose data**, and upload the CSV under **History** on the account page (at
most 50 MB). Readings that are already stored are skipped, and so is
everything older than your retention. German and English exports and mmol/L
files are understood; only the sensor's own history is imported, not scans.

## 5. Copy the connector address

The account page shows it ready to copy, for example:

```
https://mcp.example.com/libre
```

The same address for every client, exactly like this. When a client adds it,
it sends your browser to the consent page that asks you to **Allow** read
access - you are signed in through MyAuth there, so again: on a device that
can reach MyAuth. After that the connector works from anywhere, including the
phone apps.

Continue with your client: [Claude](install-claude.md),
[ChatGPT](install-chatgpt.md), [Mistral Vibe](install-mistral.md).

## Removing access

- **Your data:** *Delete my data* on the account page removes every stored
  reading, the sensor history, your settings and every connector
  authorization. Your MyAuth account and its
  LibreLinkUp connection stay - disconnect LibreLinkUp in MyAuth to cut it off
  for every app.
- **Somebody else's access:** switch the app off for them in MyAuth, or block
  the user there. Their connectors stop working within about 30 seconds.
