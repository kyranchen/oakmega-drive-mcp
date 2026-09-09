"""Google Drive access. Knows nothing about MCP.

The boundary is deliberate: everything here takes plain arguments and returns
plain dataclasses, so the read path can be tested without speaking the MCP
protocol. If you ever find yourself importing from mcp_server/ in this
package, the dependency has been inverted.
"""
