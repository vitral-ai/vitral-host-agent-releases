# Connect a keeper to its native conversation

The Vitral host can show the conversation ID and a command to resume it when a
Codex or Claude Code keeper exits. Set up the matching SessionStart command in
the agent's existing hook configuration. Keep your existing settings and hooks,
and use the verified installed `vitral-host` executable. If it is outside the
agent's PATH, use its absolute path and quote it for that agent's shell.

For Codex, add this command to a `SessionStart` hook group in
`~/.codex/hooks.json`. Review and approve the new command at the Codex hook
trust prompt. On Codex versions offering a shared daemon, launch
`vitral-host run -- codex --no-daemon` so the hook runs in the keeper's
environment.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "vitral-host keeper-native-id codex", "timeout": 3 }
        ]
      }
    ]
  }
}
```

For Claude Code, add this command to a `SessionStart` hook group in the
user's `~/.claude/settings.json`, then launch
`vitral-host run -- claude`.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "vitral-host keeper-native-id claude", "timeout": 3 }
        ]
      }
    ]
  }
}
```

Run `vitral-host keeper-list` while the conversation is open. A verified
keeper lists its `native_session_id`. If you decline hook trust, use the
agent's resume picker. `vitral-host doctor` reports live agent keepers that
have no verified ID. This configuration does not change agent sign-in.

When the same keeper reports a switch to a different conversation ID, Vitral
uses the agent's resume picker. Start a new keeper to restore an exact link.

## Configuración en español

Agregue el comando SessionStart al archivo de configuración del agente
indicado arriba. Mantenga las opciones y los ganchos que ya tenía. Si Codex le
pide aprobar el gancho, compruebe que el comando llama al binario verificado
de Vitral Host y apruébelo si quiere vincular la conversación. El inicio de
sesión de Codex y Claude Code permanece en la configuración del agente.

Mientras la conversación esté abierta, use `vitral-host keeper-list` para
comprobar si aparece `native_session_id`. Si decidió omitir el gancho, use
el selector de conversaciones de su agente para continuar.
