import React, { useState, useEffect, useRef } from 'react';
import './App.css';

const API_BASE = "";

// ---- helpers -------------------------------------------------------------

const getItemEmoji = (id) => {
  if (!id) return "";
  const name = id.split(':').pop();
  if (name.includes('pickaxe')) return "⛏️";
  if (name.includes('axe')) return "🪓";
  if (name.includes('sword')) return "⚔️";
  if (name.includes('shovel')) return "🥄";
  if (name.includes('hoe')) return "🌱";
  if (name.includes('planks')) return "🪵";
  if (name.includes('log')) return "🌲";
  if (name.includes('stick')) return "🥢";
  if (name.includes('crafting_table')) return "📦";
  if (name.includes('chest')) return "🧳";
  if (name.includes('furnace')) return "🔥";
  if (name.includes('shield')) return "🛡️";
  if (name.includes('iron_ingot')) return "🪙";
  if (name.includes('coal')) return "⬛";
  if (name.includes('apple')) return "🍎";
  if (name.includes('bread')) return "🍞";
  if (name.includes('wheat')) return "🌾";
  if (name.includes('beef') || name.includes('porkchop')) return "🥩";
  if (name.includes('leather')) return "📜";
  if (name.includes('cobblestone')) return "🪨";
  if (name.includes('air')) return "";
  return "📦";
};

const barColor = (pct, warn, danger) => {
  if (pct <= danger) return 'fill-red';
  if (pct <= warn) return 'fill-orange';
  return 'fill-green';
};

function SystemOverview({ system, fleet, onScaleFleet, onUpgradeAll, scalingMsg }) {
  const mem = system?.memory || {};
  const cpu = system?.cpu || {};
  const sysFleet = system?.fleet || {};
  const activeCount = sysFleet?.active_bot_count ?? 0;
  const loadPct = mem?.load_percent ?? 0;
  const cpuPct = cpu?.load_percent ?? 0;
  const latestBuiltJar = fleet?.latest_built_bridge_jar;
  const anyUpgradeAvailable = fleet?.bots?.some((b) => b.upgrade_available);

  return (
    <section className="system-overview card">
      <div className="server-title-row">
        <div className="card-header font-glow" style={{ border: 'none', padding: 0, margin: 0 }}>
          💻 System Headroom & Fleet Scaling (WFH Mode)
        </div>
        <div className="wfh-badge-group" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {latestBuiltJar && (
            <div className="jar-badge">
              <span>Latest Bridge: <strong>{latestBuiltJar}</strong></span>
            </div>
          )}
          <div className="wfh-badge">
            <span>WFH Limit: <strong>{sysFleet?.wfh_recommended_max ?? 4} Max Bots Recommended</strong></span>
          </div>
        </div>
      </div>

      <div className="system-metrics-grid">
        <div className="system-metric-card">
          <div className="metric-label"><span>CPU Utilization</span><strong>{cpuPct}%</strong></div>
          <div className="bar-container">
            <div className={`bar-fill ${barColor(100 - cpuPct, 50, 25)}`} style={{ width: `${cpuPct}%` }} />
          </div>
        </div>

        <div className="system-metric-card">
          <div className="metric-label">
            <span>RAM Load</span>
            <strong>{mem.used_gb || 0} / {mem.total_gb || 0} GB ({loadPct}%)</strong>
          </div>
          <div className="bar-container">
            <div className={`bar-fill ${barColor(100 - loadPct, 50, 25)}`} style={{ width: `${loadPct}%` }} />
          </div>
          <div className="metric-sub">Free RAM: <strong>{mem.available_gb || 0} GB</strong></div>
        </div>

        <div className="scale-controls-card">
          <div className="scale-label">
            <span>Active Bot Count: <strong className="neon-value">{activeCount}</strong></span>
          </div>
          <div className="scale-button-group">
            <button className="btn btn-secondary btn-sm" onClick={() => onScaleFleet(Math.max(0, activeCount - 1))}>- Ramp Down</button>
            <button className="btn btn-primary btn-sm" onClick={() => onScaleFleet(activeCount + 1)}>+ Ramp Up</button>
            <div className="preset-divider" />
            <button className="btn btn-outline btn-xs" onClick={() => onScaleFleet(1)}>1 Bot</button>
            <button className="btn btn-outline btn-xs" onClick={() => onScaleFleet(2)}>2 Bots (WFH Safe)</button>
            <button className="btn btn-outline btn-xs" onClick={() => onScaleFleet(4)}>4 Bots (Full)</button>
            {anyUpgradeAvailable && (
              <button className="btn btn-success btn-xs" onClick={onUpgradeAll} style={{ marginLeft: 'auto' }}>
                🚀 Roll Out Upgrade to All Bots
              </button>
            )}
          </div>
          {scalingMsg && <div className="scaling-msg">{scalingMsg}</div>}
        </div>
      </div>
    </section>
  );
}

