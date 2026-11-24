# Native stdio conversation note tool
The optional `scoped-tool` grant must have exactly one permission,
`mcp.conversation_note.write`, and an exact `account_scope`. The operator
launches the existing authenticated `mcp` command in native-live directory
mode. This grant is not accepted by snapshot stdio or MCP HTTP and does not
advertise resources, prompts, discovery or general search. Prior read-only
grants cannot list or call the new tool.

`manage_conversation_note` takes explicit `platform`, `account_id`, and
`conversation_id`. `operation: "revision"` returns the current metadata
revision; `operation: "update"` requires that revision, the replacement
`note`, and `confirm: true`. An empty replacement clears the note. The
client must obtain the operator's actual approval before sending the update;
the boolean is an acknowledgement, not a substitute for a trusted consent UI.
The tool does not disclose note text, tags, audit entries, or archive state of
the conversation. It cannot change tags, archive status or contacts, and does
not send messages.

Real changes use the shared immediate SQLite transaction and metadata audit.
An identical final note is a no-op, with its current revision returned even if
the supplied revision is stale; stale changes fail. Obtain fresh explicit approval
before retrying a conflicting change. Legacy live archives need the existing
offline metadata upgrade. No unversioned legacy tool alias is installed.
To disable the tool, remove its grant and stop/restart the stdio process;
source rollback does not undo already committed notes.
