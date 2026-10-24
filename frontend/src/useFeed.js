import { useEffect, useRef, useState } from "react";

const byId = (list) => Object.fromEntries(list.map((x) => [x.id, x]));

export function useFeed({ onEmergency, onUnauthorized } = {}) {
  const [cameras, setCameras] = useState({});
  const [incidents, setIncidents] = useState({});
  const [agents, setAgents] = useState([]);
  const [online, setOnline] = useState(false);
  const cb = useRef({});
  cb.current = { onEmergency, onUnauthorized };

  useEffect(() => {
    let ws, timer, stopped = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => setOnline(true);
      ws.onclose = (e) => {
        setOnline(false);
        if (e.code === 4401) return cb.current.onUnauthorized?.();
        if (!stopped) timer = setTimeout(connect, 1500);
      };
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "snapshot") {
          setCameras(byId(m.cameras || []));
          setIncidents(byId(m.incidents || []));
          if (m.agents) setAgents(m.agents);
        } else if (m.type === "camera_update") {
          setCameras((c) => ({ ...c, [m.camera.id]: { ...m.camera } }));
        } else if (m.type === "incident_new" || m.type === "incident_update" || m.type === "emergency_confirmed") {
          setIncidents((i) => ({ ...i, [m.incident.id]: { ...m.incident } }));
          if (m.type === "emergency_confirmed") cb.current.onEmergency?.(m.incident);
        }
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws && ws.close();
    };
  }, []);

  return { cameras, incidents, agents, online };
}
