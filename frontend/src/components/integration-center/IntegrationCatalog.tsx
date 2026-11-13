"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getIntegrationCatalog, type CatalogEntry, type CatalogKind } from "@/lib/integrationCenter";
import { Banner, DataTable, Panel, Pill, SearchBox, Tabs, titleCaseWords, type Column, type Tone } from "@/components/ui/PageKit";

/**
 * "Available integrations" tab of the Integration Center (P12 slice 2c):
 * every integration type the platform can connect, from the collector, ITSM
 * and action-provider registries, with what it needs and how many this
 * tenant has configured.
 */

type KindFilter = "all" | CatalogKind;

const KIND_LABEL: Record<CatalogKind, string> = { collector: "Collect", itsm: "Tickets", action: "Actions" };

const RISK_TONE: Record<string, Tone> = {
  READ_ONLY: "good", LOW_RISK: "info", CHANGE_REQUIRING_APPROVAL: "warn", HIGH_RISK: "bad", DESTRUCTIVE: "bad",
};

function readOnlyCount(e: CatalogEntry) {
  return e.actions.filter((a) => a.classification === "READ_ONLY").length;
}

const COLUMNS: Column<CatalogEntry>[] = [
  {
    key: "name", header: "Integration", sortValue: (e) => e.display_name,
    render: (e) => (
      <div>
        <span className="pk-cell-main">{e.display_name}</span>
        <span className="pk-cell-sub">{e.type} · {titleCaseWords(e.category)}</span>
      </div>
    ),
  },
  { key: "kind", header: "Does", sortValue: (e) => e.kind, render: (e) => KIND_LABEL[e.kind] },
  {
    key: "status", header: "Status", sortValue: (e) => (e.implemented ? 1 : 0),
    render: (e) => (e.implemented ? <Pill tone="good">Available</Pill> : <Pill tone="neutral">Planned</Pill>),
  },
  {
    key: "features", header: "Provides",
    render: (e) => {
      if (e.kind === "action") {
        const ro = readOnlyCount(e);
        return (
          <span title={e.actions.map((a) => `${a.action} (${a.classification})`).join("\n")}>
            {e.actions.length} actions · {ro} read-only
            {e.actions.length > ro && <> · <Pill tone={RISK_TONE[e.actions.find((a) => a.classification !== "READ_ONLY")!.classification] ?? "warn"} dot={false}>needs approval</Pill></>}
          </span>
        );
      }
      if (e.kind === "collector") {
        return e.features.length ? e.features.join(", ") : <span className="pk-muted">Inventory and metrics</span>;
      }
      return <span className="pk-truncate" title={e.features.join(", ")}>{e.features.map(titleCaseWords).join(", ")}</span>;
    },
  },
  {
    key: "config", header: "Needs",
    render: (e) =>
      e.required_config.length ? (
        <span className="pk-truncate" title={e.required_config.join("\n")}>{e.required_config.join(" · ")}</span>
      ) : (
        <span className="pk-muted">—</span>
      ),
  },
  {
    key: "configured", header: "Configured", numeric: true, sortValue: (e) => e.configured ?? -1,
    render: (e) =>
      e.configured === null ? <span className="pk-muted" title="Runs on devices of configured sources">On devices</span>
        : e.configured > 0 ? String(e.configured) : <span className="pk-muted">0</span>,
  },
];

export function IntegrationCatalog() {
  const q = useQuery({ queryKey: ["integration-center", "catalog"], queryFn: getIntegrationCatalog, staleTime: 60_000 });
  const [kind, setKind] = useState<KindFilter>("all");
  const [search, setSearch] = useState("");
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const s = q.data?.summary;

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return items.filter((e) => {
      if (kind !== "all" && e.kind !== kind) return false;
      if (!needle) return true;
      return [e.display_name, e.type, e.category, ...e.features].some((v) => v.toLowerCase().includes(needle));
    });
  }, [items, kind, search]);

  return (
    <>
      {q.isError && <Banner tone="bad">The integration catalogue could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}
      <Panel
        title="Available integrations"
        info="Everything the platform can connect: collectors read data, ITSM providers exchange tickets, action providers run policy-checked actions on devices. Planned entries are registered but have no implementation yet."
        aside={s ? `${s.implemented} available · ${s.configured_types} in use` : undefined}
        state={q.isLoading ? "loading" : "ready"}
        skeletonHeight={320}
      >
        <div className="pk-toolbar" style={{ marginBottom: 10 }}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search name, type, category" label="Search available integrations" />
          <Tabs<KindFilter>
            label="Integration kind"
            value={kind}
            onChange={setKind}
            tabs={[
              { key: "all", label: "All", count: s?.total },
              { key: "collector", label: "Collect", count: s?.collectors },
              { key: "itsm", label: "Tickets", count: s?.itsm },
              { key: "action", label: "Actions", count: s?.actions },
            ]}
          />
        </div>
        <DataTable
          caption="Available integrations"
          columns={COLUMNS}
          rows={visible}
          rowKey={(e) => `${e.kind}:${e.type}`}
          emptyText="Nothing matches the current filter."
          maxHeight="max(480px, calc(100vh - 320px))"
        />
      </Panel>
    </>
  );
}
