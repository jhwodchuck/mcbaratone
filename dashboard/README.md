# mcbaratone dashboard

This React/Vite frontend visualizes controller and bridge observations such as
phase, health, hunger, position, failures, and inventory. It is retained as a
public UI component; environment-specific data collectors and process-control
wrappers are deliberately kept outside the public repository.

## Development

Use Node.js 24:

```powershell
npm ci
npm run lint
npm run build
```

The frontend expects a compatible local API when used with a live runtime.
Connecting it to Minecraft or enabling controls crosses the live-world safety
boundary described in [`AGENTS.md`](../AGENTS.md).