function ServerOverview({ server }) {
  const online = server?.online;
  const players = server?.player_names || [];
  return (
    <section className={`server-overview card ${online === false ? 'server-offline' : ''}`}>
      <div className="server-title-row">
        <div className="card-header font-glow" style={{ border: 'none', padding: 0, margin: 0 }}>
          Minecraft Server
        </div>
        <div className="server-state">
          <span className={`status-dot ${online ? 'active' : ''}`} />
          <span>{server ? (online ? 'ONLINE' : 'OFFLINE') : 'CHECKING…'}</span>
        </div>
      </div>

      <div className="server-metrics">
        <div className="server-metric"><span>Address</span><strong>{server?.address || '—'}</strong></div>
        <div className="server-metric"><span>Version</span><strong>{server?.version || '—'}</strong></div>
        <div className="server-metric"><span>Players</span><strong>{online ? `${server.players_online}/${server.players_max}` : '—'}</strong></div>
        <div className="server-metric"><span>Latency</span><strong>{server?.latency_ms != null ? `${server.latency_ms} ms` : '—'}</strong></div>
        <div className="server-metric"><span>Server log</span><strong>{server?.local_log_age_seconds != null ? `${server.local_log_age_seconds}s ago` : 'remote'}</strong></div>
      </div>

      <div className="server-detail-row">
        <span className="server-motd">{online ? (server.motd || 'Minecraft server') : (server?.error || 'Server did not answer the status query')}</span>
        <div className="server-players">
          {players.length > 0
            ? players.map((name) => <span className="player-chip" key={name}>{name}</span>)
            : <span className="server-no-sample">{online && server.players_online > 0 ? 'Player names hidden by server' : 'No players online'}</span>}
        </div>
      </div>
    </section>
  );
}

// ---- fleet overview ------------------------------------------------------

