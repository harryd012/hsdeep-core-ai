"use client";

import Link from "next/link";
import { Suspense, type ReactNode } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import { ApiError, getBackupItem, type BackupJob, type BackupPolicy } from "@/lib/api";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { Props, RPO_LABEL, duration, jobTone } from "../../ui";

/**
 * /infrastructure/backup/items/[id] — one protected item: is it inside its
 * RPO, how many restore points exist and how long they are kept (from its
 * policy), and its recent backup jobs. Everything shown is what the provider
 * reported; anything it did not report reads "Not reported".
 */

const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;

/** "Daily at 02:00 UTC" from the policy's schedule; null when not reported. */
function schedule(p: BackupPolicy): string | null {
  if (!p.schedule_frequency) return null;
  const when = p.schedule_times.length ? ` at ${p.schedule_times.join(", ")} UTC` : "";
  return `${titleCaseWords(p.schedule_frequency)}${when}`;
}

function retentionRows(p: BackupPolicy): [string, ReactNode][] {
  return [
    ["Daily restore points", p.daily_retention_days === null ? null : `Kept ${plural(p.daily_retention_days, "day")}`],
    ["Weekly restore points", p.weekly_retention_weeks === null ? null : `Kept ${plural(p.weekly_retention_weeks, "week")}`],
    ["Monthly restore points", p.monthly_retention_months === null ? null : `Kept ${plural(p.monthly_retention_months, "month")}`],
    ["Yearly restore points", p.yearly_retention_years === null ? null : `Kept ${plural(p.yearly_retention_years, "year")}`],
  ];
}

const jobColumns: Column<BackupJob>[] = [
  { key: "started", header: "Started", sortValue: (j) => j.started_at, render: (j) => <span title={j.started_at ?? undefined}>{j.started_at ? new Date(j.started_at).toLocaleString() : "—"}</span> },
  { key: "operation", header: "Operation", sortValue: (j) => j.operation, render: (j) => j.operation ?? <span className="pk-muted">—</span> },
  { key: "status", header: "Status", sortValue: (j) => j.status, render: (j) => <Pill tone={jobTone(j.status)}>{titleCaseWords(j.status)}</Pill> },
  { key: "duration", header: "Duration", numeric: true, sortValue: (j) => j.duration_seconds, render: (j) => duration(j.duration_seconds) },
  {
    key: "error",
    header: "Error",
    render: (j) =>
      j.error_code ? (
        <span style={{ color: "var(--ecc-bad)" }} title={j.error_message ?? undefined}>
          {j.error_code}
          {j.error_message ? ` · ${j.error_message}` : ""}
        </span>
      ) : (
        <span className="pk-muted">—</span>
      ),
  },
];

