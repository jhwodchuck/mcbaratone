import React, { useState, useEffect, useRef } from 'react';
import './App.css';

// Base API URL is relative to where the dashboard server is hosted
const API_BASE = "";

function App() {
  const [state, setState] = useState(null);
  const [inventory, setInventory] = useState([]);
  const [logs, setLogs] = useState([]);
  const [connected, setConnected] = useState(false);
  const [bridgeInfo, setBridgeInfo] = useState({ host: "localhost", port: 5555 });
  const [error, setError] = useState("");
  
  // Terminal state
  const [command, setCommand] = useState("");
  const [terminalHistory, setTerminalHistory] = useState([
    { type: 'system', text: 'Welcome to mcbaratone Autonomous Operator Terminal.' },
    { type: 'system', text: 'Ready to receive directives.' }
  ]);
  
  // Crafting state
  const [craftingCount, setCraftingCount] = useState(1);
  const [craftingStatus, setCraftingStatus] = useState("");

  const terminalEndRef = useRef(null);
  const logsEndRef = useRef(null);

  // Poll state and inventory
  useEffect(() => {
    const fetchData = async () => {
      try {
        const stateRes = await fetch(`${API_BASE}/api/state`);
        const stateData = await stateRes.json();
        
        setConnected(stateData.connected);
        if (stateData.connected) {
          setState(stateData.state);
          setBridgeInfo({ host: stateData.bridge_host, port: stateData.bridge_port });
          setError("");
        } else {
          setError(stateData.error || "Disconnected from Minecraft Bridge");
          setState(null);
        }
      } catch (err) {
        setConnected(false);
        setError("Dashboard server unreachable");
        setState(null);
      }

      // Fetch inventory if connected
      if (connected) {
        try {
          const invRes = await fetch(`${API_BASE}/api/inventory`);
          const invData = await invRes.json();
          if (invData.connected && invData.inventory) {
            setInventory(invData.inventory.slots || []);
          }
        } catch (err) {
          console.error("Error fetching inventory:", err);
        }
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 2000);
    return () => clearInterval(interval);
  }, [connected]);

  // Poll logs
  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/logs`);
        const data = await res.json();
        setLogs(data.logs || []);
      } catch (err) {
        console.error("Error fetching logs:", err);
      }
    };

    fetchLogs();
    const interval = setInterval(fetchLogs, 2000);
    return () => clearInterval(interval);
  }, []);

  // Auto-scroll terminal and logs
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [terminalHistory]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const handleSendCommand = async (e) => {
    e.preventDefault();
    if (!command.trim()) return;

    const cmdText = command.trim();
    setTerminalHistory(prev => [...prev, { type: 'input', text: `> ${cmdText}` }]);
    setCommand("");

    try {
      const res = await fetch(`${API_BASE}/api/command`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmdText })
      });
      const data = await res.json();
      if (data.success) {
        setTerminalHistory(prev => [...prev, { type: 'output', text: data.result || "Command executed successfully." }]);
      } else {
        setTerminalHistory(prev => [...prev, { type: 'error', text: `Error: ${data.error}` }]);
      }
    } catch (err) {
      setTerminalHistory(prev => [...prev, { type: 'error', text: `Failed to contact server: ${err.message}` }]);
    }
  };

  const handleCraftItem = async (itemId) => {
    setCraftingStatus(`Crafting ${craftingCount}x ${itemId}...`);
    try {
      const res = await fetch(`${API_BASE}/api/craft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item: itemId, count: craftingCount })
      });
      const data = await res.json();
      if (data.success) {
        setCraftingStatus(`Successfully crafted ${itemId}!`);
        // Add to terminal history
        setTerminalHistory(prev => [...prev, { type: 'system', text: `Manual craft succeeded: ${craftingCount}x ${itemId}` }]);
      } else {
        setCraftingStatus(`Crafting failed: ${data.error || 'Check ingredients'}`);
      }
    } catch (err) {
      setCraftingStatus(`Crafting failed: ${err.message}`);
    }
    setTimeout(() => setCraftingStatus(""), 4000);
  };

  const executeQuickAction = async (cmdText) => {
    setTerminalHistory(prev => [...prev, { type: 'input', text: `[Quick Action] ${cmdText}` }]);
    try {
      const res = await fetch(`${API_BASE}/api/command`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmdText })
      });
      const data = await res.json();
      if (data.success) {
        setTerminalHistory(prev => [...prev, { type: 'output', text: data.result || "Action sent." }]);
      } else {
        setTerminalHistory(prev => [...prev, { type: 'error', text: data.error }]);
      }
    } catch (err) {
      setTerminalHistory(prev => [...prev, { type: 'error', text: err.message }]);
    }
  };

  // Helper to render inventory slots structured (slots 9-35 are storage, 0-8 are hotbar)
  const renderInventoryGrid = () => {
    // Generate empty inventory structure
    const grid = [];
    
    // Rows 0-2: Main Inventory (slots 9 to 35)
    for (let r = 0; r < 3; r++) {
      const row = [];
      for (let c = 0; c < 9; c++) {
        const slotNum = 9 + r * 9 + c;
        const item = inventory.find(i => i.slot === slotNum);
        row.push({ slot: slotNum, item });
      }
      grid.push(row);
    }
    
    // Row 3: Hotbar (slots 0 to 8)
    const hotbar = [];
    for (let c = 0; c < 9; c++) {
      const item = inventory.find(i => i.slot === c);
      hotbar.push({ slot: c, item });
    }
    grid.push(hotbar);

    return (
      <div className="inventory-grid-container">
        <div className="inventory-header">Inventory Visualizer</div>
        <div className="grid-slots-wrapper">
          {grid.map((row, rIdx) => (
            <div key={rIdx} className={`inventory-row ${rIdx === 3 ? 'hotbar-row' : ''}`}>
              {row.map(cell => (
                <div 
                  key={cell.slot} 
                  className={`inventory-slot ${cell.item ? 'has-item' : ''}`}
                  title={cell.item ? `${cell.item.id} (Count: ${cell.item.count})` : `Slot ${cell.slot}`}
                >
                  {cell.item ? (
                    <div className="slot-item-content">
                      <div className="item-emoji">{getItemEmoji(cell.item.id)}</div>
                      <span className="item-count">{cell.item.count}</span>
                      {cell.item.damage > 0 && (
                        <div className="durability-bar">
                          <div 
                            className="durability-fill" 
                            style={{ 
                              width: `${Math.max(0, 100 - (cell.item.damage * 100 / cell.item.max_damage))} %`,
                              backgroundColor: cell.item.damage * 3 > cell.item.max_damage ? 'var(--danger)' : 'var(--success)'
                            }}
                          />
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="slot-number-muted">{cell.slot}</span>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  };

  // Helper emoji map to make inventory items look nice without images
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
    if (name.includes('cobblestone')) return "🪨";
    if (name.includes('air')) return "";
    return "📦"; // Default block package
  };

  return (
    <div className="dashboard-root">
      {/* Top Header */}
      <header className="dashboard-navbar card">
        <div className="navbar-brand">
          <span className="brand-glow">☄️ ANTIGRAVITY</span>
          <span className="brand-sub">MC Fleet Automation</span>
        </div>
        
        <div className="navbar-status">
          <div className="status-connection">
            <span className={`status-dot ${connected ? 'active' : ''}`} />
            <span className="status-text">
              {connected ? 'CONNECTED TO BRIDGE' : 'BRIDGE DISCONNECTED'}
            </span>
          </div>
          <div className="status-port-info">
            Bridge: <span className="neon-value">{bridgeInfo.host}:{bridgeInfo.port}</span>
          </div>
        </div>
      </header>

      {/* Main Content Grid */}
      <main className="dashboard-grid">
        {/* Left Side Panel - Telemetry and Quick Actions */}
        <section className="dashboard-column left-col">
          {/* Telemetry Card */}
          <div className="card telemetry-card">
            <div className="card-header font-glow">Real-Time Telemetry</div>
            
            {state ? (
              <div className="telemetry-stats">
                {/* Health & Hunger */}
                <div className="stat-progress-bar">
                  <div className="bar-labels">
                    <span>HP / Health</span>
                    <span className="neon-value font-red">{state.health ? state.health.toFixed(1) : '20.0'} / 20</span>
                  </div>
                  <div className="bar-container">
                    <div className="bar-fill fill-red" style={{ width: `${(state.health || 20) * 5}%` }} />
                  </div>
                </div>

                <div className="stat-progress-bar">
                  <div className="bar-labels">
                    <span>Hunger</span>
                    <span className="neon-value font-orange">{state.foodLevel || '20'} / 20</span>
                  </div>
                  <div className="bar-container">
                    <div className="bar-fill fill-orange" style={{ width: `${(state.foodLevel || 20) * 5}%` }} />
                  </div>
                </div>

                {/* Grid stats */}
                <div className="telemetry-grid">
                  <div className="grid-item">
                    <span className="item-label">Position X</span>
                    <span className="item-val font-glow">{state.x ? state.x.toFixed(2) : '0.00'}</span>
                  </div>
                  <div className="grid-item">
                    <span className="item-label">Position Y</span>
                    <span className="item-val font-glow">{state.y ? state.y.toFixed(2) : '0.00'}</span>
                  </div>
                  <div className="grid-item">
                    <span className="item-label">Position Z</span>
                    <span className="item-val font-glow">{state.z ? state.z.toFixed(2) : '0.00'}</span>
                  </div>
                  <div className="grid-item">
                    <span className="item-label">Dimension</span>
                    <span className="item-val font-glow uppercase">{state.dimension || 'Overworld'}</span>
                  </div>
                </div>

                {/* Pathing Goal Status */}
                <div className="goal-status-box">
                  <span className="item-label">Active Pathing Goal</span>
                  <div className="goal-content">
                    {state.hasGoal ? (
                      <div className="goal-active font-cyan">
                        🎯 Goto ({state.goalX}, {state.goalY}, {state.goalZ})
                      </div>
                    ) : (
                      <div className="goal-inactive">Idle (No active target)</div>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="telemetry-fallback">
                {error || "Waiting for bot state data..."}
              </div>
            )}
          </div>

          {/* Quick Actions Card */}
          <div className="card actions-card">
            <div className="card-header font-glow">Quick Actions</div>
            <div className="actions-buttons-grid">
              <button 
                onClick={() => executeQuickAction("cancel")} 
                className="btn btn-danger"
                disabled={!connected}
              >
                🛑 Cancel Active Task
              </button>
              <button 
                onClick={() => executeQuickAction("goal clear")} 
                className="btn btn-secondary"
                disabled={!connected}
              >
                🧹 Clear Nav Goal
              </button>
              <button 
                onClick={() => executeQuickAction("pause")} 
                className="btn btn-secondary"
                disabled={!connected}
              >
                ⏸️ Pause Pathfinder
              </button>
              <button 
                onClick={() => executeQuickAction("resume")} 
                className="btn btn-primary"
                disabled={!connected}
              >
                ▶️ Resume Pathfinder
              </button>
            </div>
          </div>
        </section>

        {/* Center Panel - Inventory and Manual Crafting */}
        <section className="dashboard-column center-col">
          <div className="card inventory-card">
            {renderInventoryGrid()}
          </div>

          {/* Recipe Crafting Panel */}
          <div className="card crafting-card">
            <div className="card-header font-glow">Auto-Crafting Engine</div>
            
            <div className="crafting-controls">
              <div className="crafting-count-selector">
                <label>Target Count:</label>
                <input 
                  type="number" 
                  min="1" 
                  max="64" 
                  value={craftingCount}
                  onChange={(e) => setCraftingCount(Math.max(1, parseInt(e.target.value) || 1))}
                />
              </div>

              {craftingStatus && (
                <div className="crafting-status-alert font-cyan">
                  {craftingStatus}
                </div>
              )}
            </div>

            <div className="crafting-recipes-grid">
              <button onClick={() => handleCraftItem("minecraft:oak_planks")} className="btn btn-secondary" disabled={!connected}>
                🪵 Oak Planks (x4)
              </button>
              <button onClick={() => handleCraftItem("minecraft:stick")} className="btn btn-secondary" disabled={!connected}>
                🥢 Sticks (x4)
              </button>
              <button onClick={() => handleCraftItem("minecraft:crafting_table")} className="btn btn-secondary" disabled={!connected}>
                📦 Crafting Table
              </button>
              <button onClick={() => handleCraftItem("minecraft:chest")} className="btn btn-secondary" disabled={!connected}>
                🧳 Chest
              </button>
              <button onClick={() => handleCraftItem("minecraft:furnace")} className="btn btn-secondary" disabled={!connected}>
                🔥 Furnace
              </button>
              <button onClick={() => handleCraftItem("minecraft:shield")} className="btn btn-secondary" disabled={!connected}>
                🛡️ Shield
              </button>
              <button onClick={() => handleCraftItem("minecraft:stone_pickaxe")} className="btn btn-secondary" disabled={!connected}>
                ⛏️ Stone Pickaxe
              </button>
              <button onClick={() => handleCraftItem("minecraft:iron_pickaxe")} className="btn btn-secondary" disabled={!connected}>
                ⛏️ Iron Pickaxe
              </button>
            </div>
          </div>
        </section>

        {/* Right Side Panel - Command Terminal & Streamer Logs */}
        <section className="dashboard-column right-col">
          {/* Terminal Console */}
          <div className="card terminal-card">
            <div className="card-header font-glow">Interactive Command Terminal</div>
            
            <div className="terminal-display">
              {terminalHistory.map((item, idx) => (
                <div key={idx} className={`terminal-line line-${item.type}`}>
                  {item.text}
                </div>
              ))}
              <div ref={terminalEndRef} />
            </div>

            <form onSubmit={handleSendCommand} className="terminal-form">
              <input 
                type="text" 
                placeholder="Type command (e.g. goto 100 64 250, mine iron_ore)..." 
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                disabled={!connected}
              />
              <button type="submit" className="btn btn-primary" disabled={!connected}>
                Send
              </button>
            </form>
          </div>

          {/* Log Streamer */}
          <div className="card logs-card">
            <div className="card-header font-glow">Live Telemetry Logs</div>
            <div className="logs-display">
              {logs.length > 0 ? (
                logs.map((logLine, idx) => (
                  <div key={idx} className="log-line">
                    {logLine}
                  </div>
                ))
              ) : (
                <div className="log-line-muted">No recent logs recorded.</div>
              )}
              <div ref={logsEndRef} />
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