function BotCard({ bot, selected, onSelect, onToggleBot, onUpgradeBot }) {
  const health = bot.health ?? null;
  const food = bot.food ?? null;
  const healthPct = health != null ? (health / 20) * 100 : 0;
  const foodPct = food != null ? (food / 20) * 100 : 0;
  const fresh = bot.heartbeat_fresh;
  const running = bot.runtime_state === 'active';
  const age = bot.heartbeat_age_seconds;
  const failure = bot.current_failures?.length ? bot.current_failures[bot.current_failures.length - 1] : "";
  const jarVer = bot.installed_bridge_version ? `v${bot.installed_bridge_version}` : "no JAR";
  const upgradeAvail = bot.upgrade_available;

  return (
    <div
      className={`bot-card card ${selected ? 'bot-card-selected' : ''} ${!running ? 'bot-card-stopped' : ''}`}
      onClick={() => onSelect(bot)}
    >
      <div className="bot-card-head">
        <span className="bot-card-name font-glow">{bot.bot}</span>
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
          <button
            className={`btn btn-xs ${running ? 'btn-danger' : 'btn-primary'}`}
            style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
            onClick={(e) => { e.stopPropagation(); onToggleBot?.(bot); }}
          >
            {running ? 'Stop' : 'Start'}
          </button>
          {upgradeAvail && (
            <button
              className="btn btn-xs btn-success"
              style={{ padding: '0.2rem 0.4rem', fontSize: '0.7rem' }}
              title="New bridge JAR built! Click to upgrade."
              onClick={(e) => { e.stopPropagation(); onUpgradeBot?.(bot); }}
            >
              🚀 Upgrade
            </button>
          )}
        </div>
        <span className={`bot-card-status ${fresh && running ? 'ok' : (running ? 'warn' : 'stopped')}`}>
          {running ? (fresh ? 'LIVE' : 'STALE') : 'STOPPED'}
        </span>
      </div>

      <div className="bot-card-phase" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>{bot.phase || '—'}</span>
        <span className={`jar-ver-chip ${upgradeAvail ? 'upgrade-needed' : ''}`}>{jarVer}</span>
      </div>

      <div className="bot-card-bars">
        <div className="mini-bar">
          <div className="mini-bar-label"><span>HP</span><span>{health != null ? health.toFixed?.(1) ?? health : '—'}</span></div>
          <div className="bar-container"><div className={`bar-fill ${barColor(healthPct, 50, 25)}`} style={{ width: `${healthPct}%` }} /></div>
        </div>
        <div className="mini-bar">
          <div className="mini-bar-label"><span>Food</span><span>{food ?? '—'}</span></div>
          <div className="bar-container"><div className={`bar-fill ${barColor(foodPct, 45, 30)}`} style={{ width: `${foodPct}%` }} /></div>
        </div>
      </div>

      <div className="bot-card-meta">
        <span>📍 {bot.position ? bot.position.join(', ') : '—'}</span>
        <span>⏱ {age != null ? `${age}s` : '—'}</span>
      </div>
      {bot.bridge_unreachable && <div className="bot-card-alert">Bridge unreachable</div>}
      {failure && <div className="bot-card-alert" title={failure}>⚠ {failure}</div>}
    </div>
  );
}

function FleetOverview({ fleet, selectedName, onSelect, onToggleBot, onUpgradeBot }) {
  const bots = fleet?.bots || [];
  return (
    <section className="fleet-overview card">
      <div className="fleet-head">
        <div className="card-header font-glow" style={{ border: 'none', padding: 0 }}>Fleet Overview</div>
        <div className="fleet-summary">
          <span className="neon-value">
            {fleet ? `${fleet.healthy_heartbeats}/${fleet.fleet_size} healthy` : '…'}
          </span>
          {fleet?.discovered_fleet_size != null && (
            <span className="fleet-sub">{fleet.discovered_fleet_size} discovered</span>
          )}
        </div>
      </div>

      <div className="bot-card-grid">
        {bots.length === 0 && <div className="telemetry-fallback">No bots discovered yet…</div>}
        {bots.map((bot) => (
          <BotCard key={bot.bot} bot={bot} selected={bot.bot === selectedName} onSelect={onSelect} onToggleBot={onToggleBot} onUpgradeBot={onUpgradeBot} />
        ))}
      </div>

      {(fleet?.current_failure_clusters?.length > 0 || fleet?.shared_house_origins?.length > 0 || fleet?.shared_storage_coordinates?.length > 0) && (
        <div className="fleet-alerts">
          {fleet.current_failure_clusters?.slice(0, 4).map((c, i) => (
            <div key={`f${i}`} className="fleet-alert">⚠ {c.bots} bot(s): {c.failure}</div>
          ))}
          {fleet.shared_storage_coordinates?.map((c, i) => (
            <div key={`s${i}`} className="fleet-alert danger">Storage {JSON.stringify(c.coordinate)} shared by {c.bots.join(', ')}</div>
          ))}
          {fleet.shared_house_origins?.map((c, i) => (
            <div key={`h${i}`} className="fleet-alert danger">House {JSON.stringify(c.coordinate)} shared by {c.bots.join(', ')}</div>
          ))}
        </div>
      )}
    </section>
  );
}

// ---- per-bot detail ------------------------------------------------------

