// Cross-package import — used by tests/test_scip.py::test_cross_package_imports_resolve
// to prove _run_per_package's global sym_def_path resolves the edge.
import { greet } from "b";

export function shout(name: string): string {
  return greet(name).toUpperCase();
}
