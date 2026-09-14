#!/usr/bin/env python3
"""Add missing hoverTitle variable to AgentNode component"""
import os

os.chdir('i:/AI-LAB/hsdeep-core-ai/frontend')

filepath = 'src/components/dashboard/CommandCenterTopology.tsx'

with open(filepath, 'rb') as f:
    content = f.read()

# The file uses CRLF line endings. Find the pattern:
# ...toUpperCase()};\r\n\r\n  /* Hover tooltip
# Insert hoverTitle definition between ariaLabel and the comment

# Pattern to find: end of ariaLabel assignment followed by blank line and comment
old_bytes = b".toUpperCase()}`;\r\n\r\n  /* Hover tooltip"

# Replacement: add hoverTitle definition before the comment
new_bytes = (b".toUpperCase()}`;\r\n\r\n"
             b"  // HTML title attribute for hover tooltip (matches ariaLabel for accessibility)\r\n"
             b"  const hoverTitle = ariaLabel;\r\n\r\n"
             b"  /* Hover tooltip")

if old_bytes in content:
    new_content = content.replace(old_bytes, new_bytes, 1)
    with open(filepath, 'wb') as f:
        f.write(new_content)
    print("SUCCESS: Added hoverTitle definition after ariaLabel")
else:
    print("ERROR: Pattern not found")
    # Debug: show what we're looking for
    idx = content.find(b"ariaLabel")
    if idx != -1:
        print("\nContext around ariaLabel (hex dump):")
        chunk = content[idx:idx+250]
        for i in range(0, len(chunk), 16):
            hex_part = ' '.join(f'{b:02x}' for b in chunk[i:i+16])
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk[i:i+16])
            print(f"{i+idx:04x}: {hex_part:<48} {ascii_part}")
    else:
        print("ariaLabel not found in file")