function ItemContent() {
  const id = useParams<{ id: string }>()?.id ?? "";
  const q = useQuery({ queryKey: ["backup", "item", id], queryFn: () => getBackupItem(id), enabled: !!id, refetchInterval: 60_000, retry: false });
  const crumbs = (last: string) => [
    { label: "Backup Center", href: "/infrastructure/backup" },
    { label: "Backup Instances", href: "/infrastructure/backup/instances" },
    { label: last },
  ];

  if (q.isError) {
    const status = q.error instanceof ApiError ? q.error.status : 0;
    return (
      <PageShell title="Protected item" icon={DatabaseBackup} crumbs={crumbs(status === 404 ? "Not found" : "Unavailable")}>
        <Banner tone="warn">
          {status === 404
            ? "This backup item does not exist or is not part of your organization."
            : `The backup item could not be loaded${q.error?.message ? `: ${q.error.message}` : "."}`}
        </Banner>
        <p><Link className="ecc-link" href="/infrastructure/backup">Back to Backup</Link></p>
      </PageShell>
    );
  }

  const d = q.data;
  const item = d?.item;
  const rpo = item ? RPO_LABEL[item.rpo_state] : null;
  const failed = d?.jobs.filter((j) => j.status === "failed").length ?? 0;
  const finished = d?.jobs.filter((j) => ["completed", "completed_with_warnings", "failed"].includes(j.status)).length ?? 0;

  return (
    <PageShell
      title={item?.name ?? "Protected item"}
      icon={DatabaseBackup}
      crumbs={crumbs(item?.name ?? "Item")}
      subtitle={item ? [item.workload_type, `vault ${item.vault_name}`].filter(Boolean).join(" · ") : undefined}
      meta={rpo ? <Pill tone={rpo.tone}>{rpo.text}</Pill> : undefined}
    >
      {item?.last_backup_status === "failed" && <Banner tone="bad">The most recent backup of this item failed. See the job history below for the error.</Banner>}

      <StatStrip>
        <StatTile label="Last backup" info="When the provider last finished a backup of this item, and its verdict." value={item ? (item.last_backup_at ? `${ago(item.last_backup_at)}` : "Never") : "—"} sub={item?.last_backup_status ? titleCaseWords(item.last_backup_status) : undefined} loading={q.isLoading} />
        <StatTile label="Restore points" info="Restore points the vault holds for this item, and the oldest one." value={item?.recovery_point_count !== null && item?.recovery_point_count !== undefined ? item.recovery_point_count.toLocaleString("en-US") : "—"} naText="Not reported" sub={item?.oldest_recovery_point_at ? `oldest ${new Date(item.oldest_recovery_point_at).toLocaleDateString()}` : undefined} loading={q.isLoading} />
        <StatTile label="Daily retention" info="How long daily restore points are kept, from the item's policy." value={d?.policy?.daily_retention_days !== null && d?.policy?.daily_retention_days !== undefined ? plural(d.policy.daily_retention_days, "day") : "—"} naText="Not reported" loading={q.isLoading} />
        <StatTile label="Failed jobs" info="Failed jobs among the recent jobs listed below." badge="recent" value={d ? String(failed) : "—"} alarm={failed > 0} sub={d ? `of ${finished} finished` : undefined} loading={q.isLoading} />
      </StatStrip>

      <div className="pk-grid">
        <Panel title="Protection" state={item ? "ready" : "loading"} className="pk-col-6" skeletonHeight={220}>
          {item && (
            <Props
              rows={[
                ["Protection state", titleCaseWords(item.protection_state) || null],
                ["Health", titleCaseWords(item.health_status) || null],
                ["Last backup", item.last_backup_at ? new Date(item.last_backup_at).toLocaleString() : "Never"],
                ["Oldest restore point", item.oldest_recovery_point_at ? new Date(item.oldest_recovery_point_at).toLocaleString() : null],
                ["Protected resource", item.device_id ? <Link className="ecc-link" href={`/infrastructure/hosts/${item.device_id}`}>Open host</Link> : <span className="pk-muted">Not in inventory</span>],
                ["Resource ID", item.source_resource_id ? <span style={{ wordBreak: "break-all" }}>{item.source_resource_id}</span> : null],
              ]}
            />
          )}
        </Panel>
        <Panel
          title={d?.policy ? `Policy · ${d.policy.name}` : "Policy"}
          info="When the item is backed up and how long each tier of restore points is kept, as the vault's policy defines it."
          state={!d ? "loading" : d.policy ? "ready" : "empty"}
          emptyText={item?.policy_name ? `Policy “${item.policy_name}” was not reported by the vault.` : "No policy is assigned to this item."}
          className="pk-col-6"
          skeletonHeight={220}
        >
          {d?.policy && <Props rows={[["Schedule", schedule(d.policy)], ...retentionRows(d.policy), ["Items using this policy", d.policy.protected_items_count]]} />}
        </Panel>
        <Panel title="Backup jobs" aside={d && d.jobs.length >= 50 ? "Latest 50" : undefined} info="Jobs the vault reported for this item, newest first." state={d ? "ready" : "loading"} className="pk-col-12" skeletonHeight={240}>
          {d && <DataTable caption="Backup jobs for this item" columns={jobColumns} rows={d.jobs} rowKey={(j) => j.id} defaultSort={{ key: "started", dir: "desc" }} maxHeight={420} emptyText="No job reported for this item yet." />}
        </Panel>
      </div>
    </PageShell>
  );
}

export default function BackupItemPage() {
  return (
    <Suspense fallback={<PageShell title="Protected item"><Panel title="Protected item" state="loading" skeletonHeight={420} /></PageShell>}>
      <ItemContent />
    </Suspense>
  );
}