function BotDetail({ bot }) {
  const botQuery = `?bot=${bot.bot}`;
  const [state, setState] = useState(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [inventory, setInventory] = useState([]);
  const [logs, setLogs] = useState([]);

  const [command, setCommand] = useState("");
  const [terminalHistory, setTerminalHistory] = useState([]);
  const [craftingCount, setCraftingCount] = useState(1);
  const [craftingStatus, setCraftingStatus] = useState("");

  const terminalEndRef = useRef(null);
  const logsEndRef = useRef(null);

  // Reset terminal when switching bots.
  useEffect(() => {
    setTerminalHistory([
      { type: 'system', text: `Selected ${bot.bot} (bridge ${bot.bridge_port}).` },
      { type: 'system', text: 'Manual commands here fight the autonomous controller — pause the bot first.' },
    ]);
    setState(null);
    setInventory([]);
  }, [bot.bot, bot.bridge_port]);

  // Live state + inventory polling for the selected bot.
  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/state${botQuery}`);
        const data = await res.json();
        if (cancelled) return;
        setConnected(data.connected);
        if (data.connected) {
          const live = data.state || {};
          setState({
            ...live,
            x: live.x ?? live.position?.x,
            y: live.y ?? live.position?.y,
            z: live.z ?? live.position?.z,
            foodLevel: live.foodLevel ?? live.food_level,
            hasGoal: live.hasGoal ?? live.is_pathing,
          });
          setError("");
        }
        else { setError(data.error || "Disconnected"); setState(null); }

        if (data.connected) {
          const invRes = await fetch(`${API_BASE}/api/inventory${botQuery}`);
          const invData = await invRes.json();
          if (!cancelled && invData.connected && invData.inventory) {
            const payload = invData.inventory;
            const slots = payload.slots || payload.inventory || (Array.isArray(payload) ? payload : []);
            setInventory(slots.filter((item) => item.id !== 'minecraft:air' && item.count > 0));
          }
        }
      } catch {
        if (!cancelled) { setConnected(false); setError("Dashboard server unreachable"); setState(null); }
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 2000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [botQuery]);

  // Per-bot log polling.
  useEffect(() => {
    let cancelled = false;
    const fetchLogs = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/logs${botQuery}`);
        const data = await res.json();
        if (!cancelled) setLogs(data.logs || []);
      } catch { /* ignore */ }
    };
    fetchLogs();
    const interval = setInterval(fetchLogs, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [botQuery]);

  useEffect(() => { terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [terminalHistory]);
  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [logs]);

  const postCommand = async (cmdText, label) => {
    setTerminalHistory(prev => [...prev, { type: 'input', text: `${label || '>'} ${cmdText}` }]);
    try {
      const res = await fetch(`${API_BASE}/api/command`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmdText, bot: bot.bot }),
      });
      const data = await res.json();
      setTerminalHistory(prev => [...prev, data.success
        ? { type: 'output', text: data.result || "OK" }
        : { type: 'error', text: `Error: ${data.error}` }]);
    } catch (err) {
      setTerminalHistory(prev => [...prev, { type: 'error', text: err.message }]);
    }
  };

  const handleSendCommand = (e) => {
    e.preventDefault();
    if (!command.trim()) return;
    postCommand(command.trim());
    setCommand("");
  };

  const handleCraftItem = async (itemId) => {
    setCraftingStatus(`Crafting ${craftingCount}x ${itemId}...`);
    try {
      const res = await fetch(`${API_BASE}/api/craft`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item: itemId, count: craftingCount, bot: bot.bot }),
      });
      const data = await res.json();
      setCraftingStatus(data.success ? `Crafted ${itemId}!` : `Failed: ${data.error || 'Check ingredients'}`);
    } catch (err) {
      setCraftingStatus(`Failed: ${err.message}`);
    }
    setTimeout(() => setCraftingStatus(""), 4000);
  };

  const renderInventoryGrid = () => {
    const grid = [];
    for (let r = 0; r < 3; r++) {
      const row = [];
      for (let c = 0; c < 9; c++) {
        const slotNum = 9 + r * 9 + c;
        row.push({ slot: slotNum, item: inventory.find(i => i.slot === slotNum) });
      }
      grid.push(row);
    }
    const hotbar = [];
    for (let c = 0; c < 9; c++) hotbar.push({ slot: c, item: inventory.find(i => i.slot === c) });
    grid.push(hotbar);

    return (
      <div className="inventory-grid-container">
        <div className="inventory-header">Inventory — {bot.bot}</div>
        <div className="grid-slots-wrapper">
          {grid.map((row, rIdx) => (
            <div key={rIdx} className={`inventory-row ${rIdx === 3 ? 'hotbar-row' : ''}`}>
              {row.map(cell => (
                <div key={cell.slot}
                  className={`inventory-slot ${cell.item ? 'has-item' : ''}`}
                  title={cell.item ? `${cell.item.id} (Count: ${cell.item.count})` : `Slot ${cell.slot}`}>
                  {cell.item ? (
                    <div className="slot-item-content">
                      <div className="item-emoji">{getItemEmoji(cell.item.id)}</div>
                      <span className="item-count">{cell.item.count}</span>
                    </div>
                  ) : (<span className="slot-number-muted">{cell.slot}</span>)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="detail-grid">
      {/* Telemetry + quick actions */}
      <section className="dashboard-column left-col">
        <div className="card telemetry-card">
          <div className="card-header font-glow">
            {bot.bot} Telemetry
            <span className={`inline-dot ${connected ? 'active' : ''}`} />
            <span className="inline-dot-label">{connected ? `bridge ${bot.bridge_port}` : 'offline'}</span>
          </div>
          {state ? (
            <div className="telemetry-stats">
              <div className="stat-progress-bar">
                <div className="bar-labels"><span>HP / Health</span>
                  <span className="neon-value font-red">{state.health != null ? state.health.toFixed(1) : '—'} / 20</span></div>
                <div className="bar-container"><div className="bar-fill fill-red" style={{ width: `${(state.health ?? 0) * 5}%` }} /></div>
              </div>
              <div className="stat-progress-bar">
                <div className="bar-labels"><span>Hunger</span>
                  <span className="neon-value font-orange">{state.foodLevel ?? '20'} / 20</span></div>
                <div className="bar-container"><div className="bar-fill fill-orange" style={{ width: `${(state.foodLevel ?? 0) * 5}%` }} /></div>
              </div>
              <div className="telemetry-grid">
                <div className="grid-item"><span className="item-label">Pos X</span><span className="item-val font-glow">{state.x != null ? state.x.toFixed(1) : '—'}</span></div>
                <div className="grid-item"><span className="item-label">Pos Y</span><span className="item-val font-glow">{state.y != null ? state.y.toFixed(1) : '—'}</span></div>
                <div className="grid-item"><span className="item-label">Pos Z</span><span className="item-val font-glow">{state.z != null ? state.z.toFixed(1) : '—'}</span></div>
                <div className="grid-item"><span className="item-label">Dimension</span><span className="item-val font-glow uppercase">{state.dimension || 'Overworld'}</span></div>
              </div>
              <div className="goal-status-box">
                <span className="item-label">Active Pathing Goal</span>
                <div className="goal-content">
                  {state.hasGoal
                    ? <div className="goal-active font-cyan">
                        {state.goalX != null
                          ? `🎯 Goto (${state.goalX}, ${state.goalY}, ${state.goalZ})`
                          : '🎯 Pathing in progress'}
                      </div>
                    : <div className="goal-inactive">Idle (No active target)</div>}
                </div>
              </div>
            </div>
          ) : (
            <div className="telemetry-fallback">{error || "Waiting for live bridge data…"}</div>
          )}
        </div>

        <div className="card actions-card">
          <div className="card-header font-glow">Quick Actions <span className="warn-chip">fights controller</span></div>
          <div className="actions-buttons-grid">
            <button onClick={() => postCommand("cancel", "[Quick]")} className="btn btn-danger" disabled={!connected}>🛑 Cancel Task</button>
            <button onClick={() => postCommand("goal clear", "[Quick]")} className="btn btn-secondary" disabled={!connected}>🧹 Clear Goal</button>
            <button onClick={() => postCommand("pause", "[Quick]")} className="btn btn-secondary" disabled={!connected}>⏸️ Pause</button>
            <button onClick={() => postCommand("resume", "[Quick]")} className="btn btn-primary" disabled={!connected}>▶️ Resume</button>
          </div>
        </div>
      </section>

      {/* Inventory + crafting */}
      <section className="dashboard-column center-col">
        <div className="card inventory-card">{renderInventoryGrid()}</div>
        <div className="card crafting-card">
          <div className="card-header font-glow">Manual Crafting <span className="warn-chip">fights controller</span></div>
          <div className="crafting-controls">
            <div className="crafting-count-selector">
              <label>Count:</label>
              <input type="number" min="1" max="64" value={craftingCount}
                onChange={(e) => setCraftingCount(Math.max(1, parseInt(e.target.value) || 1))} />
            </div>
            {craftingStatus && <div className="crafting-status-alert font-cyan">{craftingStatus}</div>}
          </div>
          <div className="crafting-recipes-grid">
            {[
              ["minecraft:oak_planks", "🪵 Oak Planks"],
              ["minecraft:stick", "🥢 Sticks"],
              ["minecraft:crafting_table", "📦 Table"],
              ["minecraft:chest", "🧳 Chest"],
              ["minecraft:furnace", "🔥 Furnace"],
              ["minecraft:shield", "🛡️ Shield"],
              ["minecraft:stone_pickaxe", "⛏️ Stone Pick"],
              ["minecraft:iron_pickaxe", "⛏️ Iron Pick"],
            ].map(([id, label]) => (
              <button key={id} onClick={() => handleCraftItem(id)} className="btn btn-secondary" disabled={!connected}>{label}</button>
            ))}
          </div>
        </div>
      </section>

      {/* Terminal + logs */}
      <section className="dashboard-column right-col">
        <div className="card terminal-card">
          <div className="card-header font-glow">Command Terminal — {bot.bot}</div>
          <div className="terminal-display">
            {terminalHistory.map((item, idx) => (
              <div key={idx} className={`terminal-line line-${item.type}`}>{item.text}</div>
            ))}
            <div ref={terminalEndRef} />
          </div>
          <form onSubmit={handleSendCommand} className="terminal-form">
            <input type="text" placeholder="goto 100 64 250, mine iron_ore, cancel…"
              value={command} onChange={(e) => setCommand(e.target.value)} disabled={!connected} />
            <button type="submit" className="btn btn-primary" disabled={!connected}>Send</button>
          </form>
        </div>
        <div className="card logs-card">
          <div className="card-header font-glow">Controller Log — {bot.bot}</div>
          <div className="logs-display">
            {logs.length > 0
              ? logs.map((line, idx) => <div key={idx} className="log-line">{line}</div>)
              : <div className="log-line-muted">No recent log lines.</div>}
            <div ref={logsEndRef} />
          </div>
        </div>
      </section>
    </div>
  );
}

// ---- root ----------------------------------------------------------------

function App() {
  const [fleet, setFleet] = useState(null);
  const [mcServer, setMcServer] = useState(null);
  const [system, setSystem] = useState(null);
  const [selectedName, setSelectedName] = useState(null);
  const [serverError, setServerError] = useState("");
  const [scalingMsg, setScalingMsg] = useState("");

  useEffect(() => {
    let cancelled = false;
    const fetchFleet = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/fleet`);
        const data = await res.json();
        if (cancelled) return;
        setFleet(data);
        setServerError("");
        // Prefer a running bot for the initial drill-down; retain explicit user selection.
        setSelectedName((current) => current ?? (
          data.bots?.find((bot) => bot.runtime_state === 'active')?.bot
          || data.bots?.[0]?.bot
          || null
        ));
      } catch {
        if (!cancelled) setServerError("Dashboard server unreachable");
      }
    };
    fetchFleet();
    const interval = setInterval(fetchFleet, 4000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchServer = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/server`);
        const data = await res.json();
        if (!cancelled) setMcServer(data);
      } catch {
        if (!cancelled) setMcServer({ online: false, error: 'Dashboard server unreachable' });
      }
    };
    fetchServer();
    const interval = setInterval(fetchServer, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchSystem = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/system`);
        const data = await res.json();
        if (!cancelled) setSystem(data);
      } catch {}
    };
    fetchSystem();
    const interval = setInterval(fetchSystem, 4000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const handleScaleFleet = async (targetCount) => {
    setScalingMsg(`Scaling fleet to ${targetCount} bots…`);
    try {
      const res = await fetch(`${API_BASE}/api/scale`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "scale_fleet", target_count: targetCount }),
      });
      const data = await res.json();
      setScalingMsg(data.message || "Done");
      setTimeout(() => setScalingMsg(""), 5000);
    } catch {
      setScalingMsg("Network error scaling fleet");
    }
  };

  const handleToggleBot = async (bot) => {
    const botNum = parseInt(bot.bot.replace(/\D/g, ''));
    const action = bot.runtime_state === "active" ? "stop_bot" : "start_bot";
    setScalingMsg(`${action === "start_bot" ? "Starting" : "Stopping"} ${bot.bot}…`);
    try {
      const res = await fetch(`${API_BASE}/api/scale`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, bot_number: botNum }),
      });
      const data = await res.json();
      setScalingMsg(data.message || "Done");
      setTimeout(() => setScalingMsg(""), 5000);
    } catch {
      setScalingMsg(`Network error toggling ${bot.bot}`);
    }
  };

  const handleUpgradeBot = async (bot) => {
    const botNum = parseInt(bot.bot.replace(/\D/g, ''));
    setScalingMsg(`Upgrading Bridge JAR for ${bot.bot}…`);
    try {
      const res = await fetch(`${API_BASE}/api/upgrade_bridge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bot_number: botNum }),
      });
      const data = await res.json();
      setScalingMsg(data.message || "Done");
      setTimeout(() => setScalingMsg(""), 6000);
    } catch {
      setScalingMsg(`Network error upgrading ${bot.bot}`);
    }
  };

  const handleUpgradeAll = async () => {
    setScalingMsg("Rolling out Bridge JAR upgrade to ALL bots…");
    try {
      const res = await fetch(`${API_BASE}/api/upgrade_bridge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bot_number: "all" }),
      });
      const data = await res.json();
      setScalingMsg(data.message || "Done");
      setTimeout(() => setScalingMsg(""), 8000);
    } catch {
      setScalingMsg("Network error upgrading all bots");
    }
  };

  const selectedBot = fleet?.bots?.find(b => b.bot === selectedName) || null;

  return (
    <div className="dashboard-root">
      <header className="dashboard-navbar card">
        <div className="navbar-brand">
          <span className="brand-glow">☄️ ANTIGRAVITY</span>
          <span className="brand-sub">MC Fleet Automation</span>
        </div>
        <div className="navbar-status">
          <div className="status-connection">
            <span className={`status-dot ${fleet && !serverError ? 'active' : ''}`} />
            <span className="status-text">
              {serverError ? 'DASHBOARD OFFLINE' : (fleet ? `${fleet.healthy_heartbeats}/${fleet.fleet_size} BOTS HEALTHY` : 'LOADING…')}
            </span>
          </div>
          <div className="status-port-info">
            {fleet?.generated_at && <>Updated <span className="neon-value">{fleet.generated_at.split('T')[1] || fleet.generated_at}</span></>}
          </div>
        </div>
      </header>

      <SystemOverview system={system} fleet={fleet} onScaleFleet={handleScaleFleet} onUpgradeAll={handleUpgradeAll} scalingMsg={scalingMsg} />

      <ServerOverview server={mcServer} />

      <FleetOverview fleet={fleet} selectedName={selectedName} onSelect={(b) => setSelectedName(b.bot)} onToggleBot={handleToggleBot} onUpgradeBot={handleUpgradeBot} />

      {selectedBot
        ? <BotDetail bot={selectedBot} />
        : <div className="card telemetry-fallback" style={{ margin: '0 1rem' }}>Select a bot above to inspect it live.</div>}
    </div>
  );
}

export default App;
