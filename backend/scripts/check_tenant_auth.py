"""
CI guard: check that every route handler using `tenant_id` derives it from
`Depends(get_tenant_id)` rather than accepting it from the client as a
query parameter or bare path parameter.

This is a simple AST-based scan: it parses every Python file under
``app/api/routes/`` and ``app/services/*/router.py``, finds async/def
functions that declare a parameter named ``tenant_id``, and verifies the
default value is ``Depends(get_tenant_id)`` (or an explicitly allow-listed
exception).

Usage:
    python scripts/check_tenant_auth.py

Exit code: 0 if all routes pass, 1 if any violation is found.

Allow-listed exceptions (each with a justification comment):
  - monitoring.list_source_types: no tenant_id parameter at all (public)
  - servicenow.handle_webhook: external webhook called by ServiceNow
  - auth.*: auth routes use Principal directly (correct pattern)

Add new exceptions sparingly and with a clear justification.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Allow-listed exceptions
# ---------------------------------------------------------------------------
# Format: (module_path, function_name) -> justification
ALLOW_LIST: dict[tuple[str, str], str] = {
    ("monitoring", "list_source_types"): "Public endpoint - no tenant_id parameter",
    ("servicenow", "handle_webhook"): "External webhook called by ServiceNow, not by authenticated users",
    # Endpoints that accept tenant_id as a RESTful path parameter but enforce
    # isolation via a runtime check (tenant_id == session_tenant_id) inside the
    # function body. The AST-based scanner cannot detect runtime verification.
    ("assets", "get_tenant_health"): "Path tenant_id validated against session_tenant_id runtime check",
    ("metering", "get_tenant_usage_summary"): "Path tenant_id validated via _verify_tenant_access runtime check",
    ("metering", "get_tenant_usage_events"): "Path tenant_id validated via _verify_tenant_access runtime check",
}

# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

# The dependency function name we expect tenant_id to be sourced from
EXPECTED_DEPENDENCY = "Depends(get_tenant_id)"


def _is_get_tenant_id_ref(node: ast.AST) -> bool:
    """Check if a node represents a reference to get_tenant_id.

    FastAPI's Depends() takes a function *reference* (not a call), so the
    AST node is a Name or Attribute, not a Call.

    Handles both direct imports (Name) and module-qualified (Attribute) forms:
      - get_tenant_id
      - auth.get_tenant_id
      - app.core.auth.get_tenant_id
    """
    if isinstance(node, ast.Name):
        return node.id == "get_tenant_id"
    if isinstance(node, ast.Attribute):
        return node.attr == "get_tenant_id"
    return False


def _check_function(func_node: ast.FunctionDef | ast.AsyncFunctionDef, file_path: str) -> list[str]:
    """Check a single function for tenant_id parameter violations.

    Returns a list of error messages (empty if clean).
    """
    errors: list[str] = []

    # Check if this function has a 'tenant_id' parameter
    tenant_id_param = None
    for arg in func_node.args.args:
        if arg.arg == "tenant_id":
            tenant_id_param = arg
            break

    # Also check *args, **kwargs (unlikely but exhaustive)
    if tenant_id_param is None:
        for arg in func_node.args.kwonlyargs:
            if arg.arg == "tenant_id":
                tenant_id_param = arg
                break

    if tenant_id_param is None:
        return errors  # No tenant_id parameter - fine

    # Check if the default value is Depends(get_tenant_id)
    # The default value is in func_node.args.defaults or kw_defaults
    # We need to find the matching default for this parameter

    # Count positional parameters before the first default
    num_positional = len(func_node.args.args) - len(func_node.args.defaults)
    param_idx = func_node.args.args.index(tenant_id_param) - num_positional

    if param_idx >= 0 and param_idx < len(func_node.args.defaults):
        default = func_node.args.defaults[param_idx]
        if isinstance(default, ast.Call):
            # Check if it's Depends(get_tenant_id)
            if (
                isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and len(default.args) == 1
                and _is_get_tenant_id_ref(default.args[0])
            ):
                return errors  # Correct pattern
            # Check if it's Depends(...) with something else
            if isinstance(default.func, ast.Name) and default.func.id == "Depends":
                errors.append(
                    f"{file_path}:{func_node.lineno}: "
                    f"tenant_id uses Depends() but not get_tenant_id "
                    f"(found: {ast.unparse(default)})"
                )
                return errors

    # Check if it's a keyword-only argument with default
    if tenant_id_param in func_node.args.kwonlyargs:
        kw_idx = func_node.args.kwonlyargs.index(tenant_id_param)
        if kw_idx < len(func_node.args.kw_defaults) and func_node.args.kw_defaults[kw_idx] is not None:
            default = func_node.args.kw_defaults[kw_idx]
            if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) and default.func.id == "Depends":
                if len(default.args) == 1 and _is_get_tenant_id_ref(default.args[0]):
                    return errors  # Correct pattern

    # Check if it's a bare path parameter (no default at all)
    if param_idx < 0 or param_idx >= len(func_node.args.defaults):
        # Check if the parameter is a positional-only or regular parameter
        # without a default (i.e., it's a required path parameter)
        if tenant_id_param in func_node.args.args:
            # It has no default - it's a path/required parameter
            # Check if it's also in the function body as a Query(...) call
            # or if it's used without verification
            errors.append(
                f"{file_path}:{func_node.lineno}: "
                f"tenant_id is a required path parameter without auth verification "
                f"(should be Depends(get_tenant_id))"
            )
            return errors

    # If we get here, tenant_id has a default that's not Depends(get_tenant_id)
    # Check if it's Query(...)
    default = None
    if param_idx >= 0 and param_idx < len(func_node.args.defaults):
        default = func_node.args.defaults[param_idx]
    elif tenant_id_param in func_node.args.kwonlyargs:
        kw_idx = func_node.args.kwonlyargs.index(tenant_id_param)
        if kw_idx < len(func_node.args.kw_defaults):
            default = func_node.args.kw_defaults[kw_idx]

    if default is not None:
        default_str = ast.unparse(default)
        errors.append(
            f"{file_path}:{func_node.lineno}: "
            f"tenant_id must use Depends(get_tenant_id), got: {default_str}"
        )
    else:
        errors.append(
            f"{file_path}:{func_node.lineno}: "
            f"tenant_id must use Depends(get_tenant_id), got: no default value"
        )

    return errors


def check_file(file_path: Path) -> list[str]:
    """Check a single Python file for tenant_id violations."""
    errors: list[str] = []
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except SyntaxError as exc:
        errors.append(f"{file_path}: syntax error: {exc}")
        return errors

    # Determine the module name for allow-list checking:
    #   app/api/routes/monitoring.py -> "monitoring"
    #   app/services/metering/router.py -> "metering" (parent dir when file is router.py)
    rel_path = file_path.relative_to(Path.cwd())
    if rel_path.stem == "router" and len(rel_path.parts) >= 2:
        module_name = rel_path.parts[-2]  # parent directory
    else:
        module_name = rel_path.stem  # e.g., "monitoring"

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_name = node.name

            # Check allow-list
            if (module_name, func_name) in ALLOW_LIST:
                continue

            # Check if the function has a decorator that's a route decorator
            # (i.e., it's a FastAPI route handler)
            is_route = any(
                isinstance(d, ast.Call) and hasattr(d.func, "attr") and d.func.attr
                in ("get", "post", "put", "patch", "delete", "options")
                for d in node.decorator_list
            )

            if not is_route:
                continue  # Skip non-route functions

            errors.extend(_check_function(node, str(rel_path)))

    return errors


def main() -> int:
    """Run the check. Returns exit code (0 = pass, 1 = fail)."""
    base_dir = Path.cwd()

    # Directories to scan
    scan_dirs = [
        base_dir / "app" / "api" / "routes",
    ]

    # Also scan services that have router.py files
    services_dir = base_dir / "app" / "services"
    if services_dir.exists():
        for subdir in services_dir.iterdir():
            router_file = subdir / "router.py"
            if router_file.exists():
                scan_dirs.append(subdir)

    all_errors: list[str] = []

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        if scan_dir.is_file():
            all_errors.extend(check_file(scan_dir))
        else:
            for py_file in sorted(scan_dir.glob("*.py")):
                if py_file.name == "__init__.py":
                    continue
                all_errors.extend(check_file(py_file))

    if all_errors:
        print("=" * 70)
        print("TENANT ISOLATION VIOLATIONS DETECTED")
        print("=" * 70)
        for error in sorted(all_errors):
            print(f"  ❌  {error}")
        print()
        print("Fix: replace `tenant_id: uuid.UUID = Query(...)` with")
        print("  `tenant_id: uuid.UUID = Depends(get_tenant_id)`")
        print("(and add `from app.core.auth import get_tenant_id`)")
        print()
        print("If the endpoint genuinely cannot use auth (e.g., external webhook),")
        print("add it to the ALLOW_LIST in scripts/check_tenant_auth.py")
        return 1

    print("✅  All routes pass tenant isolation check")
    return 0


if __name__ == "__main__":
    sys.exit(main())