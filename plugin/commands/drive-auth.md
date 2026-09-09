---
description: Check Google Drive connection status and re-authenticate if needed.
---

Check whether the google-drive MCP server is connected and authorised.

Call `list_files`. If it returns files, report that the connection is healthy
and summarise what is in the folder. If it fails with an authorisation error,
tell the user to run `/mcp` to start the Google sign-in flow, and explain that
their Google account must have access to the shared folder.
