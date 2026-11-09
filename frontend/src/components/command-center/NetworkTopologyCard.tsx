"use client";

/**
 * Network Topology (LLDP) card for the Executive Command Center.
 *
 * The same left-to-right LLDP tree as the Topology page (Checkmk/NVDCT style:
 * port dot and port name at both ends of every link), sized for a dashboard
 * row. It reuses the page's render model, tree layout and canvas, so the
 * dashboard and the page always agree on state, links and ports.
 *
 * Hierarchy "Full" draws every hop; "Flat" folds the tree to the first hop
 * (core and its direct neighbours) for a large estate. "Only problems" keeps
 * alerting/offline devices and the path up to them. Selecting a device opens
 * its Host page; anything else opens the full Topology page.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getLiveTopology } from "@/lib/topologyApi";
import { fitViewport, type Viewport } from "@/lib/topology/layout";
import { DEVICE_STATE_VISUALS, type DeviceState } from "@/lib/topology/status";
import { buildTreeLayout, collapseBeyond, LARGE_TREE_THRESHOLD } from "@/lib/topology/tree";
import { buildRenderModel } from "@/components/topology/liveRenderModel";
import TopologyTreeCanvas from "@/components/topology/TopologyTreeCanvas";
import type { RenderNode } from "@/components/topology/topologyRenderTypes";
import { CardShell, type CardState } from "./primitives";

const POLL_MS = 60_000;
const PROBLEM_STATES: ReadonlySet<DeviceState> = new Set(["WARNING", "OFFLINE"]);
const NO_COLLAPSE: ReadonlySet<string> = new Set();
/** Below this the labels cannot be read; anchor top-left and let it scroll. */
const MIN_FIT_SCALE = 0.5;

type Hierarchy = "full" | "flat";

function fit(points: { x: number; y: number }[], size: { width: number; height: number }): Viewport | null {
  const fitted = fitViewport(points, size, 48);
  if (!fitted || fitted.scale >= MIN_FIT_SCALE) return fitted;
  const minX = Math.min(...points.map((p) => p.x));
  const minY = Math.min(...points.map((p) => p.y));
  return { scale: MIN_FIT_SCALE, offsetX: 40 - minX * MIN_FIT_SCALE, offsetY: 32 - minY * MIN_FIT_SCALE };
}

/** Problem devices plus every device on their uplink path, so a fault stays in context. */
function withUplinks(problems: Set<string>, parentOf: Map<string, string>): Set<string> {
  const keep = new Set(problems);
  for (const id of problems) {
    for (let up = parentOf.get(id); up && !keep.has(up); up = parentOf.get(up)) keep.add(up);
  }
  return keep;
}

