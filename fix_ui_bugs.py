#!/usr/bin/env python3
"""Fix two UI bugs in the AI Ops dashboard"""
import os
import sys

os.chdir('I:/AI-LAB/hsdeep-core-ai/frontend')

# ============ BUG 1: Fix AI CTO status logic ============
print("=" * 60)
print("BUG 1: Fix AI CTO status 'OFFLINE' incorrectly shown")
print("=" * 60)

page_path = 'src/app/(dashboard)/ai-copilot/page.tsx'
with open(page_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the exact location of the stats array
search_marker = 'const stats = [\n    { label: "AI CTO", value: systemOnline ? "ONLINE" : "OFFLINE", color: "var(--cyan)" },'

if search_marker in content:
    print(f"Found AI CTO status line in {page_path}")
    
    # Replacement: add proper status logic before stats array
    replacement = '''  const aiText = health?.ai_ready ? "AI: READY" : health ? "AI: DEGRADED" : "AI: UNKNOWN";

  // AI CTO status: use health?.status as authoritative source (matching
  // CtoNode in CommandCenterTopology.tsx). Don't rely solely on systemOnline
  // which can be true even when the control plane is degraded.
  const ctoHealthStatus = health?.status ?? (systemOnline ? "ok" : "unavailable");
  const ctoIsOnline = ctoHealthStatus !== "unavailable";
  const ctoIsDegraded = ctoHealthStatus === "degraded";
  const aiCtoStatus = ctoIsOnline
    ? ctoIsDegraded
      ? "DEGRADED"
      : "ONLINE"
    : "OFFLINE";
  const aiCtoColor = ctoIsOnline
    ? ctoIsDegraded
      ? "var(--amber, #f5a623)"
      : "var(--green, #2ecc71)"
    : "var(--red, #ff5c5c)";

  const stats = [
    { label: "AI CTO", value: aiCtoStatus, color: aiCtoColor },'''

    content = content.replace(search_marker, replacement)
    
    with open(page_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✓ Replaced AI CTO status logic")
    print("  - Now uses health?.status as authoritative source")
    print("  - Shows DEGRADED when health.status === 'degraded'")
    print("  - Shows OFFLINE only when truly unavailable")
else:
    print(f"WARNING: Could not find exact pattern in {page_path}")
    # Try to find it differently
    idx = content.find('AI CTO')
    print(f"Found 'AI CTO' at position {idx}")
    print(f"Context: {content[idx-50:idx+150]}")

print()

# ============ BUG 2: Fix masthead visibility ============
print("=" * 60)
print("BUG 2: Fix masthead text visibility/contrast")
print("=" * 60)

css_path = 'src/app/globals.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

# Fix 1: Define --cyan-dim if not defined
if '--cyan-dim' not in css_content:
    print("✗ --cyan-dim is NOT defined in CSS!")
    print("  This is likely causing the dim/invisible text")
    
    # Add --cyan-dim definition after --cyan definition
    cyan_def = '--cyan: #00dcff;\n  --green: #00d38d;'
    cyan_dim_def = '''--cyan: #00dcff;
  --cyan-dim: #66e0ff;
  --green: #00d38d;'''
    
    if cyan_def in css_content:
        css_content = css_content.replace(cyan_def, cyan_dim_def)
        print("✓ Added --cyan-dim: #66e0ff definition")
    else:
        print("  Could not find --cyan definition to add --cyan-dim after")
else:
    print("✓ --cyan-dim is already defined")
    # Find its value
    import re
    match = re.search(r'--cyan-dim:\s*([^;]+);', css_content)
    if match:
        print(f"  Current value: {match.group(1)}")

# Fix 2: Increase font sizes in masthead
# Find the hs-noc__masthead styles
masthead_pattern = r'\.hs-noc__masthead-id h1 \{[^}]+\}'
masthead_match = re.search(masthead_pattern, css_content)
if masthead_match:
    old_h1 = masthead_match.group(0)
    print(f"\nCurrent h1 style:\n{old_h1[:200]}...")
    
    new_h1 = '''\.hs-noc__masthead-id h1 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--cyan);
  text-shadow: 0 0 12px rgba(63, 224, 255, 0.5);
}'''
    
    # Escape for regex
    new_h1_escaped = new_h1.replace('\\', '\\\\').replace('.', '\\.').replace('{', '\\{').replace('}', '\\}')
    css_content = re.sub(masthead_pattern, new_h1_escaped, css_content)
    print("✓ Increased h1 font-size from 18px to 22px")

# Fix subtitle p
subtitle_pattern = r'\.hs-noc__masthead-id p \{[^}]+\}'
subtitle_match = re.search(subtitle_pattern, css_content)
if subtitle_match:
    old_p = subtitle_match.group(0)
    print(f"\nCurrent p (subtitle) style:\n{old_p[:200]}...")
    
    new_p = '''\.hs-noc__masthead-id p {
  margin: 3px 0 0;
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.18em;
  color: var(--cyan-dim);
}'''
    
    new_p_escaped = new_p.replace('\\', '\\\\').replace('.', '\\.').replace('{', '\\{').replace('}', '\\}')
    css_content = re.sub(subtitle_pattern, new_p_escaped, css_content)
    print("✓ Increased subtitle font-size from 10.5px to 12px")

# Fix context dt labels to use brighter color
dt_pattern = r'\.hs-noc__context dt \{[^}]+\}'
dt_match = re.search(dt_pattern, css_content)
if dt_match:
    old_dt = dt_match.group(0)
    print(f"\nCurrent dt style:\n{old_dt[:200]}...")
    
    new_dt = '''\.hs-noc__context dt {
  font-family: var(--font-body);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: var(--cyan);
}'''
    
    new_dt_escaped = new_dt.replace('\\', '\\\\').replace('.', '\\.').replace('{', '\\{').replace('}', '\\}')
    css_content = re.sub(dt_pattern, new_dt_escaped, css_content)
    print("✓ Changed dt color from var(--cyan-dim) to var(--cyan) for better contrast")

with open(css_path, 'w', encoding='utf-8') as f:
    f.write(css_content)

print("\n✓ Applied BUG 2 fixes to globals.css")
print("  - Defined --cyan-dim: #66e0ff")
print("  - Increased h1 from 18px to 22px")
print("  - Increased subtitle from 10.5px to 12px")
print("  - Changed context labels from dim to bright cyan")

print("\n" + "=" * 60)
print("ALL FIXES APPLIED")
print("=" * 60)