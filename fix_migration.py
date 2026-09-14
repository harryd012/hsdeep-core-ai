#!/usr/bin/env python
"""Fix the migration file."""
import re

path = r'i:\AI-LAB\hsdeep-core-ai\backend\alembic\versions\a1b2c3d4e5f6_add_integrations_normalized_events_topology.py'

with open(path, 'r') as f:
    content = f.read()

# Fix the indentation issue
old_line = '                    op.create_index("ix_resource_relationships_status", "resource_relationships", ["status"])'
new_line = '    op.create_index("ix_resource_relationships_status", "resource_relationships", ["status"])'
content = content.replace(old_line, new_line)

with open(path, 'w') as f:
    f.write(content)

print('Migration indentation fixed')
