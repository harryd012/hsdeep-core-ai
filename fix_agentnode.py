import re

p = r"I:\AI-LAB\hsdeep-core-ai\frontend\src\components\dashboard\CommandCenterTopology.tsx"
s = open(p, encoding="utf-8").read()

# The file contains literal \r\n as 4-char text sequences (not real CRLF).
# In Python source, to represent those 4 chars we need: \\r\\n (each backslash doubled).
old = (
    "  const workloadPct =\\r\\n"
    "    agent.max_workload > 0\\r\\n"
    "      ? Math.round((agent.current_workload / agent.max_workload) * 100)\\r\\n"
    "      : 0;\\r\\n"
    "  const hoverTitle = [\\r\\n"
    "    agent.name,\\r\\n"
    "    sub,\\r\\n"
    "    task\\r\\n"
    "      ? `TASK ${task.task_number} · ${task.status.toUpperCase()} · ${task.priority}`\\r\\n"
    "      : `STATUS · ${status.toUpperCase()}`,\\r\\n"
    "    `Workload: ${workloadPct}% (${agent.current_workload}/${agent.max_workload})`,\\r\\n"
    "    `Success rate: ${Math.round(agent.success_rate ?? 0)}%`,\\r\\n"
    "  ].join(\"\\n\");\\r\\n"
)

# sanity check
if old not in s:
    print("OLD BLOCK NOT FOUND")
    idx = s.find("const workloadPct")
    print(repr(s[idx-20:idx+550]))
    exit(1)

new = (
    "  const workloadPct =\\r\\n"
    "    agent.max_workload > 0\\r\\n"
    "      ? Math.round((agent.current_workload / agent.max_workload) * 100)\\r\\n"
    "      : 0;\\r\\n"
    "\\r\\n"
    "  const [tip, setTip] = useState<{ x: number; y: number; lines: React.ReactNode[] } | null>(null);\\r\\n"
    "\\r\\n"
    "  const showTip = (e: React.MouseEvent<HTMLDivElement>) => {\\r\\n"
    "    const lines: React.ReactNode[] = [\\r\\n"
    "      <span key='name' style={{ fontWeight: 700, color: 'var(--cyan)' }}>{agent.name}</span>,\\r\\n"
    "      <span key='sub' style={{ color: 'var(--muted)', fontSize: 11 }}>{sub}</span>,\\r\\n"
    "      task\\r\\n"
    "        ? [\\r\\n"
    "            <span key='taskid' style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>\\r\\n"
    "              {task.task_number}\\r\\n"
    "            </span>,\\r\\n"
    "            <span key='status' style={{ color: statusColor(task.status), fontWeight: 700 }}>\\r\\n"
    "              {task.status.toUpperCase()}\\r\\n"
    "            </span>,\\r\\n"
    "            task.title\\r\\n"
    "              ? <span key='desc' style={{ color: 'var(--text-mid)', fontSize: 11 }}>{task.title}</span>\\r\\n"
    "              : null,\\r\\n"
    "          ]\\r\\n"
    "        : [\\r\\n"
    "            <span key='idle' style={{ color: 'var(--green, #2ecc71)', fontWeight: 700 }}>\\r\\n"
    "              NO ACTIVE TASK\\r\\n"
    "            </span>,\\r\\n"
    "            <span key='avail' style={{ color: 'var(--text-mid)', fontSize: 11 }}>\\r\\n"
    "              {agent.availability.toUpperCase()}\\r\\n"
    "            </span>,\\r\\n"
    "          ],\\r\\n"
    "      <span key='workload' style={{ color: 'var(--muted)', fontSize: 11 }}>\\r\\n"
    "        Workload: {workloadPct}% ({agent.current_workload}/{agent.max_workload})\\r\\n"
    "      </span>,\\r\\n"
    "      <span key='success' style={{ color: 'var(--muted)', fontSize: 11 }}>\\r\\n"
    "        Success rate: {Math.round(agent.success_rate ?? 0)}%\\r\\n"
    "      </span>,\\r\\n"
    "    ].filter(Boolean);\\r\\n"
    "    setTip({ x: e.clientX + 14, y: e.clientY + 14, lines });\\r\\n"
    "  };\\r\\n"
    "\\r\\n"
    "  const moveTip = (e: React.MouseEvent<HTMLDivElement>) => {\\r\\n"
    "    if (!tip) return;\\r\\n"
    "    setTip({ ...tip, x: e.clientX + 14, y: e.clientY + 14 });\\r\\n"
    "  };\\r\\n"
    "\\r\\n"
    "  const hideTip = () => setTip(null);"
)

s2 = s.replace(old, new, 1)
open(p, "w", encoding="utf-8").write(s2)
print("DONE — replaced tooltip block")
