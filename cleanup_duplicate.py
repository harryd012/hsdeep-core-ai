#!/usr/bin/env python3
"""Remove duplicate aiText declaration"""
with open('I:/AI-LAB/hsdeep-core-ai/frontend/src/app/(dashboard)/ai-copilot/page.tsx', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and remove the duplicate
new_lines = []
seen_aiText = False
removed = 0

for line in lines:
    stripped = line.strip()
    if stripped.startswith('const aiText ='):
        if seen_aiText:
            # This is a duplicate, skip it
            removed += 1
            continue
        seen_aiText = True
    new_lines.append(line)

if removed > 0:
    with open('I:/AI-LAB/hsdeep-core-ai/frontend/src/app/(dashboard)/ai-copilot/page.tsx', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f'Removed {removed} duplicate aiText declaration(s)')
else:
    print('No duplicate found')