export default function NetworkTopologyCard({ siteId }: { siteId: string | null }) {
  const router = useRouter();
  const [hierarchy, setHierarchy] = useState<Hierarchy>("full");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [labelAll, setLabelAll] = useState(false);
  const [collapsedOverride, setCollapsedOverride] = useState<Set<string> | null>(null);
  const [viewportOverride, setViewportOverride] = useState<Viewport | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const frameRef = useRef<HTMLDivElement | null>(null);

  const q = useQuery({
    queryKey: ["dashboard", "topology-live", siteId],
    queryFn: () => getLiveTopology({ siteId }),
    refetchInterval: POLL_MS,
    retry: false,
  });

  useEffect(() => {
    const el = frameRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    measure();
    return () => observer.disconnect();
  }, [q.data]);

  const model = useMemo(() => (q.data ? buildRenderModel(q.data) : null), [q.data]);
  const devices = useMemo(() => (model ? model.nodes.filter((n) => n.kind === "device") : []), [model]);
  const deviceIds = useMemo(() => new Set(devices.map((n) => n.id)), [devices]);
  const edges = useMemo(
    () => (model ? model.edges.filter((e) => deviceIds.has(e.source) && deviceIds.has(e.target)) : []),
    [model, deviceIds]
  );

  // The full tree first: it defines parents, so "only problems" can keep the uplink path.
  const fullTree = useMemo(
    () =>
      buildTreeLayout(
        devices.map((n) => ({ id: n.id, iconKey: n.visual.iconKey })),
        edges.map((e) => ({ id: e.id, source: e.source, target: e.target }))
      ),
    [devices, edges]
  );

  const shown = useMemo(() => {
    if (!onlyProblems) return { devices, edges };
    const parentOf = new Map(fullTree.links.map((l) => [l.child, l.parent]));
    const problems = new Set(devices.filter((n) => PROBLEM_STATES.has(n.state)).map((n) => n.id));
    const keep = withUplinks(problems, parentOf);
    return {
      devices: devices.filter((n) => keep.has(n.id)),
      edges: edges.filter((e) => keep.has(e.source) && keep.has(e.target)),
    };
  }, [onlyProblems, devices, edges, fullTree]);

  const baseTree = useMemo(
    () =>
      onlyProblems
        ? buildTreeLayout(
            shown.devices.map((n) => ({ id: n.id, iconKey: n.visual.iconKey })),
            shown.edges.map((e) => ({ id: e.id, source: e.source, target: e.target }))
          )
        : fullTree,
    [onlyProblems, shown, fullTree]
  );

  const collapsed = useMemo<ReadonlySet<string>>(() => {
    if (collapsedOverride) return collapsedOverride;
    if (hierarchy === "flat") return collapseBeyond(baseTree, 1);
    return baseTree.positions.size > LARGE_TREE_THRESHOLD ? collapseBeyond(baseTree, 2) : NO_COLLAPSE;
  }, [collapsedOverride, hierarchy, baseTree]);

  const tree = useMemo(
    () =>
      collapsed.size
        ? buildTreeLayout(
            shown.devices.map((n) => ({ id: n.id, iconKey: n.visual.iconKey })),
            shown.edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
            { collapsed }
          )
        : baseTree,
    [collapsed, shown, baseTree]
  );

  const nodeMap = useMemo(() => new Map<string, RenderNode>(shown.devices.map((n) => [n.id, n])), [shown.devices]);
  const edgeMap = useMemo(() => new Map(shown.edges.map((e) => [e.id, e])), [shown.edges]);

  const viewport = useMemo(
    () => viewportOverride ?? fit([...tree.positions.values()], size) ?? { scale: 1, offsetX: 40, offsetY: 40 },
    [viewportOverride, tree, size]
  );

  const resetView = () => {
    setCollapsedOverride(null);
    setViewportOverride(null);
  };

  const counts = model?.summary;
  // Only problems ON the tree: unconnected devices are not drawn here.
  const problemCount = devices.filter((n) => fullTree.positions.has(n.id) && PROBLEM_STATES.has(n.state)).length;
  const linked = fullTree.positions.size;
  const unconnected = fullTree.unlinked.length;

  const state: CardState = q.isLoading && !q.data ? "loading" : q.error && !q.data ? "error" : "ready";

  const segmented = (active: boolean) => `ecc-topo__seg${active ? " ecc-topo__seg--on" : ""}`;

  return (
    <CardShell
      title="Network Topology (LLDP)"
      info="Devices linked by recorded LLDP/CDP neighbours, upstream on the left. Each link shows the port at both ends. Select a device to open it."
      aside={
        <Link className="ecc-link" href="/infrastructure-topology/live">
          Open topology →
        </Link>
      }
      state={state}
      onRetry={() => q.refetch()}
      skeletonHeight={360}
      className="ecc-span-full ecc-topo"
    >
      {model && (
        <>
          <div className="ecc-topo__bar" role="toolbar" aria-label="Topology view">
            <span className="ecc-topo__group">
              <span className="ecc-topo__label">Hierarchy</span>
              {(["flat", "full"] as const).map((h) => (
                <button
                  key={h}
                  type="button"
                  className={segmented(hierarchy === h)}
                  aria-pressed={hierarchy === h}
                  onClick={() => {
                    setHierarchy(h);
                    resetView();
                  }}
                >
                  {h === "flat" ? "Flat" : "Full"}
                </button>
              ))}
            </span>
            <span className="ecc-topo__group">
              <span className="ecc-topo__label">Devices</span>
              <button
                type="button"
                className={segmented(!onlyProblems)}
                aria-pressed={!onlyProblems}
                onClick={() => {
                  setOnlyProblems(false);
                  resetView();
                }}
              >
                All
              </button>
              <button
                type="button"
                className={segmented(onlyProblems)}
                aria-pressed={onlyProblems}
                onClick={() => {
                  setOnlyProblems(true);
                  resetView();
                }}
              >
                Only problems{problemCount ? ` (${problemCount})` : ""}
              </button>
            </span>
            <label className="ecc-topo__check">
              <input type="checkbox" checked={labelAll} onChange={(e) => setLabelAll(e.target.checked)} />
              Label all devices
            </label>
            <span className="ecc-topo__stats">
              {(["ONLINE", "WARNING", "OFFLINE", "STALE"] as const).map((s) => {
                const n = s === "ONLINE" ? counts!.online : s === "WARNING" ? counts!.warning : s === "OFFLINE" ? counts!.offline : counts!.stale;
                if (!n) return null;
                return (
                  <span key={s} className="ecc-topo__stat">
                    <i style={{ background: DEVICE_STATE_VISUALS[s].color }} aria-hidden="true" />
                    {n.toLocaleString("en-US")} {DEVICE_STATE_VISUALS[s].label.toLowerCase()}
                  </span>
                );
              })}
              <span className="ecc-topo__stat ecc-topo__muted">
                {counts!.links} link{counts!.links === 1 ? "" : "s"}
              </span>
            </span>
          </div>

          <div ref={frameRef} className="ecc-topo__frame">
            {tree.positions.size === 0 ? (
              <div className="ecc-quiet" role="status">
                {linked === 0 ? (
                  <>
                    <p className="ecc-quiet__title">No LLDP/CDP neighbours recorded yet</p>
                    <p className="ecc-quiet__detail">
                      {devices.length.toLocaleString("en-US")} devices are monitored, but none has a recorded neighbour, so
                      there is no tree to draw. Enable LLDP/CDP on your switches and run discovery.
                    </p>
                    <a className="ecc-quiet__action" href="/infrastructure/discovery">
                      Run discovery
                    </a>
                  </>
                ) : (
                  <>
                    <p className="ecc-quiet__title">No connected device has a problem</p>
                    <p className="ecc-quiet__detail">Every device on the LLDP tree is online or not yet measured.</p>
                  </>
                )}
              </div>
            ) : (
              <TopologyTreeCanvas
                tree={tree}
                nodes={nodeMap}
                edges={edgeMap}
                viewport={viewport}
                onViewportChange={setViewportOverride}
                selectedNodeId={null}
                selectedEdgeId={null}
                onSelectNode={(id) => router.push(`/infrastructure/hosts/${id.replace(/^device:/, "")}`)}
                onSelectEdge={() => router.push("/infrastructure-topology/live")}
                onClearSelection={() => undefined}
                collapsed={collapsed}
                onToggleCollapse={(id) =>
                  setCollapsedOverride(() => {
                    const next = new Set(collapsed);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    return next;
                  })
                }
                labelAll={labelAll}
                highlight={null}
              />
            )}
          </div>

          <p className="ecc-topo__foot">
            {linked.toLocaleString("en-US")} connected device{linked === 1 ? "" : "s"}
            {unconnected ? (
              <>
                {" · "}
                <Link className="ecc-link" href="/infrastructure-topology/live">
                  {unconnected.toLocaleString("en-US")} with no recorded neighbour
                </Link>
              </>
            ) : null}
            {" · scroll to zoom, drag to pan"}
          </p>
        </>
      )}
    </CardShell>
  );
}
