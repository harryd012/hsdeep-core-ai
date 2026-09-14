#!/usr/bin/env python3
"""Fix the CSS file after broken regex replacement"""
import re

with open('I:/AI-LAB/hsdeep-core-ai/frontend/src/app/globals.css', 'r', encoding='utf-8') as f:
    content = f.read()

# The file has literal backslash characters that shouldn't be there
# We need to clean up the malformed CSS sections

# Pattern 1: The h1 style got mangled with escaped dots and braces
# Find and fix the h1 section
broken_h1_pattern = r'\\\.hs-noc__masthead-id h1 \\\{\\n  margin: 0;\\n  font-family: var\(--font-display\);\n  font-size: 22px;\n  font-weight: 700;\n  letter-spacing: 0\\\.14em;\n  color: var\(--cyan\);\n  text-shadow: 0 0 12px rgba\(63, 224, 255, 0\\\.5\);\n\\\}\n\n\\\.hs-noc__masthead-id p \\\{\n  margin: 3px 0 0;\n  font-family: var\(--font-body\);\n  font-size: 12px;\n  font-weight: 600;\n  letter-spacing: 0\\\.18em;\n  color: var\(--cyan-dim\);\n\\\}\n\n\\\.hs-noc__context dt \\\{\n  font-family: var\(--font-body\);\n  font-size: 10px;\n  font-weight: 600;\n  letter-spacing: 0\\\.16em;\n  color: var\(--cyan\);\n\\\}'

# Check if this pattern exists
if re.search(broken_h1_pattern, content):
    print("Found broken escaped CSS section")
    
    # The correct replacement
    correct_css = '''.hs-noc__masthead-id h1 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--cyan);
  text-shadow: 0 0 12px rgba(63, 224, 255, 0.5);
}

.hs-noc__masthead-id p {
  margin: 3px 0 0;
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.18em;
  color: var(--cyan-dim);
}

.hs-noc__context dt {
  font-family: var(--font-body);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: var(--cyan);
}'''
    
    content = re.sub(broken_h1_pattern, correct_css, content)
    print("Replaced with correct CSS")
else:
    print("Broken pattern not found, checking raw content...")
    # Look for any mangled CSS
    idx = content.find('hs-noc__masthead-id')
    if idx != -1:
        print("Found masthead section:")
        print(repr(content[idx:idx+500]))

# Also check for --cyan-dim definition
if '--cyan-dim:' not in content:
    print("--cyan-dim not defined, adding it")
    # Add after --cyan definition
    content = content.replace(
        '--cyan: #00dcff;\n  --green:',
        '--cyan: #00dcff;\n  --cyan-dim: #66e0ff;\n  --green:'
    )
    print("Added --cyan-dim: #66e0ff")
else:
    print("--cyan-dim already defined")
    # Check its value
    match = re.search(r'--cyan-dim:\s*([^;]+);', content)
    if match:
        print(f"  Current value: {match.group(1)}")

with open('I:/AI-LAB/hsdeep-core-ai/frontend/src/app/globals.css', 'w', encoding='utf-8') as f:
    f.write(content)

print("\nCSS file fixed successfully!")

# Final verification
with open('I:/AI-LAB/hsdeep-core-ai/frontend/src/app/globals.css', 'r', encoding='utf-8') as f:
    final = f.read()

print("\n=== Final masthead styles ===")
idx = final.find('.hs-noc__masthead-id h1')
if idx != -1:
    print(final[idx:idx + 400])