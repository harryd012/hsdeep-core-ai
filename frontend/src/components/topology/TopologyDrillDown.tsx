"use client";

/**
 * Topology drill-down (observability plan P6 slice 1): from a device or a link
 * end on the map to what monitors it — the device's sensors and open alerts
 * with links into the host page tabs and sensor pages, and a link end's
 * interface page. Everything is read from the existing per-device and network
 * APIs on demand; nothing is shown that those APIs did not return.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { getDeviceAlerts, getDeviceSensors } from "@/lib/api";
import { getNetworkInterfaces } from "@/lib/network";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The device id behind a topology node id ("<uuid>" or "device:<uuid>"), or null. */
export function deviceIdOf(nodeId: string | null | undefined): string | null {
  if (!nodeId) return null;
  const raw = nodeId.startsWith("device:") ? nodeId.slice("device:".length) : nodeId;
  return UUID.test(raw) ? raw : null;
}

const PROBLEM_ORDER: Record<string, number> = { down: 0, warning: 1 };

function Muted({ children }: { children: ReactNode }) {
  return <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{children}</p>;
}

function linkStyle() {
  return { fontSize: 12, color: "var(--accent, var(--ecc-accent))" } as const;
}

export function DeviceDrillDown({ deviceId }: { deviceId: string }) {
  const sensors = useQuery({ queryKey: ["topology-drill", deviceId, "sensors"], queryFn: () => getDeviceSensors(deviceId), retry: false });
  const alerts = useQuery({ queryKey: ["topology-drill", deviceId, "alerts"], queryFn: () => getDeviceAlerts(deviceId, 50), retry: false });
  const host = `/infrastructure/hosts/${deviceId}`;

  const list = sensors.data ?? [];
  const byStatus = list.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s.status]: (acc[s.status] ?? 0) + 1 }), {});
  const problems = list
    .filter((s) => s.status in PROBLEM_ORDER)
    .sort((a, b) => PROBLEM_ORDER[a.status] - PROBLEM_ORDER[b.status] || a.name.localeCompare(b.name))
    .slice(0, 6);
  const open = (alerts.data ?? []).filter((a) => a.status !== "resolved");

  return (
    <>
      {sensors.isLoading ? (
        <Muted>Loading sensors…</Muted>
      ) : sensors.isError ? (
        <Muted>Sensors could not be loaded.</Muted>
      ) : list.length === 0 ? (
        <Muted>No sensor reports for this device.</Muted>
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginTop: 4 }}>
            <span style={{ color: "var(--muted)" }}>Sensors</span>
            <span>
              {list.length}
              {(byStatus.down || byStatus.warning) ? ` · ${byStatus.down ?? 0} down, ${byStatus.warning ?? 0} warning` : " · none failing"}
            </span>
          </div>
          {problems.map((s) => (
            <div key={s.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, marginTop: 4 }}>
              <Link href={`/monitoring/sensors/${s.id}`} style={linkStyle()}>{s.name}</Link>
              <span style={{ color: s.status === "down" ? "var(--ecc-bad)" : "var(--ecc-warn)" }}>{s.status}</span>
            </div>
          ))}
        </>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginTop: 6 }}>
        <span style={{ color: "var(--muted)" }}>Open alerts</span>
        <span>{alerts.isLoading ? "…" : alerts.isError ? "N/A" : open.length}</span>
      </div>
      {open.slice(0, 3).map((a) => (
        <div key={a.id} style={{ fontSize: 12, marginTop: 4 }}>{a.title}</div>
      ))}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
        <Link href={host} style={linkStyle()}>Open host</Link>
        <Link href={`${host}?tab=sensors`} style={linkStyle()}>Sensors</Link>
        <Link href={`${host}?tab=alerts`} style={linkStyle()}>Alerts</Link>
        <Link href={`${host}?tab=interfaces`} style={linkStyle()}>Interfaces</Link>
        <Link href={`${host}?tab=events`} style={linkStyle()}>Events</Link>
      </div>
    </>
  );
}

/** "Open interface" for one link end, when the device has an interface of that exact name. */
export function InterfaceDrillLink({ deviceId, name }: { deviceId: string | null; name: string | null }) {
  const enabled = !!deviceId && !!name;
  const found = useQuery({
    queryKey: ["topology-drill", deviceId, "iface", name],
    queryFn: () => getNetworkInterfaces({ deviceId: deviceId as string, q: name as string, sort: "name", limit: 20 }),
    enabled,
    retry: false,
  });
  if (!enabled || !found.data) return null;
  const match = found.data.items.find((i) => i.name === name);
  if (!match) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <Link href={`/network/interfaces/${match.id}`} style={linkStyle()}>Open interface history</Link>
    </div>
  );
}
